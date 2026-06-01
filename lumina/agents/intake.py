"""Intake agent: freeform brief -> structured CreativeBrief."""
from google.adk.agents import LlmAgent

from ..config import settings
from ..schemas import CreativeBrief

intake_agent = LlmAgent(
    name="intake",
    model=settings.model_reasoning,
    description="Parses a freeform product brief into a structured CreativeBrief.",
    instruction=(
        "You are the intake analyst for a content studio. From the user's product brief, "
        "extract a structured creative brief: infer a concise brand_voice, the product_type, "
        "the key_features, and the target channels. Stay faithful to the brief."
    ),
    output_schema=CreativeBrief,
    output_key="brief",
)
