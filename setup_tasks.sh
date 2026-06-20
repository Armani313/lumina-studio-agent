#!/usr/bin/env bash
#
# One-time setup for the Cloud Tasks order queue (durable, retried, globally rate-limited order
# processing — see docs/SCALING.md and marketplace/cloud_tasks.py).
#
# After this runs, the marketplace webhook enqueues each order instead of running it in an in-process
# thread; the worker endpoint /internal/run-order generates it inside the task's HTTP request. If the
# env vars below are NOT set on the service, the code transparently falls back to the old in-process
# path, so this is safe to roll out gradually.
set -euo pipefail

PROJECT="${PROJECT:-aifreelance-hackathon}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-lumina-marketplace}"
QUEUE="${QUEUE:-lumina-orders}"

# --- 1. Create the queue (idempotent: ignore "already exists") --------------------------------
#   --max-concurrent-dispatches : GLOBAL cap on orders generating at once (across ALL instances).
#                                 This is the real Vertex-quota throttle. With IMAGE_CONCURRENCY=3
#                                 per instance, 8 is a safe start — raise it only after raising quota.
#   --max-dispatches-per-second : smooth the rate so a burst doesn't hit Vertex all at once.
#   --max-attempts              : retry a crashed dispatch a few times, then dead-letter (give up).
gcloud tasks queues create "$QUEUE" \
  --project "$PROJECT" --location "$REGION" \
  --max-concurrent-dispatches 8 \
  --max-dispatches-per-second 2 \
  --max-attempts 3 \
  || echo "queue '$QUEUE' already exists — leaving its config as-is (edit with: gcloud tasks queues update)"

# --- 2. Wire the service to the queue ----------------------------------------------------------
# TASKS_SECRET gates the public /internal/run-order endpoint (only Cloud Tasks knows it). Generate a
# strong one once and keep it; rotating it is fine (set a new value and redeploy).
SECRET="${TASKS_SECRET:-$(openssl rand -hex 32)}"

gcloud run services update "$SERVICE" \
  --project "$PROJECT" --region "$REGION" \
  --update-env-vars "TASKS_QUEUE=${QUEUE},TASKS_LOCATION=${REGION},TASKS_SECRET=${SECRET}"

echo
echo "Done. Queue '$QUEUE' is live and the service is wired to it."
echo "With the queue handling durability, you MAY drop the always-on instance to save cost:"
echo "  gcloud run services update $SERVICE --project $PROJECT --region $REGION --min-instances 0"
echo "(the queue keeps work safe while instances are cold and wakes the worker on demand)."
