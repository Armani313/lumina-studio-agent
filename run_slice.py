"""Local end-to-end run of the pipeline with a PRODUCT PHOTO as input (fidelity mode).

Uploads a product photo to GCS, seeds it into session state as `product_image_uri` (so the
image + QA tools condition on the real product), then runs the full graph.

Requires ADC:
    gcloud auth application-default login
    gcloud auth application-default set-quota-project aifreelance-hackathon

Run:
    .venv/bin/python run_slice.py [path/to/product_photo.png]
"""
import asyncio
import os
import sys

from google.genai import types
from google.adk.runners import InMemoryRunner

from lumina.agent import root_agent
from lumina.tools.delivery import mime_for_uri, public_https_url, upload_bytes

# Stand-in product photo (a previously generated clean Aurelia bottle). Replace with a real
# product photo path as the first CLI arg.
PRODUCT_PHOTO = sys.argv[1] if len(sys.argv) > 1 else "outputs/grounded_4x5.png"

DESCRIPTION = os.getenv("BRIEF") or (
    "Brand: 'Aurelia' — minimalist premium skincare; calm, clinical tone; earthy neutral palette. "
    "Product (see the uploaded product photo): 'Aurelia Glow Serum', a vitamin-C serum in a frosted "
    "glass dropper bottle. Features: brightening, lightweight, fragrance-free. Channel: instagram. "
    "Generate on-brand lifestyle imagery featuring this exact product."
)


async def main() -> None:
    with open(PRODUCT_PHOTO, "rb") as f:
        data = f.read()
    product_uri = upload_bytes(data, "inputs/product_reference.png", mime_for_uri(PRODUCT_PHOTO))
    print("product photo ->", product_uri, "(", public_https_url(product_uri), ")")

    runner = InMemoryRunner(agent=root_agent, app_name="lumina")
    session = await runner.session_service.create_session(
        app_name="lumina", user_id="demo",
        state={"product_image_uri": product_uri, "brief_text": DESCRIPTION},
    )
    message = types.Content(role="user", parts=[types.Part(text=DESCRIPTION)])

    async for event in runner.run_async(
        user_id="demo", session_id=session.id, new_message=message
    ):
        author = getattr(event, "author", "?")
        if event.content and event.content.parts:
            for p in event.content.parts:
                if getattr(p, "text", None):
                    print(f"\n[{author}] {p.text[:320]}")
                if getattr(p, "function_call", None):
                    print(f"[{author}] -> tool: {p.function_call.name}")
                if getattr(p, "function_response", None):
                    print(f"[{author}] <- {p.function_response.name}: {str(p.function_response.response)[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
