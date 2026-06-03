"""Intake agent: freeform brief -> structured CreativeBrief."""
from google.adk.agents import LlmAgent

from ..config import settings
from ..schemas import CreativeBrief

intake_agent = LlmAgent(
    name="intake",
    model=settings.model_reasoning,
    description="Parses a freeform product brief into a structured CreativeBrief.",
    instruction=(
        "You are the intake analyst for a content studio. You receive the user's (possibly terse) "
        "product brief AND a factual description of the ACTUAL product taken from its photo. Build "
        "a structured creative brief accurate to the REAL product: set product_name, product_type "
        "and key_features primarily from the product description; take brand_name, brand_voice and "
        "channels from the user's brief (infer sensibly if missing). When the user's brief is "
        "vague or conflicts with the photo, trust the product description.\n\n"
        "Actual product (from the photo):\n{product_description?}"
    ),
    output_schema=CreativeBrief,
    output_key="brief",
)
