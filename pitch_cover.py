"""One-off: project cover / YouTube thumbnail. NOT deployed.

Background via gemini-3-pro-image (16:9, matches the pitch-video endcard aesthetic),
typography via Pillow (real Helvetica Neue — generated text is never crisp enough).
Background is cached: delete outputs/pitch/cover_bg.png to regenerate.
Run: .venv/bin/python pitch_cover.py
"""
from __future__ import annotations

import pathlib
import subprocess

from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from lumina.config import settings

OUT = pathlib.Path("outputs/pitch")
BG = OUT / "cover_bg.png"
COVER = OUT / "cover.png"
COVER_YT = OUT / "cover_yt.jpg"
W, H = 1920, 1080

TTC = "/System/Library/Fonts/HelveticaNeue.ttc"
REGULAR, BOLD, LIGHT, MEDIUM = 0, 1, 7, 10
GOLD = (244, 180, 0)

PROMPT = (
    "Cinematic key art for a premium tech keynote, widescreen: a vast dark navy scene filled with "
    "light — a gentle arc of floating translucent holographic storefront panels glowing vivid cool "
    "blue recedes from both sides toward the distance, a soft luminous blue horizon glow runs low "
    "across the frame, and a column of warm golden light with fine golden particles rises just "
    "right of center like earnings being paid; particles drift across the whole frame, subtle "
    "bokeh, volumetric light rays, rich contrast, photoreal, elegant, the middle band of the frame "
    "slightly dimmer so a title can sit over it, no text, no logos, no people, no watermark."
)


def gcloud_creds() -> Credentials:
    tok = subprocess.run(
        ["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return Credentials(token=tok)


def gen_background() -> None:
    client = genai.Client(
        vertexai=True, project=settings.project, location=settings.gemini_location,
        credentials=gcloud_creds(),
    )
    cfg = dict(response_modalities=["IMAGE"])
    try:
        config = types.GenerateContentConfig(
            **cfg, image_config=types.ImageConfig(aspect_ratio="16:9", image_size="2K")
        )
    except Exception:  # older SDK without image_size
        config = types.GenerateContentConfig(
            **cfg, image_config=types.ImageConfig(aspect_ratio="16:9")
        )
    resp = client.models.generate_content(
        model=settings.model_image_pro, contents=PROMPT, config=config
    )
    for cand in resp.candidates or []:
        for part in (cand.content.parts or []):
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                BG.write_bytes(inline.data)
                print(f"bg: {BG} ({len(inline.data)} bytes, {inline.mime_type})")
                return
    raise SystemExit("no image in response")


def font(face: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(TTC, size, index=face)


def tracked(d: ImageDraw.ImageDraw, xy, text, f, fill, tracking=0, anchor_center_w=None):
    """Draw text with letter-spacing; centers on width anchor_center_w when given."""
    widths = [d.textlength(ch, font=f) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x, y = xy
    if anchor_center_w:
        x = (anchor_center_w - total) / 2
    for ch, w in zip(text, widths):
        d.text((x, y), ch, font=f, fill=fill)
        x += w + tracking


def compose(bg_path: pathlib.Path = BG, out_png: pathlib.Path = COVER,
            out_jpg: pathlib.Path | None = COVER_YT, scrim_alpha: int = 135) -> None:
    bg = Image.open(bg_path).convert("RGB")
    bg = bg.resize((W, round(bg.height * W / bg.width)), Image.LANCZOS)
    if bg.height < H:
        bg = bg.resize((round(bg.width * H / bg.height), H), Image.LANCZOS)
    left = (bg.width - W) // 2
    top = (bg.height - H) // 2
    img = bg.crop((left, top, left + W, top + H)).convert("RGBA")

    # soft dark band behind the title so it reads at thumbnail size
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    sd.ellipse([W * 0.08, H * 0.26, W * 0.92, H * 0.78], fill=(4, 8, 18, scrim_alpha))
    scrim = scrim.filter(ImageFilter.GaussianBlur(110))
    img = Image.alpha_composite(img, scrim)

    d = ImageDraw.Draw(img)
    tracked(d, (0, 118), "L U M I N A", font(MEDIUM, 44), (*GOLD, 255), tracking=10, anchor_center_w=W)

    f_big = font(BOLD, 148)
    for i, line in enumerate(["The labor market", "for AI agents"]):
        w = d.textlength(line, font=f_big)
        x, y = (W - w) / 2, 380 + i * 172
        d.text((x + 3, y + 5), line, font=f_big, fill=(0, 0, 0, 160))
        d.text((x, y), line, font=f_big, fill=(255, 255, 255, 255))

    sub = "agents sell · escrow protects · work delivers"
    f_sub = font(REGULAR, 46)
    w = d.textlength(sub, font=f_sub)
    d.text(((W - w) / 2 + 2, 792 + 3), sub, font=f_sub, fill=(0, 0, 0, 140))
    d.text(((W - w) / 2, 792), sub, font=f_sub, fill=(235, 240, 250, 235))

    tracked(d, (0, 962), "G O O G L E  A D K   ·   G E M I N I   ·   V E O   ·   V E R T E X  A I",
            font(REGULAR, 30), (255, 255, 255, 165), tracking=6, anchor_center_w=W)

    img = img.convert("RGB")
    img.save(out_png)
    if out_jpg:
        img.resize((1280, 720), Image.LANCZOS).save(out_jpg, quality=88, optimize=True)
        print(f"wrote {out_png} and {out_jpg} ({out_jpg.stat().st_size // 1024} KB)")
    else:
        print(f"wrote {out_png}")


if __name__ == "__main__":
    if not BG.exists():
        gen_background()
    compose()
    veo = OUT / "cover_veo_frame.png"
    if veo.exists():
        compose(veo, OUT / "cover_v_veo.png", None, scrim_alpha=120)
