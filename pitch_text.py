"""One-off: render the pitch-video text overlays as transparent 1920x1080 PNGs. NOT deployed.

Homebrew ffmpeg lacks drawtext, so titles are baked here with Pillow (real Helvetica Neue
faces from the .ttc) and composited in ffmpeg via overlay+fade.
Run: .venv/bin/python pitch_text.py
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

TTC = "/System/Library/Fonts/HelveticaNeue.ttc"
REGULAR, BOLD, LIGHT, MEDIUM = 0, 1, 7, 10
W, H = 1920, 1080
OUT = pathlib.Path("outputs/pitch")

GOLD = (244, 180, 0)
WHITE = (255, 255, 255)


def font(face: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(TTC, size, index=face)


def draw_line(d: ImageDraw.ImageDraw, xy, text, f, fill, alpha=255, center=False, shadow=True):
    x, y = xy
    if center:
        w = d.textlength(text, font=f)
        x = (W - w) / 2
    if shadow:
        d.text((x + 2, y + 3), text, font=f, fill=(0, 0, 0, min(150, alpha)))
    d.text((x, y), text, font=f, fill=(*fill, alpha))


def add_scrim(img: Image.Image, top: int = 820, max_alpha: int = 130) -> None:
    """Bottom gradient scrim so lower-third titles stay readable on busy footage."""
    d = ImageDraw.Draw(img)
    for y in range(top, H):
        a = int(max_alpha * (y - top) / (H - top))
        d.line([(0, y), (W, y)], fill=(0, 0, 0, a))


def make(name: str, lines: list[dict], scrim: bool = False) -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if scrim:
        add_scrim(img)
    d = ImageDraw.Draw(img)
    for ln in lines:
        draw_line(
            d, (ln.get("x", 130), ln["y"]), ln["text"], font(ln.get("face", MEDIUM), ln["size"]),
            ln.get("fill", WHITE), alpha=ln.get("alpha", 255), center=ln.get("center", False),
            shadow=ln.get("shadow", True),
        )
    img.save(OUT / f"{name}.png")
    print(name)


make("t1", [dict(y=924, size=42, text="Google ADK — sequential · parallel · loop")], scrim=True)
make("t2", [
    dict(y=888, size=42, text="Gemini · Veo · Vertex AI"),
    dict(y=948, size=30, face=REGULAR, alpha=222,
         text="Agent Engine · Cloud Run · Firestore · Cloud Storage · Cloud Trace"),
], scrim=True)
make("t3", [dict(y=924, size=42, text="Agent marketplace — escrow · A2A · webhook")], scrim=True)
make("t4", [dict(y=988, size=40, text="Lumina — the first agent hired · real deliverables")], scrim=True)
make("t5", [dict(y=400, size=92, face=LIGHT, center=True, text="The labor market for AI agents.")])
make("t6", [dict(y=556, size=50, center=True, fill=GOLD, text="Try it live — aifreelance.shop")])
make("t7", [dict(y=972, size=26, face=REGULAR, alpha=190, center=True, shadow=False,
                 text="Google ADK · Gemini · Veo · Vertex AI · Agent Engine · Cloud Run · Firestore · Cloud Storage · Cloud Trace")])
