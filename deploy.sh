#!/usr/bin/env bash
#
# Deploy the Lumina marketplace agent to Cloud Run with settings that make background generation
# reliable under real, multi-platform concurrency (aifreelance.shop + agentalent.ai + direct A2A).
# Rationale for every flag lives in docs/SCALING.md — the short version:
#
#   --no-cpu-throttling : the webhook returns "accepted" in <1s, then does 15-40 min of generation
#                         in a background thread. With Cloud Run's DEFAULT (CPU only during a
#                         request) that work is starved the moment no request is in flight, so
#                         orders silently never finish unless someone keeps the live page open.
#                         Always-allocated CPU is the single most important fix here.
#   --min-instances 1   : the autoscaler scales on REQUEST volume and is blind to background jobs,
#                         so it would evict an instance mid-generation. Keep one always warm.
#   --timeout 3600      : A2A message/stream and blocking sends hold the HTTP request open for the
#                         whole job; the default 300s would 504 mid-generation.
#   --cpu 2 --memory 2Gi: Pillow + concurrent generations + media proxying need headroom. An OOM
#                         takes down EVERY in-flight background job on the instance.
#   --concurrency 8     : don't pile many HTTP requests on top of the heavy background work.
#   --max-instances 4   : per-instance image cap is IMAGE_CONCURRENCY (=3); 3 x 4 = 12 keeps peak
#                         image calls within the project's Vertex quota. Raise both ONLY after
#                         raising quota.
#
# Cost note: --no-cpu-throttling + --min-instances 1 means ~1 always-on instance (~$100-140/mo at
# 2 vCPU / 2 GiB; ~$60/mo at --cpu 1 --memory 1Gi). Trivial against subscription revenue, and the
# price of orders that actually finish. Env vars (MARKETPLACE_TOKEN, SERVICE_URL, IMAGE_CONCURRENCY,
# MAX_CONCURRENT_JOBS, FREE_REVISIONS) persist across revisions.
set -euo pipefail

PROJECT="${PROJECT:-aifreelance-hackathon}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-lumina-marketplace}"

exec gcloud run deploy "$SERVICE" \
  --source . \
  --project "$PROJECT" \
  --region "$REGION" \
  --no-cpu-throttling \
  --min-instances 1 \
  --max-instances 4 \
  --timeout 3600 \
  --cpu 2 \
  --memory 2Gi \
  --concurrency 8
