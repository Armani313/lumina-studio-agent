"""Product-card tool: an AI-DESIGNED marketing card.

The image model (Nano Banana Pro) designs the WHOLE card — product hero, typography, layout and
palette matched to the product's identity and audience (playful for kids, elegant for luxury,
sleek for tech, …) — so cards stop looking like one templated font stamped on a photo. A vision
check guards against garbled text; a Pillow-composited card is the legible fallback.
"""
from __future__ import annotations

import json
import uuid
from io import BytesIO

from google.adk.tools import ToolContext
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

from ..clients import gemini_client
from ..config import settings
from .delivery import public_https_url, upload_bytes
from .generation import render_image_bytes, render_image_from_prompt

# Per-category design language — a strong prior; the model further adapts to the actual product.
_CARD_STYLES = {
    "apparel": "fashion-editorial — chic modern or high-contrast type, magazine-cover composition, confident negative space, aspirational",
    "jewelry": "luxury editorial — delicate thin serif or refined sans, generous negative space, soft premium palette, quiet elegance",
    "cosmetics": "clean beauty editorial — soft elegant serif/sans, airy neutral or pastel palette, spa-calm, premium minimal",
    "beverage": "fresh & vibrant — bold friendly type, lively appetizing colors, dynamic energetic layout",
    "electronics": "sleek tech — clean geometric sans, high-contrast dark or minimal background, sharp modern premium-gadget feel",
    "footwear": "sporty streetwear — bold condensed/heavy type, dynamic diagonal energy, high-impact and youthful",
    "accessory": "premium fashion accessory — elegant modern type, clean stylish tasteful composition",
    "home": "warm lifestyle — cozy elegant serif or humanist sans, natural inviting palette, editorial-interior feel",
    "food": "appetizing & warm — bold inviting type, rich fresh colors, mouth-watering dynamic styling",
    "other": "clean premium commercial design — tasteful modern typography matched to the product, balanced and high-end",
}

_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
]
_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def _font(size: int, bold: bool = False):
    for path in (_BOLD if bold else _REGULAR) + _REGULAR:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _accent(img: Image.Image) -> tuple[int, int, int]:
    """Pick a vivid accent color from the image; fall back to a warm terracotta."""
    small = img.convert("RGB").resize((64, 64))
    best, best_score = None, -1.0
    for count, (r, g, b) in (small.getcolors(64 * 64) or []):
        mx, mn = max(r, g, b), min(r, g, b)
        sat, val = mx - mn, mx
        if val < 45 or (mx > 235 and sat < 28):  # skip near-black / near-white
            continue
        score = sat * 1.0 + val * 0.15 + count * 0.02
        if score > best_score:
            best_score, best = score, (r, g, b)
    return best or (193, 123, 74)


def _wrap(d: ImageDraw.ImageDraw, text: str, font, x: int, y: int, maxw: int, fill, gap: int) -> int:
    line = ""
    for word in (text or "").split():
        trial = (line + " " + word).strip()
        if d.textlength(trial, font=font) <= maxw or not line:
            line = trial
        else:
            d.text((x, y), line, font=font, fill=fill)
            y += font.size + gap
            line = word
    if line:
        d.text((x, y), line, font=font, fill=fill)
        y += font.size + gap
    return y


def _compose_card(
    data: bytes, headline: str, subtext: str, bullets: list[str], brand: str, cta: str
) -> bytes:
    """Composite styled card text/CTA over a generated background (legible fallback). Returns PNG."""
    img = Image.open(BytesIO(data)).convert("RGBA")
    W, H = img.size
    accent = _accent(img)

    # Gradient scrim (transparent -> dark) over the lower portion for legibility + depth.
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    start = int(H * 0.42)
    for yy in range(start, H):
        a = int(232 * ((yy - start) / max(1, H - start)) ** 0.85)
        sd.line([(0, yy), (W, yy)], fill=(16, 14, 13, a))
    img = Image.alpha_composite(img, scrim)
    d = ImageDraw.Draw(img)

    margin = int(W * 0.065)
    white = (255, 255, 255, 255)
    soft = (248, 246, 244, 224)

    # Brand wordmark (letter-spaced) + accent rule, top-left.
    d.text((margin, int(H * 0.05)), " ".join((brand or "").upper()), font=_font(int(W * 0.030), bold=True), fill=white)
    ry = int(H * 0.05) + int(W * 0.052)
    d.line([(margin, ry), (margin + int(W * 0.11), ry)], fill=accent + (255,), width=max(2, int(W * 0.007)))

    # Text block (bottom).
    x, y = margin, int(H * 0.49)
    y = _wrap(d, headline, _font(int(W * 0.064), bold=True), x, y, W - 2 * margin, white, gap=int(W * 0.012))
    y += int(W * 0.012)
    y = _wrap(d, subtext, _font(int(W * 0.031)), x, y, W - 2 * margin, soft, gap=int(W * 0.010))
    y += int(W * 0.022)
    bf = _font(int(W * 0.030))
    for b in (bullets or [])[:4]:
        r = int(W * 0.011)
        cy = y + int(bf.size * 0.55)
        d.ellipse([x, cy - r, x + 2 * r, cy + r], fill=accent + (255,))
        d.text((x + 4 * r, y), b, font=bf, fill=soft)
        y += bf.size + int(W * 0.020)

    # CTA pill.
    y += int(W * 0.018)
    cf = _font(int(W * 0.034), bold=True)
    cw = d.textlength(cta or "", font=cf)
    ph = int(W * 0.078)
    pw = int(cw + W * 0.11)
    d.rounded_rectangle([x, y, x + pw, y + ph], radius=ph // 2, fill=accent + (255,))
    d.text((x + (pw - cw) / 2, y + (ph - cf.size) / 2 - int(W * 0.004)), cta or "", font=cf, fill=white)

    composite = img.convert("RGB")
    buf = BytesIO()
    composite.save(buf, format="PNG")
    return buf.getvalue()


def _card_prompt(headline, subtext, cta, brand, scene, style, description, brand_voice, language) -> str:
    lines = []
    if brand:
        lines.append(f"- Brand wordmark (small, refined): {brand}")
    lines.append(f"- Headline (largest, dominant): {headline}")
    if subtext:
        lines.append(f"- Supporting line (smaller): {subtext}")
    if cta:
        lines.append(f"- Call-to-action inside a button/pill: {cta}")
    return (
        "Design ONE finished, high-converting MARKETING PRODUCT CARD — a polished advertising / "
        "social-ad creative (a graphic design, NOT a plain photo) featuring the attached product as "
        "the hero, kept EXACTLY as in the reference (same shape, color, materials, details, any "
        "logo).\n"
        f"PRODUCT: {description or 'see the reference image'}\n"
        f"CATEGORY STYLE (starting point): {style}.\n"
        f"BRAND VOICE: {brand_voice or 'premium, tasteful'}.\n"
        "CRUCIAL — adapt the ENTIRE design (typography choice, colors, shapes, mood, layout) to "
        "THIS product's identity and target audience as seen in the reference: e.g. a children's / "
        "toy product → playful, fun, rounded, colorful, friendly type; a luxury item → minimal, "
        "elegant, refined; a sport/tech product → bold, modern, energetic. Pick fonts that genuinely "
        "suit the product — do NOT default to a generic font.\n"
        f"Background / scene: {scene}.\n"
        "Render EXACTLY the following text — spelled PERFECTLY with real, legible letterforms, a "
        "clear visual hierarchy, and NO lorem/placeholder/extra text, NO misspellings or random "
        "characters:\n"
        f"{chr(10).join(lines)}\n"
        f"Write ALL of this text in {language}. Professional balanced composition with intentional "
        "negative space; premium magazine / e-commerce quality; high resolution; no watermark."
    )


def _verify_headline(card_bytes: bytes, headline: str) -> bool:
    """True if the headline is rendered cleanly. Lenient — fails only on clear garbling, so a flaky
    check never needlessly downgrades a good AI card to the fallback."""
    if not headline:
        return True
    try:
        resp = gemini_client().models.generate_content(
            model=settings.model_reasoning,
            contents=[
                types.Part.from_bytes(data=card_bytes, mime_type="image/png"),
                types.Part(text=(
                    f'This is a marketing card. Its main headline should read exactly: "{headline}". '
                    "Is that headline present and correctly spelled, with real and legible letters? "
                    "Minor styling/kerning is fine; answer false ONLY if it is clearly garbled, "
                    "misspelled, has random/extra characters, or is unreadable. "
                    'Return STRICT JSON: {"ok": true|false}.'
                )),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", max_output_tokens=128
            ),
        )
        r = json.loads(resp.text)
        return bool(isinstance(r, dict) and r.get("ok", True))
    except Exception:
        return True  # never block delivery on a flaky check


def make_product_card(
    headline: str,
    subtext: str,
    bullets: list[str],
    brand: str,
    bg_scene_description: str,
    cta: str = "Shop now",
    aspect_ratio: str = "4:5",
    tool_context: ToolContext = None,
) -> dict:
    """Create a styled, high-converting marketplace product card.

    The card is DESIGNED by the image model end-to-end (product hero + typography + layout + palette
    matched to the product's identity/audience), so it adapts per product (playful for kids, elegant
    for luxury, etc.). A vision check guards the headline against garbling; if it fails twice, a
    Pillow-composited card (crisp text over a generated background) is delivered as a legible fallback.

    Args:
        headline: Punchy headline (<=42 chars).
        subtext: One supporting line (<=90 chars).
        bullets: 3-4 short benefit bullets (used only by the fallback layout).
        brand: Brand wordmark to show.
        bg_scene_description: Background scene/setting for the card.
        cta: Call-to-action shown in the button pill (<=20 chars).
        aspect_ratio: '1:1', '4:5', or '9:16'.

    Returns:
        dict with 'gs_uri' and 'https_url' of the card, or 'error'.
    """
    state = tool_context.state if tool_context else {}
    product_uri = state.get("product_image_uri") if tool_context else None
    category = state.get("product_category") if tool_context else None
    category = category if category in _CARD_STYLES else "other"
    description = (state.get("product_description") if tool_context else "") or ""
    brief = state.get("brief") if tool_context else None
    brief = brief if isinstance(brief, dict) else {}
    brand_voice = brief.get("brand_voice") or ""
    language = brief.get("language") or "the product's language"

    prompt = _card_prompt(
        headline, subtext, cta, brand, bg_scene_description,
        _CARD_STYLES[category], description, brand_voice, language,
    )

    # Try an AI-designed card (Pro model for text fidelity); verify the headline; one retry.
    for _ in range(2):
        out = render_image_from_prompt(
            prompt, aspect_ratio, product_uri, model=settings.model_image_pro
        )
        if out and _verify_headline(out[0], headline):
            gs_uri = upload_bytes(out[0], f"cards/{uuid.uuid4().hex}.png", "image/png")
            return {"gs_uri": gs_uri, "https_url": public_https_url(gs_uri), "design": "ai"}

    # Fallback: Pillow-composited card (guaranteed legible) over a generated background.
    scene = (
        f"{bg_scene_description}. Feature the product prominently and faithfully in the upper "
        "two-thirds; keep clean negative space in the lower third for text; minimal, premium, on-brand."
    )
    out = render_image_bytes(scene, aspect_ratio, product_uri)
    if not out:
        return {"error": "card generation failed"}
    png = _compose_card(out[0], headline, subtext, bullets, brand, cta)
    gs_uri = upload_bytes(png, f"cards/{uuid.uuid4().hex}.png", "image/png")
    return {"gs_uri": gs_uri, "https_url": public_https_url(gs_uri), "design": "composite"}
