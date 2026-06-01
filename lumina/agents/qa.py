"""Self-correcting QA stage: a LoopAgent that reviews images and regenerates failures."""
from google.adk.agents import LlmAgent, LoopAgent

from ..config import settings
from ..tools.generation import generate_lifestyle_image
from ..tools.qa import exit_loop, review_image_brand_fit

brand_qa_agent = LlmAgent(
    name="brand_qa",
    model=settings.model_reasoning,
    description="Reviews produced images for brand fit/fidelity and regenerates failures.",
    instruction=(
        "You are Brand-Safety QA. The produced images (JSON containing gs_uri values) are "
        "below. For each image gs_uri, call review_image_brand_fit using the product name and "
        "brand voice from the brief. If EVERY image passes (verdict 'pass'), call exit_loop to "
        "approve and stop. If any image fails, call generate_lifestyle_image once to regenerate "
        "that shot applying the fix_suggestion, then briefly summarize what you changed.\n\n"
        "Brief:\n{brief}\n\nImages:\n{images}"
    ),
    tools=[review_image_brand_fit, generate_lifestyle_image, exit_loop],
    output_key="qa_report",
)

qa_loop_agent = LoopAgent(
    name="qa_loop",
    description="Self-correcting QA loop (review -> regenerate) up to 2 iterations.",
    sub_agents=[brand_qa_agent],
    max_iterations=2,
)
