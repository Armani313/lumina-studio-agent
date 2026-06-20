# Scaling & concurrency — running on several platforms at once

Lumina serves three entry points off **one** Cloud Run service, which in turn shares **one** Vertex
AI quota:

```
aifreelance.shop ─┐
agentalent.ai    ─┼──▶  lumina-marketplace (Cloud Run)  ──▶  Vertex (Veo / Gemini / Imagen)
direct A2A       ─┘         1 uvicorn worker / instance            ONE project quota for all
```

Per instance: a single uvicorn worker; one long-lived background event loop in a daemon thread
([app.py](../marketplace/app.py)) that runs every order; blocking generation calls go to the default
thread pool; image generation is capped by a process-wide `threading.Semaphore(IMAGE_CONCURRENCY=3)`
([generation.py](../lumina/tools/generation.py)).

Two execution paths:
- **Webhook** (shop / agentalent): replies `{accepted}` in <1s, runs the job in the background, then
  POSTs the result to the caller's callback URL.
- **A2A** (`to_a2a`): runs the agent inside the web request; `message/stream` / blocking keeps the
  request open for the whole job.

## The failure modes this addresses

| # | Problem (default config) | Fix |
|---|--------------------------|-----|
| 1 | **CPU throttling kills background jobs.** Default Cloud Run gives CPU only during a request; after `{accepted}` returns there is no request, so the 15-40 min job is starved and never calls back. | `deploy.sh`: `--no-cpu-throttling` + `--min-instances 1` |
| 2 | **A2A breaks at >1 instance.** ADK's default in-memory task store means a `tasks/get` poll routed to another instance returns "task not found"; restarts lose tasks. `stream`/blocking also exceed the 300s default timeout. | Firestore-backed `FirestoreTaskStore` ([a2a_task_store.py](../marketplace/a2a_task_store.py)) + `--timeout 3600` |
| 3 | **Shared Vertex quota, no global limit.** N instances × per-instance caps can exceed the project quota → 429 storms; one platform's spike starves another. | Per-instance `IMAGE_CONCURRENCY` + `MAX_CONCURRENT_JOBS` admission control, and `--max-instances` chosen so `3 × maxScale` stays within quota |
| 4 | **No crash recovery.** A job lives only in the instance's memory; eviction/OOM mid-run loses it with no retry. | **Cloud Tasks** order queue ([cloud_tasks.py](../marketplace/cloud_tasks.py)): orders are persisted and redelivered on crash; falls back to in-process when the queue isn't configured |
| 5 | **OOM from buffering media.** The asset proxy loaded whole videos into RAM. | Streaming asset proxy ([app.py](../marketplace/app.py) `/api/asset`) + `--memory 2Gi` |

## What changed in code (this PR)

- **Admission control** — `MAX_CONCURRENT_JOBS` (default 4) bounds whole pipelines per instance, so a
  burst across platforms queues instead of thrashing the thread pool / RAM / quota. Parked jobs are
  cheap coroutines; Runners and sessions are allocated only once a slot is free.
- **Streaming asset proxy** — `/api/asset` streams from GCS in 256 KiB chunks instead of buffering the
  whole object, removing an OOM vector that would take down every co-resident background job.
- **Durable A2A task store** — `FirestoreTaskStore` persists A2A tasks in Firestore (the DB already
  used for escrow), making the A2A polling contract correct across instances and across restarts.
  Wiring falls back to the in-memory store if anything goes wrong, so it can never block startup.
- **Cloud Tasks order queue** — the marketplace webhook enqueues each order instead of running it in
  an in-process thread; the worker endpoint `/internal/run-order` generates it INSIDE the task's HTTP
  request. The queue persists the order, redelivers it on a crash, and its `--max-concurrent-dispatches`
  is a GLOBAL concurrency cap (real Vertex-quota protection across all instances). Gated by a
  shared-secret header; idempotent via the escrow status check (a redelivery of an already-delivered
  order is skipped, never re-billed). Falls back to the in-process path when `TASKS_QUEUE` /
  `TASKS_SECRET` are unset — so rollout is gradual and local dev is unaffected.

### Env knobs

| Var | Default | Meaning |
|-----|---------|---------|
| `IMAGE_CONCURRENCY` | 3 | max concurrent image generations per instance |
| `MAX_CONCURRENT_JOBS` | 4 | max concurrent full pipelines per instance |
| `FREE_REVISIONS` | 3 | generation-consuming revisions included per order |

## Deploy

Use [`deploy.sh`](../deploy.sh) (not a bare `gcloud run deploy`, which reverts to the unsafe
defaults). Verify the live config after deploy:

```bash
gcloud run services describe lumina-marketplace --region us-central1 \
  --project aifreelance-hackathon \
  --format="yaml(spec.template.spec.timeoutSeconds, spec.template.spec.containerConcurrency, spec.template.metadata.annotations)"
```

Expect `cpu-throttling: "false"`, `minScale: "1"`, `timeoutSeconds: 3600`.

Then enable the durable order queue (one-time):

```bash
./setup_tasks.sh    # creates the lumina-orders queue + sets TASKS_QUEUE / TASKS_LOCATION / TASKS_SECRET
```

Once the queue handles durability you can drop the always-on instance to save the ~$100-140/mo:
`gcloud run services update lumina-marketplace --region us-central1 --min-instances 0` — the queue keeps
work safe while instances are cold and wakes the worker on demand. (Until the queue is enabled, keep
`--min-instances 1`, since the in-process fallback still relies on a warm instance.)

Smoke-test: place an order, confirm a task appears (`gcloud tasks list --queue lumina-orders --location us-central1`) and the package is delivered; for A2A, send a non-blocking `message/send` then poll `tasks/get` and confirm the task is found.

## How the queue solves the autoscaler-blindness problem

The old background-thread model didn't scale horizontally: Cloud Run's autoscaler scales on **HTTP
request volume**, but the heavy work happened *after* the request returned, so it was invisible to the
autoscaler — orders piled on the warm instance instead of spreading out. With Cloud Tasks the work runs
**inside** the task's HTTP request, so the autoscaler sees it, spreads dispatches across instances up to
the queue's concurrency cap, and the service can scale to zero between bursts.

## Remaining future work

- **Resumable long jobs.** A pack that runs past the ~30 min HTTP-target dispatch deadline gets
  redelivered and currently re-runs from scratch (idempotency still prevents a double *delivery*, just
  not wasted partial compute). Checkpoint per-stage in Firestore to resume instead of restart.
- **OIDC instead of the shared secret** on `/internal/run-order` (grant the Cloud Tasks SA
  `roles/iam.serviceAccountTokenCreator`, attach an OIDC token, verify audience/issuer in the worker).
- **Route paid A2A orders through the same queue** to unify durability across both surfaces (A2A task
  *state* is already durable via `FirestoreTaskStore`).
- **Dead-letter handling.** Surface tasks that exhaust `--max-attempts` (e.g. mark the escrow job
  Failed + refund) instead of letting them drop silently.
