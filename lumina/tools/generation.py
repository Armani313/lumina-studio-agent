"""Generation tools: marketing copy + on-brand lifestyle image.

generate_lifestyle_image is **image-conditioned**: if a product reference photo is present in
session state (`product_image_uri`), the generated scene depicts that exact product (preserving
shape, label, color), rather than inventing one. Falls back to text->image when absent.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

from google.adk.tools import ToolContext
from google.genai import errors as genai_errors
from google.genai import types

from ..clients import gemini_client
from ..config import settings
from .delivery import mime_for_uri, public_https_url, upload_bytes

# Image generation has a tight per-minute quota; cap concurrency and retry transient 429/503.
_IMG_SEM = threading.Semaphore(int(os.getenv("IMAGE_CONCURRENCY", "3")))


_HERO_TYPES = {"hero", "ecommerce", "packshot"}  # rendered with the higher-quality Pro image model


def _image_generate_with_retry(contents, config, model: str | None = None, attempts: int = 5):
    delay = 8.0
    model = model or settings.model_image
    for i in range(attempts):
        try:
            with _IMG_SEM:
                return gemini_client().models.generate_content(
                    model=model, contents=contents, config=config
                )
        except genai_errors.APIError as e:
            transient = getattr(e, "code", None) in (429, 503, 500) or "RESOURCE_EXHAUSTED" in str(e)
            if transient and i < attempts - 1:
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise


def generate_copy(
    product_name: str,
    key_features: str,
    brand_voice: str,
    channel: str,
    language: str = "English",
    tool_context: ToolContext = None,
) -> dict:
    """Generate channel-ready marketing copy for a product, in the brand's voice.

    Args:
        product_name: The product's name.
        key_features: Comma-separated key features/benefits.
        brand_voice: Short description of brand tone (e.g. 'minimalist, premium, calm').
        channel: Target channel: 'amazon', 'instagram', or 'website'.
        language: Language to write ALL copy in (e.g. 'Russian', 'English') — match the user's brief.

    Returns:
        A dict with keys: title, short, detailed, bullets.
    """
    prompt = (
        f"You are a senior DTC copywriter. Write vivid, SELLING {channel} marketing copy — "
        f"benefit-led and persuasive, never dry.\n"
        f"Write ALL text ENTIRELY in {language} (the user's language).\n"
        f"Brand voice: {brand_voice}\nProduct: {product_name}\nKey features: {key_features}\n"
        "Return STRICT JSON with keys:\n"
        "  title: punchy headline (<=60 chars)\n"
        "  short: concise tagline (<=160 chars)\n"
        "  long: rich 4-5 sentence SEO product description\n"
        "  emotional: 2-3 evocative, feeling-led sentences\n"
        "  bullets: array of 4-5 short benefit bullets\n"
        "  cta: short call-to-action (<=20 chars)\n"
        "  keywords: array of 6-10 SEO keywords\n"
        "  reviews: array of 3 objects {author, rating (integer 1-5), text (1-2 sentences)}"
    )
    resp = gemini_client().models.generate_content(
        model=settings.model_reasoning,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            max_output_tokens=8192,  # multi-variant copy; thinking model needs headroom
        ),
    )
    try:
        result = json.loads(resp.text)
    except Exception:
        result = {"raw": resp.text}
    if not isinstance(result, dict):  # model may return a JSON array
        result = {"raw": result}
    # Persist the FULL multi-variant copy to state so delivery uses it verbatim (the copywriter's
    # text echo can drop fields).
    if tool_context is not None:
        tool_context.state["copy_full"] = result
    return result


_QUALITY = (
    " Professional product photography: studio-grade, intentional lighting; considered composition "
    "and negative space; crisp focus on the product with rich, true-to-life detail and texture; "
    "tasteful color grading; sharp, high-resolution, premium magazine / e-commerce quality. "
    "No text overlays, no watermarks, no distortion."
)


def render_image_bytes(
    scene_description: str,
    aspect_ratio: str = "1:1",
    product_uri: str | None = None,
    model: str | None = None,
):
    """Generate one image and return (bytes, mime), or None. Image-conditioned on product_uri
    when given; `model` selects flash vs the Pro image model. Shared by generate_lifestyle_image
    and the product-card tool."""
    contents: list = []
    if product_uri:
        contents.append(types.Part.from_uri(file_uri=product_uri, mime_type=mime_for_uri(product_uri)))
        instruction = (
            "The attached reference image shows the EXACT product to feature. Reproduce that SAME "
            "product as the clear, prominent hero of a new photo: identical type, shape, color, "
            "material, pattern, embellishments and any text or logo. Do NOT substitute a different "
            "product, restyle it, or leave it out — it must be unmistakably the same item and "
            "clearly visible and in focus. If the product is apparel or shown worn, feature the "
            "garment faithfully (on a suitable model or as a clean flat-lay), preserving its exact "
            f"design and details. Setting/scene: {scene_description}." + _QUALITY
        )
    else:
        instruction = (f"On-brand product photograph. {scene_description}." + _QUALITY)
    contents.append(types.Part(text=instruction))

    resp = _image_generate_with_retry(
        contents,
        types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
        ),
        model=model,
    )
    for cand in resp.candidates or []:
        for part in (cand.content.parts or []):
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                return inline.data, (inline.mime_type or "image/png")
    return None


def render_image_from_prompt(
    prompt: str,
    aspect_ratio: str = "4:5",
    product_uri: str | None = None,
    model: str | None = None,
):
    """Generate one image from a VERBATIM prompt (the caller supplies the full art direction — no
    photography wrapper is added). Attaches the product reference image when given. Returns
    (bytes, mime) or None. Used for fully designed compositions like product cards."""
    contents: list = []
    if product_uri:
        contents.append(types.Part.from_uri(file_uri=product_uri, mime_type=mime_for_uri(product_uri)))
    contents.append(types.Part(text=prompt))
    resp = _image_generate_with_retry(
        contents,
        types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
        ),
        model=model,
    )
    for cand in resp.candidates or []:
        for part in (cand.content.parts or []):
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                return inline.data, (inline.mime_type or "image/png")
    return None


def generate_lifestyle_image(
    scene_description: str,
    aspect_ratio: str = "1:1",
    shot_type: str = "lifestyle",
    tool_context: ToolContext = None,
) -> dict:
    """Generate an on-brand photorealistic image and store it in GCS.

    If a product reference photo exists in session state, the SAME product is depicted in the
    new scene (image-conditioned generation for product fidelity).

    Args:
        scene_description: Vivid description of the scene/setting and product placement.
        aspect_ratio: One of '1:1', '4:5', '9:16', '16:9'.
        shot_type: hero / ecommerce / macro / lifestyle / flatlay / on_model. 'hero' and
            'ecommerce' shots are rendered with the higher-quality Pro image model.

    Returns:
        A dict with 'gs_uri' and 'https_url' of the stored image, or 'error'.
    """
    product_uri = tool_context.state.get("product_image_uri") if tool_context else None
    model = settings.model_image_pro if (shot_type or "").lower() in _HERO_TYPES else settings.model_image
    out = render_image_bytes(scene_description, aspect_ratio, product_uri, model=model)
    if not out:
        return {"error": "no image part returned"}
    data, mime = out
    ext = "png" if "png" in mime else "jpg"
    blob_name = f"images/{uuid.uuid4().hex}.{ext}"
    gs_uri = upload_bytes(data, blob_name, mime)
    return {"gs_uri": gs_uri, "https_url": public_https_url(gs_uri), "mime": mime}
