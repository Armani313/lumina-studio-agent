"""Video production: one short cinematic product video (Veo, image-to-video)."""
from google.adk.agents import LlmAgent

from ..config import settings
from ..tools.video import generate_product_video

video_agent = LlmAgent(
    name="video_production",
    model=settings.model_reasoning,
    description="Produces a short cinematic product video (Veo) animated from the product photo.",
    instruction=(
        "You are the video producer. From the brief and plan below, craft ONE concise cinematic "
        "motion concept for a short vertical product video that animates FROM the product photo: "
        "keep the product identical, use gentle on-brand motion (e.g. slow push-in, soft light "
        "shift, shallow depth of field). Call generate_product_video once with aspect_ratio '9:16'. "
        "Then reply with the resulting video gs_uri.\n\n"
        "Brief:\n{brief}\n\nPlan:\n{plan}"
    ),
    tools=[generate_product_video],
    output_key="videos",
)
