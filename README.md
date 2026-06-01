# Lumina Studio Agent

> Autonomous, multi-agent **on-brand content studio** built net-new for the Google for
> Startups AI Agents Challenge. Built on **Google ADK** + **Gemini Enterprise Agent Platform
> (Vertex AI)**. From a single product brief it plans the shoot, generates an on-brand content
> package (lifestyle imagery + channel copy), **self-checks every asset for brand consistency
> and product fidelity in a correction loop**, and delivers the package to Cloud Storage.

## Status — multi-agent pipeline runs end-to-end ✅
A `SequentialAgent` orchestrates five stages, demonstrating the three ADK workflow patterns
(Sequential, Parallel, Loop):

```
product photo + brief
  → intake (→CreativeBrief)
  → ParallelAgent[ brand_research (Google Search) ‖ brand_rag (Vertex AI Search) ]
  → shot_planner (→ N-shot ShotPlan)
  → ParallelAgent[ image_production ×N (product-faithful) ‖ copywriter ‖ video_production (Veo) ]
  → card_production (2 product cards, composited crisp text)
  → LoopAgent[ brand_qa: fidelity + brand review → regenerate failures → exit ]  (max 2)
  → delivery (images + cards + video + copy → manifest.json → GCS)
```

On a live run the QA loop autonomously caught a garbled-text artifact on a generated label,
regenerated that shot, re-reviewed it (0.98), and approved the package. See
[ARCHITECTURE.md](ARCHITECTURE.md).

**Deployed** to Vertex AI Agent Engine (us-central1, `--trace_to_cloud`) and verified end-to-end
via remote `stream_query`. Resource id `4329993888170246144`.

Brand grounding is live: `brand_research` (Google Search) + `brand_rag` (Vertex AI Search over a
brand-guidelines data store) feed the planner, so generated scenes cite the brand's exact palette.

Marketplace + A2A implemented in `marketplace/` (order UI + Firestore escrow + the agent
published as an A2A service with a discoverable AgentCard).

## Deployment
- **Agent (managed runtime):** Vertex AI Agent Engine `…/reasoningEngines/4329993888170246144`
  (us-central1, Cloud Trace) — queryable via the Agent Engine API and the A2A AgentCard.
- **Marketplace:** Cloud Run `lumina-marketplace` →
  `https://lumina-marketplace-587790795280.us-central1.run.app` — **public**: open the URL, upload a
  product photo, fund escrow, watch the agent deliver, accept to release. Full order lifecycle
  verified in production. (Scripted smoke test: `python marketplace/smoke_test.py [url]`.)

### For judges (testing access)
1. Open the marketplace URL above.
2. Upload [`assets/sample_product.png`](assets/sample_product.png) (a sample Aurelia product
   photo) — or any product photo of your own.
3. Add a one-line brief and click **Order & fund escrow**.
4. Watch the agent work; when the job is **Delivered**, review the package (images, cards, video,
   copy) and click **Accept** to release escrow.

**Next:** record the demo video; finalize the write-up ([SUBMISSION.md](SUBMISSION.md),
[DEMO_SCRIPT.md](DEMO_SCRIPT.md)).

## Platform facts (verified 2026-06-01 against live Vertex APIs)
- **SDK:** Google Gen AI SDK (`google-genai`) on the Vertex backend. The legacy `vertexai.*`
  generative modules are removed 2026-06-24 — not used here.
- **Endpoints:** Gemini 3.x (text + image) → `global`; Veo (video) → `us-central1`.
- **Models:** `gemini-3.5-flash` (reasoning/QA), `gemini-3.1-flash-image` (Nano Banana 2),
  `gemini-3-pro-image` (Nano Banana Pro), `veo-3.1-fast-generate-001` (video).
- Full config: [`gcp.env`](gcp.env).

## Setup
```bash
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python -r requirements.txt

# Application Default Credentials for local runs:
gcloud auth application-default login
gcloud auth application-default set-quota-project aifreelance-hackathon
```

## Run
```bash
.venv/bin/python run_slice.py     # runs the full pipeline on a sample fictional brand
```

## Layout
```
lumina/
  config.py              # env + verified model IDs
  clients.py             # google-genai clients (Gemini global / Veo regional) + GCS
  schemas.py             # CreativeBrief, ShotPlan (ADK output_schema targets)
  agent.py               # exposes root_agent
  agents/
    pipeline.py          # SequentialAgent assembling all stages
    intake.py  planner.py  production.py  qa.py  delivery.py
  tools/
    generation.py        # generate_copy, generate_lifestyle_image
    qa.py                # review_image_brand_fit (multimodal), exit_loop
    delivery.py          # GCS upload + write_manifest
run_slice.py             # local end-to-end runner
```
