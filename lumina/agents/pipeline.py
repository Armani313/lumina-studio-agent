"""The full Lumina pipeline: a SequentialAgent composing all stages.

    Intake (brief) -> ShotPlanner (plan) -> [Image || Copy] -> QA loop -> Delivery
"""
from google.adk.agents import LlmAgent, SequentialAgent

from ..models import reasoning_model, thinking_planner
from .cards import card_production_agent
from .delivery import delivery_agent
from .intake import intake_agent
from .planner import shot_planner_agent
from .production import production_agent
from .qa import qa_loop_agent
from .research import grounding_agent
from .vision import product_vision_agent

root_agent = SequentialAgent(
    name="lumina_pipeline",
    description=(
        "Autonomous on-brand content studio: see the product photo -> intake -> brand grounding "
        "(web + RAG) -> shot planning -> concurrent image+copy+video production -> product cards "
        "-> self-correcting QA loop -> delivery."
    ),
    sub_agents=[
        product_vision_agent,
        intake_agent,
        grounding_agent,
        shot_planner_agent,
        production_agent,
        card_production_agent,
        qa_loop_agent,
        delivery_agent,
    ],
)


def _apply_retry(agent) -> None:
    """Give every LlmAgent a retry-enabled model (so a transient 429/quota spike doesn't fail a
    stage) and, when enabled, a thinking planner that surfaces the model's reasoning summaries."""
    if isinstance(agent, LlmAgent):
        agent.model = reasoning_model()
        planner = thinking_planner()
        if planner is not None:
            agent.planner = planner
    for sub in getattr(agent, "sub_agents", None) or []:
        _apply_retry(sub)


_apply_retry(root_agent)
