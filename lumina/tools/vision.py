"""Vision tool: brief-guided structured recognition of the uploaded product.

Picks WHICH item in the photo to feature using the user's brief (e.g. brief 'tie' on a photo of a
suited man → the TIE, not the jacket), classifies its CATEGORY, writes a category-specific SHOT
STRATEGY into state, AND crops the photo to the chosen product so every downstream generator
(images, cards, video, QA) conditions on the RIGHT object — not whatever is largest in the frame.
"""
from __future__ import annotations

import io
import json
import uuid

from google.adk.tools import ToolContext
from google.genai import types
from PIL import Image

from ..clients import gcs_bucket, gemini_client
from ..config import settings
from .delivery import mime_for_uri, upload_bytes

# Category -> how to shoot that product class (compositions + which shot types + ratios).
SHOT_STRATEGIES = {
    "apparel": "Show the garment on a suitable model in lifestyle settings AND as a clean flat-lay; "
    "vary angles to convey fit, fabric and details. Favor 4:5 and 1:1.",
    "jewelry": "Use macro close-ups on textured natural surfaces (stone, wood, linen) AND on-body / "
    "on-hand shots; emphasize material, finish and craftsmanship. Favor 1:1 and 4:5.",
    "cosmetics": "Place the product on a natural surface with minimal props; include a texture / "
    "swatch / dropper detail shot; soft daylight. Favor 4:5 and 1:1.",
    "beverage": "Show the product on a surface with condensation/garnish AND a lifestyle in-hand or "
    "pour shot; fresh, appetizing light. Favor 4:5 and 9:16.",
    "electronics": "Hero shot on a minimal surface + a detail macro + an in-use/in-context shot; "
    "clean, modern. Favor 1:1 and 16:9.",
    "footwear": "On-foot lifestyle shots AND a clean studio pair; vary angles to show silhouette and "
    "material. Favor 4:5 and 1:1.",
    "accessory": "On-model/styled shots AND a clean flat-lay; show scale and detail. Favor 4:5 and 1:1.",
    "home": "In-room lifestyle context AND a detail shot; show scale and texture. Favor 4:5 and 16:9.",
    "food": "Appetizing plated/styled shots AND an ingredient or lifestyle shot; warm natural light. "
    "Favor 1:1 and 4:5.",
    "other": "Feature the product prominently as the clear hero across varied, complementary "
    "settings; show its key details. Favor 4:5 and 1:1.",
}

_ALIASES = {
    "skincare": "cosmetics", "beauty": "cosmetics", "makeup": "cosmetics", "fragrance": "cosmetics",
    "ring": "jewelry", "rings": "jewelry", "watch": "jewelry", "necklace": "jewelry",
    "earrings": "jewelry", "bracelet": "jewelry",
    "drink": "beverage", "bottle": "beverage", "wine": "beverage", "coffee": "beverage",
    "gadget": "electronics", "tech": "electronics", "phone": "electronics", "headphones": "electronics",
    "shoes": "footwear", "sneakers": "footwear", "boots": "footwear",
    "bag": "accessory", "handbag": "accessory", "belt": "accessory", "hat": "accessory",
    "sunglasses": "accessory", "wallet": "accessory", "tie": "accessory", "necktie": "accessory",
    "scarf": "accessory", "gloves": "accessory",
    "furniture": "home", "decor": "home", "homeware": "home", "candle": "home",
    "clothing": "apparel", "shirt": "apparel", "tshirt": "apparel", "t-shirt": "apparel",
    "jacket": "apparel", "blazer": "apparel", "suit": "apparel", "coat": "apparel",
    "dress": "apparel", "snack": "food", "drinkware": "home",
}


def _download(gs_uri: str) -> bytes | None:
    prefix = f"gs://{settings.gcs_bucket}/"
    if not gs_uri.startswith(prefix):
        return None
    try:
        return gcs_bucket().blob(gs_uri[len(prefix):]).download_as_bytes()
    except Exception:
        return None


def _crop_to_product(product_uri: str, bbox) -> str | None:
    """Crop the photo to the chosen product's box ([ymin,xmin,ymax,xmax], normalized 0-1000) and
    upload the crop, so downstream generators condition on THAT object. Returns the crop's gs:// URI,
    or None to keep the original (invalid/near-full-frame box, or any failure)."""
    try:
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            return None
        ymin, xmin, ymax, xmax = (float(v) for v in bbox)
        if not (0 <= xmin < xmax <= 1000 and 0 <= ymin < ymax <= 1000):
            return None
        # If the box already fills most of the frame, the product IS the photo — don't crop.
        if ((xmax - xmin) / 1000.0) * ((ymax - ymin) / 1000.0) > 0.82:
            return None
        data = _download(product_uri)
        if not data:
            return None
        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        px0, py0, px1, py1 = xmin / 1000 * w, ymin / 1000 * h, xmax / 1000 * w, ymax / 1000 * h
        pad_x, pad_y = (px1 - px0) * 0.08, (py1 - py0) * 0.08  # a little context around the product
        box = (
            max(0, int(px0 - pad_x)), max(0, int(py0 - pad_y)),
            min(w, int(px1 + pad_x)), min(h, int(py1 + pad_y)),
        )
        if box[2] - box[0] < 16 or box[3] - box[1] < 16:
            return None
        buf = io.BytesIO()
        img.crop(box).save(buf, format="PNG")
        return upload_bytes(buf.getvalue(), f"inputs/crop_{uuid.uuid4().hex}.png", "image/png")
    except Exception:
        return None


def describe_product(brief: str = "", tool_context: ToolContext = None) -> dict:
    """Inspect the uploaded product photo and decide WHICH item to feature using the user's brief.

    If the brief names a specific item (e.g. 'tie'), that item is featured even when it is not the
    largest object in the photo. Classifies the product's category, writes a category-specific shot
    strategy into state, and crops the photo to the chosen product so all downstream generation
    conditions on the right object.

    Args:
        brief: The user's order brief / description (tells us which item in the photo to feature).

    Returns:
        dict with 'category', 'product_description', 'shot_strategy', 'suggested_settings'.
    """
    product_uri = tool_context.state.get("product_image_uri") if tool_context else None
    if not product_uri:
        return {"category": "other", "product_description": "", "note": "no product photo provided"}

    # Idempotent: if the consultation phase already analysed (and cropped) the product, reuse it —
    # don't re-run vision or (worse) re-crop the already-cropped image.
    if tool_context is not None and tool_context.state.get("vision_done"):
        return {
            "category": tool_context.state.get("product_category", "other"),
            "product_description": tool_context.state.get("product_description", ""),
            "shot_strategy": tool_context.state.get("shot_strategy", ""),
            "suggested_settings": tool_context.state.get("suggested_settings", ""),
            "focused_crop": bool(tool_context.state.get("product_image_uri_original")),
            "cached": True,
        }

    brief = (brief or "").strip()
    if not brief and tool_context is not None:
        brief = str(tool_context.state.get("brief_text") or "").strip()
    brief_line = f'The user\'s order brief says: "{brief}".\n' if brief else ""

    resp = gemini_client().models.generate_content(
        model=settings.model_reasoning,
        contents=[
            types.Part.from_uri(file_uri=product_uri, mime_type=mime_for_uri(product_uri)),
            types.Part(
                text=(
                    "You are a product analyst preparing a marketing shoot.\n"
                    + brief_line +
                    "Identify THE single product to feature, then describe it.\n"
                    "CHOOSING THE PRODUCT: If the brief names or describes a specific item (e.g. "
                    "'tie', 'watch', 'the belt'), you MUST feature THAT exact item — even if other "
                    "items in the photo are larger or more prominent. Example: brief 'tie' on a "
                    "photo of a man in a suit means feature the TIE, not the jacket. Only if the "
                    "brief gives no usable product hint, fall back to the single most prominent "
                    "product. Always focus on the product itself, never the person wearing it (the "
                    "garment/accessory, NOT the model; the watch, NOT the wrist).\n"
                    "Return STRICT JSON:\n"
                    "  category: one of [apparel, jewelry, cosmetics, beverage, electronics, "
                    "footwear, accessory, home, food, other]\n"
                    "  product_description: 3-5 factual sentences — exact product type, materials, "
                    "colors, finish, distinctive design details, any visible text/logos, approximate "
                    "scale. Describe ONLY the chosen product and what you actually see; do not invent "
                    "features or a brand story.\n"
                    "  suggested_settings: 2-3 short, product-appropriate settings / props / moods "
                    "for marketing imagery of THIS specific product.\n"
                    "  bounding_box: the chosen product's TIGHT bounding box in the photo as "
                    "[ymin, xmin, ymax, xmax], each an integer 0-1000 (normalized to image size). "
                    "Box ONLY the product, excluding the person and background."
                )
            ),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json", max_output_tokens=3072
        ),
    )
    try:
        spec = json.loads(resp.text)
    except Exception:
        spec = {"category": "other", "product_description": (resp.text or "").strip()}
    if not isinstance(spec, dict):  # model may return a JSON array
        spec = {"category": "other", "product_description": str(spec)[:600]}

    category = str(spec.get("category") or "other").strip().lower()
    category = _ALIASES.get(category, category)
    if category not in SHOT_STRATEGIES:
        category = "other"
    strategy = SHOT_STRATEGIES[category]
    description = (spec.get("product_description") or "").strip()
    settings_hint = spec.get("suggested_settings")
    if isinstance(settings_hint, list):
        settings_hint = "; ".join(str(s) for s in settings_hint)
    settings_hint = (settings_hint or "").strip()

    focused = False
    if tool_context is not None:
        crop_uri = _crop_to_product(product_uri, spec.get("bounding_box"))
        if crop_uri:
            # Re-point the whole pipeline at the chosen product; keep the original for reference.
            tool_context.state["product_image_uri_original"] = product_uri
            tool_context.state["product_image_uri"] = crop_uri
            focused = True
        tool_context.state["product_category"] = category
        tool_context.state["shot_strategy"] = strategy
        tool_context.state["suggested_settings"] = settings_hint
        tool_context.state["product_description"] = description
        tool_context.state["vision_done"] = True

    return {
        "category": category,
        "product_description": description,
        "shot_strategy": strategy,
        "suggested_settings": settings_hint,
        "focused_crop": focused,
    }
