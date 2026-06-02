"""Self-correcting QA stage: a LoopAgent that reviews images and regenerates failures."""
from google.adk.agents import LlmAgent, LoopAgent

from ..config import settings
from ..tools.qa import exit_loop, replace_failed_image, review_image_brand_fit

brand_qa_agent = LlmAgent(
    name="brand_qa",
    model=settings.model_reasoning,
    description="Reviews produced images for brand fit/fidelity and regenerates failures.",
    instruction=(
        "You are Brand-Safety QA. The produced images (JSON containing gs_uri values) are below. "
        "For each image gs_uri, call review_image_brand_fit using the product name and brand voice "
        "from the brief. For every image that FAILS, call replace_failed_image(failed_gs_uri=<that "
        "gs_uri>, scene_description=<an improved scene that applies the fix_suggestion and keeps the "
        "product the faithful hero>, aspect_ratio=<the shot's ratio>) — this regenerates AND swaps "
        "the corrected image into the delivered set — then review the returned new_uri. Only once "
        "EVERY image passes, call exit_loop to approve and stop. Finally summarize the QA outcome.\n\n"
        "Brief:\n{brief}\n\nImages:\n{images}"
    ),
    tools=[review_image_brand_fit, replace_failed_image, exit_loop],
    output_key="qa_report",
)

qa_loop_agent = LoopAgent(
    name="qa_loop",
    description="Self-correcting QA loop (review -> regenerate) up to 2 iterations.",
    sub_agents=[brand_qa_agent],
    max_iterations=2,
)
