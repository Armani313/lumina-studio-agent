"""Lumina as an A2A service (A2A protocol 0.3, JSON-RPC transport).

The AgentCard is hand-written rather than ADK's auto-built one, so that:
  * ``url`` advertises the real public RPC endpoint, not the container bind address;
  * ``skills`` is a curated catalog instead of a dump of every sub-agent's internal prompt;
  * ``capabilities.streaming`` is declared, which unlocks ``message/stream``.

Discovery (A2A spec: well-known URI at the domain root):
  GET /.well-known/agent-card.json        (served by marketplace.app at the domain root)
  GET /a2a/.well-known/agent-card.json    (served by this app at its /a2a mount)
JSON-RPC endpoint: POST {A2A_RPC_URL} — the /a2a mount in marketplace.app.

Run standalone (local):
  A2A_RPC_URL=http://localhost:8081/ .venv/bin/uvicorn marketplace.a2a_server:a2a_app --host 0.0.0.0 --port 8081
"""
import os

from a2a.types import AgentCapabilities, AgentCard, AgentProvider, AgentSkill
from google.adk.a2a.utils.agent_to_a2a import to_a2a

from lumina.agent import root_agent

SERVICE_URL = os.getenv("SERVICE_URL", "https://lumina-marketplace-587790795280.us-central1.run.app")
# Public JSON-RPC endpoint other agents should POST to (trailing slash: the exact mount root,
# so clients don't depend on a 307 redirect).
A2A_RPC_URL = os.getenv("A2A_RPC_URL", f"{SERVICE_URL}/a2a/")


def build_agent_card(rpc_url: str = A2A_RPC_URL) -> AgentCard:
    """The public AgentCard. Input/output are text-only on purpose: over A2A the agent takes a
    product brief (plus optional brand link) and replies with a delivery summary + manifest URL;
    photo attachments and paid orders flow through the marketplace webhook, not this surface."""
    return AgentCard(
        name="Lumina Studio",
        description=(
            "Autonomous on-brand content studio for e-commerce. From one short product brief it "
            "researches the brand (live web + brand-guideline RAG), plans a shot list, then "
            "produces a complete content package — lifestyle images in platform aspect ratios, "
            "designed product cards, product videos and channel marketing copy — reviewed by a "
            "self-correcting QA loop and delivered as a manifest of asset URLs. Built with Google "
            "ADK (sequential / parallel / loop agents) on Vertex AI: Gemini, Imagen-class image "
            "generation and Veo."
        ),
        url=rpc_url,
        preferred_transport="JSONRPC",
        provider=AgentProvider(organization="Lumina Studio", url=SERVICE_URL),
        version="1.0.0",
        documentation_url="https://github.com/Armani313/lumina-studio-agent#readme",
        capabilities=AgentCapabilities(
            streaming=True,            # DefaultRequestHandler + ADK executor stream task events
            push_notifications=False,  # config store only, no sender wired
            state_transition_history=False,
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        supports_authenticated_extended_card=False,
        skills=[
            AgentSkill(
                id="content-package",
                name="Product content package",
                description=(
                    "Turn a short product brief (optionally with a brand link) into a complete, "
                    "ready-to-publish e-commerce content package: a varied set of lifestyle images "
                    "(hero / macro / flatlay / on-model, in instagram, stories, amazon and web-hero "
                    "aspect ratios), designed product-card variants, product videos and marketing "
                    "copy in the brand voice and the buyer's language. Every asset passes a "
                    "self-correcting QA loop; the reply is a short summary plus the manifest URL "
                    "listing all asset URLs."
                ),
                tags=["content-generation", "ecommerce", "images", "product-cards", "video", "copywriting"],
                examples=[
                    "Content pack for 'Aurora' ceramic pour-over coffee set — warm minimal mood, brand: auroraware.com",
                    "Need lifestyle images, 2 product cards and an orbit video for a matte-black mechanical keyboard; bold voice, audience: gamers",
                ],
            ),
            AgentSkill(
                id="brand-grounding",
                name="Brand research & grounding",
                description=(
                    "Before producing anything, grounds the shoot in the customer's real brand: "
                    "reads the brand's site, runs live Google Search research and retrieves "
                    "brand-guideline rules (palette, tone, imagery do/don'ts) via RAG, so the "
                    "output matches the actual brand rather than a generic style."
                ),
                tags=["branding", "research", "rag", "grounding"],
            ),
        ],
    )


# Starlette app exposing the A2A protocol (message/send, message/stream, tasks/*) + the card.
# A Firestore-backed task store replaces ADK's default in-memory one so task state survives instance
# restarts and is shared across Cloud Run instances — otherwise a client's tasks/get poll can be
# load-balanced to an instance that never saw the task ("task not found"). Falls back to the
# in-memory default if the store can't be wired, so A2A durability can never block startup.
def _build_a2a_app():
    try:
        from .a2a_task_store import FirestoreTaskStore

        return to_a2a(root_agent, agent_card=build_agent_card(), task_store=FirestoreTaskStore())
    except Exception as exc:  # noqa: BLE001 — never let A2A persistence wiring break boot
        import logging

        logging.getLogger("lumina").warning(
            "A2A: persistent task store unavailable (%r); using in-memory store", exc)
        return to_a2a(root_agent, agent_card=build_agent_card())


a2a_app = _build_a2a_app()
