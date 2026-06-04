"""Product-card tool: generate an on-brand background (image-conditioned on the product) and
composite a STYLED, selling marketing card with Pillow (image models garble text, so text is
rendered crisply on top): gradient scrim, accent color sampled from the image, bold wrapped
headline, accent-dotted bullets and a call-to-action pill."""
from __future__ import annotations

import uuid
from io import BytesIO

from google.adk.tools import ToolContext
from PIL import Image, ImageDraw, ImageFont

from .delivery import public_https_url, upload_bytes
from .generation import render_image_bytes

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
    """Composite the styled card text/CTA over a generated background. Returns PNG bytes."""
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
    """Create a styled, high-converting marketplace product-card image.

    Args:
        headline: Punchy headline (<=42 chars).
        subtext: One supporting line (<=90 chars).
        bullets: 3-4 short benefit bullets.
        brand: Brand wordmark to show.
        bg_scene_description: Background scene; leave clean negative space (lower third) for text.
        cta: Call-to-action shown in the button pill (<=20 chars).
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
    png = _compose_card(out[0], headline, subtext, bullets, brand, cta)
    gs_uri = upload_bytes(png, f"cards/{uuid.uuid4().hex}.png", "image/png")
    return {"gs_uri": gs_uri, "https_url": public_https_url(gs_uri)}
