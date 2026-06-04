"""Video production: two short Veo clips from the product photo — a macro detail clip and a
UGC-style clip."""
from google.adk.agents import LlmAgent

from ..config import settings
from ..tools.video import generate_product_video

video_agent = LlmAgent(
    name="video_production",
    model=settings.model_reasoning,
    description="Produces two short product videos (Veo): a macro detail clip and a UGC-style clip.",
    instruction=(
        "You are the video producer. Produce TWO short videos that animate FROM the product photo "
        "(keep the product identical to the real product):\n"
        "1) MACRO — call generate_product_video with a concept for an EXTREME CLOSE-UP macro: slow "
        "rack-focus over the product's texture, materials and fine details, shallow depth of field, "
        "soft light; aspect_ratio '9:16', duration_seconds 6, person_generation 'dont_allow'.\n"
        "2) UGC — call generate_product_video again with a concept for a casual, authentic, "
        "handheld phone-style user-generated clip: the product in a real everyday setting, natural "
        "light, slight handheld motion, social-media feel; aspect_ratio '9:16', duration_seconds 8, "
        "person_generation 'allow_adult'.\n"
        "Then reply with BOTH resulting video gs_uris as a JSON array.\n\n"
        "Brief:\n{brief}\n\nActual product (from photo):\n{product_description?}\n\nPlan:\n{plan}"
    ),
    tools=[generate_product_video],
    output_key="videos",
)
