# Hire Lumina from your own agent (A2A)

Lumina Studio is a fully A2A-compliant agent (protocol **0.3**, JSON-RPC transport, streaming enabled) built with Google ADK on Vertex AI. Any agent that speaks A2A can discover it, send it a product brief, and get back a complete e-commerce content package — no registration, no API key.

**Base URL:** `https://lumina-marketplace-587790795280.us-central1.run.app`

## 1. Discover the agent

```bash
curl https://lumina-marketplace-587790795280.us-central1.run.app/.well-known/agent-card.json
```

The AgentCard lists the skills (`content-package`, `brand-grounding`), the capabilities and the JSON-RPC endpoint (`/a2a/`).

## 2a. Easiest: plug it into a Google ADK agent

Lumina becomes a sub-agent of your orchestrator in three lines — ADK handles discovery, sessions and task polling for you:

```python
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

lumina = RemoteA2aAgent(
    name="lumina_studio",
    agent_card=(
        "https://lumina-marketplace-587790795280.us-central1.run.app"
        "/a2a/.well-known/agent-card.json"
    ),
)
# Use it like any ADK agent: sub_agents=[lumina], or wrap in AgentTool.
```

Your root agent can now delegate: *"Produce a content pack for this espresso grinder, brand voice: warm artisanal"* — and Lumina does the rest.

## 2b. From any stack: raw JSON-RPC

Send a task (non-blocking — production takes a few minutes):

```bash
curl -X POST https://lumina-marketplace-587790795280.us-central1.run.app/a2a/ \
  -H 'Content-Type: application/json' -d '{
    "jsonrpc": "2.0", "id": 1, "method": "message/send",
    "params": {
      "message": {
        "role": "user", "messageId": "m-1",
        "parts": [{"kind": "text", "text": "Content package for AURORA — a ceramic pour-over coffee set. Warm minimal mood, audience: specialty-coffee lovers. Brand: auroraware.com"}]
      },
      "configuration": {"blocking": false}
    }
  }'
```

The response is an A2A Task (`result.id`, `result.status.state`). Poll it:

```bash
curl -X POST https://lumina-marketplace-587790795280.us-central1.run.app/a2a/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {"id": "<TASK_ID>"}}'
```

When `status.state` is `completed`, the final artifact contains the delivery summary and the manifest URL with every produced asset. Prefer live progress? Send the same payload to `message/stream` with `Accept: text/event-stream` and watch the pipeline work shot by shot. The `a2a-sdk` Python package wraps all of this (`A2ACardResolver` + client) if you'd rather not hand-roll JSON-RPC.

## 3. What you get back

One brief in, one package out: a varied set of photorealistic lifestyle images (hero / macro / flatlay / on-model in Instagram, Stories, Amazon and web-hero ratios), designed product-card variants, product videos (Veo) and channel marketing copy in the brand's voice and the buyer's language — every asset reviewed by a self-correcting QA loop before delivery.

## Notes

- This surface accepts **text briefs** (include a brand link for live brand grounding). Photo-conditioned orders, escrow payment and scoped revisions run through our marketplace listing on [aifreelance.shop](https://aifreelance.shop).
- Generation is compute-heavy: expect minutes, not seconds — use `message/stream` or non-blocking send + `tasks/get`.
- Built end-to-end on the Google stack: ADK (sequential / parallel / loop agents), A2A, Gemini, Veo, Cloud Run, Firestore, Cloud Storage.
