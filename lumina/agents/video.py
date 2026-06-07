"""Video production: short Veo clips per the production spec — 360° orbit, voiceover ad, UGC, macro."""
from google.adk.agents import LlmAgent

from ..config import settings
from ..tools.video import generate_product_video

video_agent = LlmAgent(
    name="video_production",
    model=settings.model_reasoning,
    description="Produces the spec's product videos (Veo): 360° orbit, voiceover ad, UGC and/or macro.",
    instruction=(
        "You are the video producer. Produce the videos requested in the production spec below — "
        "call generate_product_video ONCE per clip in spec.videos, using that clip's aspect_ratio "
        "and duration_seconds. If spec.videos is an EMPTY list, produce NO video clips at all (reply "
        "with []). Only if NO production spec is provided should you default to TWO clips (a '360' and "
        "a 'voiceover'). Every clip animates FROM the product photo, keeping the product IDENTICAL to "
        "the real one (same shape, color, materials, logos) and the clear hero.\n\n"
        "Build each clip's `concept` and flags by its KIND:\n"
        "• 360 — a smooth, CONTINUOUS 360-degree orbital turntable: the camera slowly circles a full "
        "revolution around the product (centered, sharp, identical throughout) on a clean seamless "
        "studio backdrop with even premium lighting; photoreal, no on-screen text. "
        "person_generation 'dont_allow', generate_audio false.\n"
        "• voiceover — a cinematic product b-roll with an OFF-SCREEN NARRATOR (no visible speaker). "
        "Decide the product's SINGLE most important feature/benefit, then write ONE short persuasive "
        "spoken line (max ~22 words, ~8s) IN THE BRIEF'S LANGUAGE that describes the product and "
        "recommends buying it, emphasizing that feature. Build concept EXACTLY as: 'Cinematic "
        "product b-roll of <product>: gentle push-ins and tasteful close-ups in soft premium light, "
        "including a close-up of <key feature>. No visible speaker, voiceover only. Warm, confident "
        "narrator says: \"<your line>\". Clear persuasive tone, medium pace. Quiet ambient room tone "
        "only. No on-screen text.' person_generation 'dont_allow', generate_audio true.\n"
        "• ugc — a casual, authentic handheld phone-style clip: the product in a real everyday "
        "setting, natural light, slight handheld motion, social feel. person_generation "
        "'allow_adult', generate_audio false.\n"
        "• macro — an extreme close-up: slow rack-focus over the product's texture, materials and "
        "fine details, shallow depth of field, soft light. person_generation 'dont_allow', "
        "generate_audio false.\n\n"
        "Then reply with ALL resulting video gs_uris as a JSON array.\n\n"
        "Production spec (clips to make):\n{spec?}\n\n"
        "Brief (note its `language`):\n{brief}\n\n"
        "Actual product (from photo):\n{product_description?}\n\nPlan:\n{plan}"
    ),
    tools=[generate_product_video],
    output_key="videos",
)
