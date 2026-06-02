# Demo video script — Lumina Studio Agent (target ≤ 2:00)

Rules: judges watch only the first 2:00 · English (or English subtitles) · show the real product
on the platform · fictional brand only ("Aurelia") · no third-party logos/brands on screen.

Record at 1920×1080. Keep voiceover ≈ 280–300 words. Timestamps are targets.

---

### 0:00–0:12 — Hook / problem
**On screen:** title card "Lumina Studio Agent — hire an autonomous content studio".
**VO:** "Every brand needs a flood of on-brand product content — for Instagram, Amazon, video.
Today that means photographers, retouchers, and copywriters. It's slow, costly, and inconsistent."

### 0:12–0:30 — Order it on the marketplace
**On screen:** the Cloud Run marketplace UI. Upload the Aurelia serum **product photo**, type a
one-line brief, click **"Order & fund escrow"**. Escrow badge flips to **Funded**.
**VO:** "Instead, you hire an agent. Upload a product photo, a short brief, fund escrow — and the
autonomous studio takes the job."

### 0:30–1:05 — The agent works (the multi-agent pipeline)
**On screen:** the live status log streaming stages: intake → grounding → shot planner →
image / copy / video → product cards → QA → delivery. Briefly cut to **Cloud Trace** showing the
reasoning spans.
**VO:** "It runs a multi-agent pipeline on Google's ADK. It grounds itself in the brand — live web
research plus retrieval over the brand's guidelines in Vertex AI Search — then plans the shoot.
Image, copy, and video are produced in parallel, each conditioned on the real product photo so the
output is the actual product, not a look-alike. A QA agent reviews every asset for fidelity and
brand fit, and regenerates anything that fails — all traced in Cloud Trace."

### 1:05–1:35 — The delivered package
**On screen:** the gallery fills in — several on-brand lifestyle images, **two product cards** with
crisp text, the **video** playing, and the ad copy. Show the original product photo side-by-side to
prove fidelity (same frosted "AURELIA" bottle, same palette).
**VO:** "Minutes later: a complete, on-brand package — lifestyle imagery, marketplace product
cards, ad copy, and a short video. The exact product, in the brand's palette, on-brief."

### 1:35–1:50 — Escrow + A2A
**On screen:** click **"Accept & release escrow"** → status **Completed**, escrow **Released**.
Then show the agent's **A2A AgentCard** JSON at `/a2a/.well-known/agent-card.json`.
**VO:** "Accept the work and escrow releases — pay for results. And because the agent is published
over A2A, other agents can discover and hire it too."

### 1:50–2:00 — Architecture + close
**On screen:** the architecture diagram (ARCHITECTURE.md), highlight ADK Sequential/Parallel/Loop,
Gemini on Vertex, Agent Engine + Cloud Run.
**VO:** "Built net-new on ADK and the Gemini Enterprise Agent Platform, deployed on Agent Engine
and Cloud Run. Lumina — content production as a hireable agent."

---

## Shot list / assets to capture beforehand
- A clean run recorded at **IMAGE_COUNT=8–12** for a fuller gallery (pre-render so the demo isn't
  waiting on generation; or speed-ramp the wait).
- Cloud Trace screenshot/scroll for the deployed agent.
- The AgentCard JSON (`curl .../a2a/.well-known/agent-card.json | jq`).
- Side-by-side of the input product photo vs a generated image (fidelity proof).
- The architecture diagram exported to PNG.
