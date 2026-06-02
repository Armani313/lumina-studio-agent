"""Product-card tool: generate an on-brand background (image-conditioned on the product) and
overlay CRISP marketing text with Pillow (image models garble text, so text is composited)."""
from __future__ import annotations

import uuid
from io import BytesIO

from google.adk.tools import ToolContext
from PIL import Image, ImageDraw, ImageFont

from .delivery import public_https_url, upload_bytes
from .generation import render_image_bytes

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size: int):
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_product_card(
    headline: str,
    subtext: str,
    bullets: list[str],
    brand: str,
    bg_scene_description: str,
    aspect_ratio: str = "4:5",
    tool_context: ToolContext = None,
) -> dict:
    """Create a marketplace product-card image: an on-brand product background with crisp,
    composited marketing text.

    Args:
        headline: Short punchy headline (<=42 chars).
        subtext: One supporting line (<=90 chars).
        bullets: 3 short feature bullets.
        brand: Brand wordmark to show.
        bg_scene_description: Background scene; should leave clean negative space for text.
        aspect_ratio: '1:1', '4:5', or '9:16'.

    Returns:
        dict with 'gs_uri' and 'https_url' of the composited card, or 'error'.
    """
    product_uri = tool_context.state.get("product_image_uri") if tool_context else None
    scene = (
        f"{bg_scene_description}. Feature the product prominently and faithfully in the upper "
        "two-thirds; keep clean negative space in the lower third for text; minimal, premium, "
        "on-brand."
    )
    out = render_image_bytes(scene, aspect_ratio, product_uri)
    if not out:
        return {"error": "background generation failed"}
    data, _ = out

    img = Image.open(BytesIO(data)).convert("RGBA")
    W, H = img.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # translucent bone-white panel across the lower portion for legibility
    panel_top = int(H * 0.62)
    d.rectangle([0, panel_top, W, H], fill=(244, 241, 236, 214))

    ink = (43, 42, 40, 255)
    margin = int(W * 0.06)
    y = panel_top + int(H * 0.03)
    d.text((margin, y), headline, font=_font(int(W * 0.058)), fill=ink)
    y += int(W * 0.085)
    d.text((margin, y), subtext, font=_font(int(W * 0.030)), fill=(43, 42, 40, 230))
    y += int(W * 0.058)
    for b in (bullets or [])[:3]:
        d.text((margin, y), "•  " + b, font=_font(int(W * 0.028)), fill=(43, 42, 40, 230))
        y += int(W * 0.044)

    # brand wordmark, top-left
    d.text((margin, int(H * 0.05)), brand.upper(), font=_font(int(W * 0.044)), fill=(255, 255, 255, 235))

    composite = Image.alpha_composite(img, overlay).convert("RGB")
    buf = BytesIO()
    composite.save(buf, format="PNG")

    blob_name = f"cards/{uuid.uuid4().hex}.png"
    gs_uri = upload_bytes(buf.getvalue(), blob_name, "image/png")
    return {"gs_uri": gs_uri, "https_url": public_https_url(gs_uri)}
