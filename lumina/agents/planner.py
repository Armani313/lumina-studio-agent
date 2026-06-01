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
        f"stories 9:16, amazon 1:1, web hero 16:9). Vary the scenes, angles and props so the set is "
        f"diverse. Honor the guidelines' color palette, imagery direction and forbidden elements. "
        f"Choose one primary copy_channel. Write vivid, on-brand scene descriptions.\n\n"
        "Creative brief:\n{brief}\n\n"
        "Brand research (web):\n{brand_research}\n\n"
        "Brand guidelines (retrieved):\n{brand_knowledge}"
    ),
    output_schema=ShotPlan,
    output_key="plan",
)
