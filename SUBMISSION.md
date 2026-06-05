# Lumina Studio Agent — Submission

**Google for Startups AI Agents Challenge · Track 1 (Build, net-new)**

An autonomous, multi-agent **on-brand content studio** that a brand can *hire* through an
escrow marketplace. Give it a product photo, a short brief, and (optionally) a brand link; it
researches the brand, plans the shoot, generates a faithful on-brand content package — lifestyle
imagery, styled marketplace cards, multi-variant copy, and two short videos (macro + UGC) — checks
its own work for product fidelity and brand consistency, and delivers the package. Built net-new on
**Google ADK** and the **Gemini Enterprise Agent Platform (Vertex AI)**.

---

## 1. Problem (B2B)
DTC brands, agencies, and marketplaces need a constant, high volume of **on-brand** product
content across channels (Instagram, Stories, Amazon, web hero, video). Today that means
photographers, retouchers, copywriters, and editors — expensive, slow, and inconsistent. As SKU
counts and channels multiply, content production becomes a bottleneck.

## 2. Solution
Lumina is an "art director + studio" as an autonomous agent. One brief in → a complete, brand-
checked package out, in minutes, with no human in the loop mid-process. It is **hireable** via a
marketplace with escrow: a client funds a job, the agent fulfils it, the client accepts, and
escrow releases — a direct model for monetizing autonomous work.

Why an **agent**, not a UI tool: a single prompt cannot reliably do brand research + retrieval-
grounded planning + product-faithful generation across channels + channel-specific copy + a
self-correcting QA pass. Decomposition + grounding + retrieval + a regeneration loop is what makes
the output production-grade.

## 3. How it works — multi-agent architecture (ADK)
A root `SequentialAgent` orchestrates the pipeline, using all three ADK workflow patterns
(Sequential, Parallel, Loop) plus an LLM-driven QA agent:

```
product photo + brief
  → product_vision    (sees the photo: classifies category + writes a precise product description)
  → intake            (LlmAgent, output_schema=CreativeBrief incl. detected language)
  → grounding         (ParallelAgent)
        ├ brand_research  (google_search — live web grounding)
        └ brand_rag       (VertexAiSearchTool — brand-guideline RAG)
  → shot_planner      (LlmAgent, output_schema=ShotPlan; category-driven shot strategy)
  → production        (ParallelAgent)
        ├ image_production  (×N, image-conditioned on the product photo)
        ├ copywriter        (multi-variant copy in the user's language)
        └ video_production  (TWO Veo clips: a macro detail clip + a UGC-style clip)
  → card_production   (2 styled, selling cards: generated bg + composited typography + CTA pill)
  → qa_loop           (LoopAgent ≤2: multimodal fidelity + brand review → regenerate fails → exit)
  → delivery          (assemble package + quality scorecard + manifest → Cloud Storage)
```

State flows via ADK session state (`output_key` + `{state}` interpolation). The product photo is
threaded to the image/video/QA tools through `ToolContext.state` (not the LLM) for reliable fidelity.

## 4. What makes it production-grade
- **Sees the product:** a vision stage classifies the product category from the photo and writes a
  category-specific shot strategy (apparel → on-model/flat-lay, jewelry → macro, bottle → lifestyle),
  so even a one-word brief yields accurate scenes and copy about the *real* product.
- **Product fidelity:** image and video are *conditioned on the real product photo* (reference
  frame), so outputs depict the actual product; QA compares original vs generated and fails on drift.
- **Grounding + RAG:** `google_search` for live brand/category research, and **Vertex AI Search**
  over a brand-guidelines data store so generated scenes cite the brand's exact palette, props, and
  forbidden elements.
- **Self-correcting QA loop + quality scorecard:** a multimodal reviewer scores each asset, swaps
  regenerated fixes into the delivered set, and surfaces a per-asset fidelity/brand **scorecard**.
- **Multi-variant copy, in the user's language:** title, short, long SEO, emotional, bullets, CTA,
  keywords and customer reviews — produced in the language of the brief (e.g. a Russian brief → Russian copy).
- **Styled, selling product cards:** crisp typography composited over a generated background
  (gradient scrim, accent color sampled from the product, CTA pill) — not garbled image-model text.
- **Two videos:** a macro detail clip and a UGC-style clip (Veo image-to-video).
- **Robustness & UX:** image generation is concurrency-throttled with exponential backoff on rate
  limits; per-tool sub-progress streams to the UI so long stages never look frozen.

## 5. Monetization & interoperability
- **Escrow marketplace** (Cloud Run + Firestore): `Funded → InProgress → Delivered → Released`
  (or `Refunded`). A client funds a job; the agent fulfils it; acceptance releases escrow.
- **A2A:** the agent is also published as an **A2A service** (`to_a2a`), mounted on the same Cloud
  Run service at `/a2a`, with a discoverable AgentCard at `/a2a/.well-known/agent-card.json`
  (protocol 0.3.0) — other agents can discover and hire it. This is the bridge to an agent-to-agent
  economy / Cloud Marketplace.

## 6. Technology (all mandatory + encouraged)
| Layer | Used |
|---|---|
| Orchestration | **Google ADK 2.1** (Sequential / Parallel / Loop / LlmAgent, FunctionTools, A2A) |
| Intelligence | **Gemini via Vertex AI**: `gemini-3.5-flash` (reason/QA), `gemini-3.1-flash-image` (Nano Banana 2), `veo-3.1-fast` (video) |
| Grounding | `google_search` + **Vertex AI Search** RAG |
| Runtime | **Vertex AI Agent Engine** (managed) + **Cloud Run** (marketplace) |
| State / storage | **Firestore** (jobs + escrow), **Cloud Storage** (assets) |
| Interop | **A2A** AgentCard |
| Observability | **Cloud Trace** (OpenTelemetry) — reasoning traces |

SDK note: built on the current **`google-genai`** SDK (the legacy `vertexai.*` generative modules
are removed 2026-06-24). Gemini 3.x uses the `global` endpoint; Veo runs in `us-central1`.

## 7. Deployed (testing access)
- **Agent (managed):** Vertex AI Agent Engine `…/reasoningEngines/4329993888170246144` (us-central1,
  Cloud Trace enabled) — queryable via the Agent Engine API and the A2A AgentCard.
- **Marketplace:** Cloud Run — `https://lumina-marketplace-587790795280.us-central1.run.app`
  (open the URL, upload a product photo, fund, watch the agent deliver, accept to release escrow).
- Sample product + brief included; a fictional brand ("Aurelia") is used throughout (no real
  third-party brands, per the rules).

## 8. Originality
Net-new project built during the contest period. It is informed by domain experience but the agent,
prompts, tools, and infrastructure are written from scratch for this challenge on ADK + Vertex.
