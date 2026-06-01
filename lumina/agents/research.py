"""Grounding stage: concurrent live web research (Google Search) + brand-guideline RAG
(Vertex AI Search). Runs after intake, before shot planning."""
from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.tools import VertexAiSearchTool, google_search

from ..config import settings

brand_research_agent = LlmAgent(
    name="brand_research",
    model=settings.model_reasoning,
    description="Researches the brand's public presence and category context via Google Search.",
    instruction=(
        "You are a brand researcher. Using Google Search, research the brand and product in the "
        "brief below: positioning, typical visual style, target audience, and 2-3 category cues. "
        "Summarize concisely in 4-6 bullets for the shot planner. If the brand appears fictional "
        "or unfindable, say so explicitly and infer sensible cues from the product category.\n\n"
        "Brief:\n{brief}"
    ),
    tools=[google_search],
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
