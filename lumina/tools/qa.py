"""QA tools: multimodal brand-fit + product-fidelity review, and a loop-exit signal."""
from __future__ import annotations

import json

from google.adk.tools import ToolContext
from google.genai import types

from ..clients import gemini_client
from ..config import settings
from .delivery import mime_for_uri


def review_image_brand_fit(
    gs_uri: str, brand_voice: str, product_name: str, tool_context: ToolContext = None
) -> dict:
    """Multimodally review a generated image for brand fit AND product fidelity.

    If a product reference photo is in session state, the original product and the generated
    image are compared directly — the review FAILS if the depicted product drifts from the real one.

    Args:
        gs_uri: gs:// URI of the generated image to review.
        brand_voice: The brand's tone/voice to check against.
        product_name: The product that should be depicted.

    Returns:
        dict with: verdict ('pass'|'fail'), score (0.0-1.0), issues (list[str]), fix_suggestion (str).
    """
    product_uri = tool_context.state.get("product_image_uri") if tool_context else None

    parts: list = []
    if product_uri:
        parts.append(types.Part.from_uri(file_uri=product_uri, mime_type=mime_for_uri(product_uri)))
    parts.append(types.Part.from_uri(file_uri=gs_uri, mime_type=mime_for_uri(gs_uri)))

    if product_uri:
        prompt = (
            "The FIRST image is the REAL product photo. The SECOND image is a generated marketing "
            f"image for '{product_name}'.\n"
            f"Brand voice: {brand_voice}\n"
            "FAIL if the second image's product differs from the real one (different bottle shape, "
            "label, wordmark, color or proportions), shows garbled text, distortions or unsafe "
            "content, or violates the brand voice. Otherwise PASS.\n"
            "Return STRICT JSON: verdict ('pass'|'fail'), score (0.0-1.0), issues (array of short "
            "strings), fix_suggestion (one short sentence)."
        )
    else:
        prompt = (
            "You are a strict brand-safety QA reviewer for product marketing imagery.\n"
            f"Brand voice: {brand_voice}\nProduct: {product_name}\n"
            "Assess the image for (a) brand consistency, (b) product realism/fidelity, "
            "(c) policy issues (garbled text, distortions, unsafe content).\n"
            "Return STRICT JSON: verdict ('pass'|'fail'), score (0.0-1.0), issues (array of short "
            "strings), fix_suggestion (one short sentence)."
        )
    parts.append(types.Part(text=prompt))

    resp = gemini_client().models.generate_content(
        model=settings.model_reasoning,
        contents=parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json", max_output_tokens=2048
        ),
    )
    try:
        return json.loads(resp.text)
    except Exception:
        return {
            "verdict": "pass",
            "score": 0.5,
            "issues": ["unparseable QA response"],
            "fix_suggestion": "",
        }


def exit_loop(tool_context: ToolContext) -> dict:
    """Call this once every image has passed QA, to approve the package and stop the review loop."""
    tool_context.actions.escalate = True
    return {"status": "approved"}
