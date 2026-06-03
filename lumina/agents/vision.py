"""Product vision: look at the uploaded photo and record a precise product description that
grounds the rest of the pipeline (brief, scenes, copy) in the REAL product."""
from google.adk.agents import LlmAgent

from ..config import settings
from ..tools.vision import describe_product

product_vision_agent = LlmAgent(
    name="product_vision",
    model=settings.model_reasoning,
    description="Inspects the uploaded product photo and records a precise product description.",
    instruction=(
        "Call describe_product to inspect the uploaded product photo, then reply with ONLY the "
        "resulting description text (no preamble). This grounds the whole pipeline in the real "
        "product, even when the user's text brief is vague."
    ),
    tools=[describe_product],
    output_key="product_description",
)
