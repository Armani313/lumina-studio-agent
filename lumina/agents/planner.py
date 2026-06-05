"""Shot planner: CreativeBrief -> ShotPlan (small shot list + copy channel)."""
from google.adk.agents import LlmAgent

from ..config import settings
from ..schemas import ShotPlan

shot_planner_agent = LlmAgent(
    name="shot_planner",
    model=settings.model_reasoning,
    description="Turns a creative brief into a small shot list and a copy plan.",
    instruction=(
        f"You are the shot planner. Using the creative brief, the live brand research, and the "
        f"retrieved brand guidelines below, plan exactly {settings.image_count} distinct lifestyle "
        f"shots across the brief's channels, each with an appropriate aspect ratio (instagram 4:5, "
        f"stories 9:16, amazon 1:1, web hero 16:9). Make the SET genuinely varied: assign each shot a "
        f"distinct shot_type and cover a range — include at least one 'hero', plus a mix of 'macro' "
        f"(extreme close-up of texture/detail), 'lifestyle' (real-world context), 'flatlay' (styled "
        f"top-down), 'ecommerce' (clean/seamless background) and 'on_model' where the category fits. "
        f"Vary lighting, mood, angle, palette and setting across shots so no two feel alike. "
        f"CRITICAL: every shot MUST feature the product itself as the clear hero subject — large, in "
        f"focus and unmistakably visible; NEVER a scene where the product is absent, tiny or "
        f"incidental. Honor the guidelines' palette, imagery direction and forbidden elements, and "
        f"the category shot strategy below. Write each scene_description like a professional "
        f"photographer's art-direction (lighting setup, lens/angle, composition, props, mood). "
        f"Choose one primary copy_channel.\n\n"
        "Product category: {product_category?}\n"
        "Shot strategy for this category:\n{shot_strategy?}\n\n"
        "Creative brief:\n{brief}\n\n"
        "Actual product (from photo):\n{product_description?}\n\n"
        "Brand research (web):\n{brand_research?}\n\n"
        "Brand guidelines (retrieved):\n{brand_knowledge?}"
    ),
    output_schema=ShotPlan,
    output_key="plan",
)
