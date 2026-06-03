"""Delivery stage: assemble the package manifest and report it."""
from google.adk.agents import LlmAgent

from ..config import settings
from ..tools.delivery import write_manifest

delivery_agent = LlmAgent(
    name="delivery",
    model=settings.model_reasoning,
    description="Assembles and delivers the final content-package manifest.",
    instruction=(
        "You are the delivery packager. Assemble a concise JSON package summary containing the "
        "images, the product cards, the video(s), the copy and the QA report below, then call "
        "write_manifest with that JSON string. Finally reply with a short human summary of the "
        "delivered package (counts of images, cards and videos) plus the manifest https_url.\n\n"
        "Images:\n{images}\n\nCards:\n{cards?}\n\nVideos:\n{videos?}\n\nCopy:\n{copy_doc?}\n\nQA report:\n{qa_report?}"
    ),
    tools=[write_manifest],
    output_key="package",
)
