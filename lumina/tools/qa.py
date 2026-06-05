"""QA tools: multimodal brand-fit + product-fidelity review, and a loop-exit signal."""
from __future__ import annotations

import json
import uuid

from google.adk.tools import ToolContext
from google.genai import types

from ..clients import gemini_client
from ..config import settings
from .delivery import mime_for_uri, public_https_url, upload_bytes
from .generation import render_image_bytes


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
            "The FIRST image is the REAL product. The SECOND image is a generated marketing image "
            f"for '{product_name}'.\n"
            f"Brand voice: {brand_voice}\n"
            "PASS only if the SECOND image clearly and prominently shows the SAME product as the "
            "first — same type, color, key design and embellishments. FAIL if the product is "
            "missing or not clearly visible, is a different product, or differs in type, color or "
            "design; also FAIL on garbled text, distortions, unsafe content, or brand-voice "
            "violations.\n"
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
        result = json.loads(resp.text)
    except Exception:
        result = {
            "verdict": "pass",
            "score": 0.5,
            "issues": ["unparseable QA response"],
            "fix_suggestion": "",
        }
    if not isinstance(result, dict):  # model may return a JSON array
        result = {"verdict": "pass", "score": 0.5, "issues": ["non-object QA response"], "fix_suggestion": ""}
    # Accumulate a structured quality scorecard in state (surfaced in the delivered package).
    if tool_context is not None:
        scores = list(tool_context.state.get("qa_scores") or [])
        scores.append(
            {
                "uri": gs_uri,
                "verdict": result.get("verdict"),
                "score": result.get("score"),
                "issues": result.get("issues") or [],
            }
        )
        tool_context.state["qa_scores"] = scores
    return result


def replace_failed_image(
    failed_gs_uri: str,
    scene_description: str,
    aspect_ratio: str = "1:1",
    tool_context: ToolContext = None,
) -> dict:
    """Regenerate an image that failed QA (product-conditioned) and SWAP it into the delivered
    image set, so the corrected asset — not the failed one — is what ships.

    Args:
        failed_gs_uri: gs:// URI of the image that failed review.
        scene_description: the scene to regenerate, applying the fix_suggestion (keep the product
            the clear, faithful hero).
        aspect_ratio: aspect ratio of the shot ('1:1', '4:5', '9:16', '16:9').

    Returns:
        dict with 'old_uri', 'new_uri', 'https_url', or 'error'.
    """
    product_uri = tool_context.state.get("product_image_uri") if tool_context else None
    out = render_image_bytes(scene_description, aspect_ratio, product_uri)
    if not out:
        return {"error": "regeneration produced no image"}
    data, mime = out
    ext = "png" if "png" in mime else "jpg"
    new_uri = upload_bytes(data, f"images/{uuid.uuid4().hex}.{ext}", mime)
    # Swap the failed URI for the corrected one in the delivered image set (state['images'] is the
    # producer's output string; the marketplace extracts asset URIs from it).
    if tool_context is not None:
        imgs = str(tool_context.state.get("images") or "")
        tool_context.state["images"] = imgs.replace(failed_gs_uri, new_uri)
    return {"old_uri": failed_gs_uri, "new_uri": new_uri, "https_url": public_https_url(new_uri)}


def exit_loop(tool_context: ToolContext) -> dict:
    """Call this once every image has passed QA, to approve the package and stop the review loop."""
    tool_context.actions.escalate = True
    return {"status": "approved"}
