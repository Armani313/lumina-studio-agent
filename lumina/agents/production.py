"""Production stage: image generation, copywriting and video run concurrently (ParallelAgent)."""
from google.adk.agents import LlmAgent, ParallelAgent

from ..config import settings
from ..tools.generation import generate_copy, generate_lifestyle_image
from .video import video_agent

image_production_agent = LlmAgent(
    name="image_production",
    model=settings.model_reasoning,
    description="Generates the planned lifestyle images.",
    instruction=(
        "You produce lifestyle images. For EACH shot in the plan below, call "
        "generate_lifestyle_image with that shot's scene_description and aspect_ratio. "
        "After all shots are generated, reply with a JSON array where each element has the "
        "fields channel, gs_uri and https_url.\n\n"
        "Shot plan:\n{plan}"
    ),
    tools=[generate_lifestyle_image],
    output_key="images",
)

copywriter_agent = LlmAgent(
    name="copywriter",
    model=settings.model_reasoning,
    description="Writes channel marketing copy in the brand voice.",
    instruction=(
        "You are the copywriter. Call generate_copy once for the plan's copy_channel, using "
        "the brief's product_name, key_features and brand_voice, and pass language set to the "
        "brief's language so the copy is in the user's language. Ground the copy in the ACTUAL "
        "product below — never claim features the product does not have. Then reply with the "
        "returned copy as JSON.\n\nBrief:\n{brief}\n\nActual product (from photo):\n"
        "{product_description?}\n\nPlan:\n{plan}"
    ),
    tools=[generate_copy],
    output_key="copy_doc",
)

production_agent = ParallelAgent(
    name="production",
    description="Runs image production, copywriting and video production concurrently.",
    sub_agents=[image_production_agent, copywriter_agent, video_agent],
)
