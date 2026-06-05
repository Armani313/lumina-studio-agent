"""Vision tool: structured recognition of the uploaded product.

Classifies the product CATEGORY from the photo and writes a category-specific SHOT STRATEGY into
state, so the planner shoots each product class appropriately (apparel on-model, jewelry macro,
etc.) — grounding the whole pipeline in the real product even with a terse brief.
"""
from __future__ import annotations

import json

from google.adk.tools import ToolContext
from google.genai import types

from ..clients import gemini_client
from ..config import settings
from .delivery import mime_for_uri

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
    "sunglasses": "accessory", "wallet": "accessory",
    "furniture": "home", "decor": "home", "homeware": "home", "candle": "home",
    "clothing": "apparel", "shirt": "apparel", "tshirt": "apparel", "t-shirt": "apparel",
    "dress": "apparel", "snack": "food", "drinkware": "home",
}


def describe_product(tool_context: ToolContext = None) -> dict:
    """Inspect the uploaded product photo: classify its category and produce a precise factual
    description. Writes the category and a category-specific shot strategy into session state so
    downstream agents shoot/write for the REAL product.

    Returns:
        dict with 'category', 'product_description', 'shot_strategy'.
    """
    product_uri = tool_context.state.get("product_image_uri") if tool_context else None
    if not product_uri:
        return {"category": "other", "product_description": "", "note": "no product photo provided"}

    resp = gemini_client().models.generate_content(
        model=settings.model_reasoning,
        contents=[
            types.Part.from_uri(file_uri=product_uri, mime_type=mime_for_uri(product_uri)),
            types.Part(
                text=(
                    "You are a product analyst preparing a marketing shoot. First identify THE single "
                    "product to feature: if it is worn by a person or shown among other items, focus "
                    "on the product itself (e.g. the garment, NOT the model; the watch, NOT the "
                    "wrist); if several products are shown, choose the single most prominent one. "
                    "Return STRICT JSON:\n"
                    "  category: one of [apparel, jewelry, cosmetics, beverage, electronics, "
                    "footwear, accessory, home, food, other]\n"
                    "  product_description: 3-5 factual sentences — exact product type, materials, "
                    "colors, finish, distinctive design details, any visible text/logos, approximate "
                    "scale, and whether it is standalone or worn. Describe ONLY what you actually "
                    "see; do not invent features or a brand story.\n"
                    "  suggested_settings: 2-3 short, product-appropriate settings / props / moods "
                    "for marketing imagery of THIS specific product."
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

    if tool_context is not None:
        tool_context.state["product_category"] = category
        tool_context.state["shot_strategy"] = strategy
        tool_context.state["suggested_settings"] = settings_hint

    return {
        "category": category,
        "product_description": description,
        "shot_strategy": strategy,
        "suggested_settings": settings_hint,
    }
