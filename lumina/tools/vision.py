"""Vision tool: describe the ACTUAL uploaded product from its photo, so the whole pipeline is
grounded in the real product even when the user's text brief is terse."""
from __future__ import annotations

from google.adk.tools import ToolContext
from google.genai import types

from ..clients import gemini_client
from ..config import settings
from .delivery import mime_for_uri


def describe_product(tool_context: ToolContext = None) -> dict:
    """Inspect the uploaded product photo and return a precise, factual description of the real
    product (type/category, materials, colors, distinctive design details, any visible text/logos,
    standalone vs worn). Use this so the brief, scenes and copy match what is actually pictured.

    Returns:
        dict with 'product_description'.
    """
    product_uri = tool_context.state.get("product_image_uri") if tool_context else None
    if not product_uri:
        return {"product_description": "", "note": "no product photo provided"}
    resp = gemini_client().models.generate_content(
        model=settings.model_reasoning,
        contents=[
            types.Part.from_uri(file_uri=product_uri, mime_type=mime_for_uri(product_uri)),
            types.Part(
                text=(
                    "Describe ONLY what you actually see in this product photo, for a marketing "
                    "brief: the product type/category, materials, colors, distinctive design "
                    "details, any visible text or logos, and whether it is shown standalone or "
                    "worn. Be concrete and factual in 2-4 sentences. Do NOT invent a brand story "
                    "or features that are not visible."
                )
            ),
        ],
        config=types.GenerateContentConfig(max_output_tokens=2048),
    )
    return {"product_description": (resp.text or "").strip()}
