"""Grounding stage: concurrent live web research (Google Search) + brand-guideline RAG
(Vertex AI Search). Runs after intake, before shot planning."""
from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.tools import VertexAiSearchTool, google_search, url_context

from ..config import settings

brand_research_agent = LlmAgent(
    name="brand_research",
    model=settings.model_reasoning,
    description="Grounds the studio in the customer's real brand: reads the brand link (URL) + Google Search.",
    instruction=(
        "You are a brand researcher grounding the studio in the customer's REAL brand.\n"
        "STEP 1 — If the brief contains a 'Brand link' (a URL), you MUST READ that exact page with "
        "url_context (and obvious about/brand subpages) and extract CONCRETE identity: color palette "
        "(give hex codes when visible), tone of voice, typography / visual style, product line, and "
        "any do/don't cues.\n"
        "STEP 2 — Use Google Search to fill gaps: positioning, target audience, 2-3 category cues.\n"
        "If the link is a login-walled social profile (Instagram/TikTok/Facebook) or url_context "
        "fails to retrieve it, do NOT invent page content — rely only on Google Search results and "
        "the brief, and say the page itself was unreadable.\n"
        "PRIORITIZE what you read from the brand's own site over generic search results. Then "
        "summarize 4-6 concise, CONCRETE bullets (named colors / hex, tone words, visual-style "
        "keywords, audience) that the shot planner, copywriter and card designer can act on. "
        "If there is no link, research via Google Search; if the brand seems fictional or "
        "unfindable, say so explicitly and infer sensible cues from the product category.\n\n"
        "Brief:\n{brief}"
    ),
    tools=[google_search, url_context],
    output_key="brand_research",
)

brand_rag_agent = LlmAgent(
    name="brand_rag",
    model=settings.model_reasoning,
    description="Retrieves brand-guideline rules (palette, tone, do/don't) from the brand KB.",
    instruction=(
        "You are the brand-guidelines retriever. Query the brand knowledge base for the rules "
        "needed to produce on-brand assets: color palette (hex), tone of voice, imagery "
        "direction, product-fidelity rules, and forbidden elements. Summarize the retrieved "
        "rules concisely for the shot planner and copywriter. Report only what you retrieve.\n\n"
        "Brief:\n{brief}"
    ),
    tools=[VertexAiSearchTool(data_store_id=settings.vertex_search_datastore)],
    output_key="brand_knowledge",
)

grounding_agent = ParallelAgent(
    name="grounding",
    description="Concurrent brand grounding: live web research + brand-guidelines retrieval.",
    sub_agents=[brand_research_agent, brand_rag_agent],
)
