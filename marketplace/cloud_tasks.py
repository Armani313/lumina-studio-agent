"""Cloud Tasks integration: hand long-running order generation to a durable, retried, globally
rate-limited queue instead of an in-process background thread.

Why: the marketplace webhook returns ``{accepted}`` in <1s and then must run a 15-40 min generation.
Doing that in a background thread on the same instance means (a) Cloud Run throttles its CPU once no
request is in flight, (b) a scale-in/crash loses the job with no retry, and (c) the autoscaler — which
scales on request volume — never sees the work, so it can't spread load across instances. Routing the
order through Cloud Tasks fixes all three: the task is persisted, redelivered on crash, and the queue's
``--max-concurrent-dispatches`` is a GLOBAL concurrency cap (natural Vertex-quota protection). The
worker runs the job INSIDE the task's HTTP request, so CPU is allocated normally and the service can
even scale to zero.

Config (env): ``TASKS_QUEUE``, ``TASKS_LOCATION`` (default us-central1), ``TASKS_SECRET``. When unset,
``enqueue_order`` returns False and the caller falls back to the in-process path — so local dev and
un-provisioned environments keep working unchanged. See ``setup_tasks.sh`` / ``docs/SCALING.md``.

Auth: the worker endpoint must stay public (the marketplace webhook can't send GCP creds, so the
service can't be IAM-locked wholesale). We gate ``/internal/run-order`` with a shared-secret header,
constant-time compared — the same model as ``MARKETPLACE_TOKEN``. OIDC tokens are the stronger upgrade.
"""
from __future__ import annotations

import hmac
import json
import os

from lumina.config import settings

_QUEUE = os.getenv("TASKS_QUEUE", "")
_LOCATION = os.getenv("TASKS_LOCATION", "us-central1")
_SECRET = os.getenv("TASKS_SECRET", "")
_SERVICE_URL = os.getenv("SERVICE_URL", "https://lumina-marketplace-587790795280.us-central1.run.app")
# HTTP-target tasks allow a dispatch deadline up to 30 min; most packs finish inside it. A job that
# runs longer gets no HTTP response, so Cloud Tasks redelivers it — the worker's idempotency check
# then prevents a double-delivery (it may re-run from scratch; resumable checkpoints are future work).
_DEADLINE_S = int(os.getenv("TASKS_DISPATCH_DEADLINE", "1800"))

ORDER_PATH = "/internal/run-order"

_client = None


def enabled() -> bool:
    """True when a queue and a worker secret are configured; else callers run jobs in-process."""
    return bool(_QUEUE and _SECRET)


def secret_ok(presented: str) -> bool:
    """Constant-time check that a request to the worker endpoint actually came from our queue."""
    return bool(_SECRET) and hmac.compare_digest((presented or "").strip(), _SECRET)


def _get_client():
    global _client
    if _client is None:
        from google.cloud import tasks_v2  # lazy import keeps the dependency optional at runtime
        _client = tasks_v2.CloudTasksClient()
    return _client


def enqueue_order(job_args: dict) -> bool:
    """Enqueue one order for the worker endpoint.

    Returns True if the order was handed to Cloud Tasks, False if the queue isn't configured or the
    enqueue failed — in which case the caller runs it in-process, so an order is never lost to a queue
    hiccup. Never raises.
    """
    if not enabled():
        return False
    try:
        from google.cloud import tasks_v2
        from google.protobuf import duration_pb2

        client = _get_client()
        parent = client.queue_path(settings.project, _LOCATION, _QUEUE)
        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{_SERVICE_URL}{ORDER_PATH}",
                "headers": {"Content-Type": "application/json", "X-Tasks-Secret": _SECRET},
                "body": json.dumps(job_args).encode("utf-8"),
            },
            "dispatch_deadline": duration_pb2.Duration(seconds=_DEADLINE_S),
        }
        client.create_task(parent=parent, task=task)
        print(f"[cloud_tasks] enqueued order jid={job_args.get('jid') or '-'}", flush=True)
        return True
    except Exception as exc:  # noqa: BLE001 — never lose an order to an enqueue error
        print(f"[cloud_tasks] enqueue failed ({exc!r}); falling back to in-process", flush=True)
        return False
