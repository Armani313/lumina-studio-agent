"""Dynamic pricing + sensible defaults + platform presets for a ProductionSpec.

The consultant agent turns a customer's wishes (platforms, how much content) into a ProductionSpec;
the marketplace quotes a price from it before funding escrow.

Pricing model — a FLAT BASE PACKAGE plus per-item adjustments:
  • The base package (BASE_IMAGES + BASE_VIDEOS + BASE_CARDS) is a flat BASE_PACKAGE_PRICE.
  • Wanting MORE than the base surcharges per extra item (images / videos / cards).
  • Wanting FEWER videos or cards discounts per dropped item; the base images are always
    included (asking for fewer images doesn't lower the price), and the total never drops
    below PRICE_FLOOR.
So the standard 16 images + 2 videos + 2 cards = $10; +1 video = $13; 1 video & no cards = $5.
"""
from __future__ import annotations

import os

# Flat base-package price (USD) and what it includes. Tunable via env for per-deployment pricing.
BASE_PACKAGE_PRICE = int(os.getenv("BASE_PACKAGE_PRICE", "10"))
BASE_IMAGES = int(os.getenv("BASE_IMAGES", "16"))
BASE_VIDEOS = int(os.getenv("BASE_VIDEOS", "2"))
BASE_CARDS = int(os.getenv("BASE_CARDS", "2"))

# Per-item adjustments vs the base package (USD).
ADD_IMAGE = 1    # each image BEYOND the base count (fewer images is not discounted)
VIDEO_DELTA = 3  # each video added OR removed vs the base count (symmetric)
CARD_DELTA = 2   # each card added OR removed vs the base count (symmetric)
PRICE_FLOOR = int(os.getenv("PRICE_FLOOR", "5"))  # the quote never drops below this

# Revision policy: how many generation-consuming revisions an order includes. Each revision
# regenerates ONLY what the buyer asks to change, capped by the paid scope; questions are free
# and unlimited. Past the limit the approved package is re-delivered unchanged with a polite
# note — the buyer can't burn our Imagen/Veo budget with endless free re-rolls.
FREE_REVISIONS = int(os.getenv("FREE_REVISIONS", "3"))


def price_for_counts(images: int, videos: int, cards: int) -> int:
    """Quote (USD) for explicit asset counts: flat base package + per-item adjustments, floored."""
    price = (
        BASE_PACKAGE_PRICE
        + ADD_IMAGE * max(0, int(images) - BASE_IMAGES)   # extra images only; fewer stay included
        + VIDEO_DELTA * (int(videos) - BASE_VIDEOS)        # ± per video vs the base count
        + CARD_DELTA * (int(cards) - BASE_CARDS)           # ± per card vs the base count
    )
    return max(price, PRICE_FLOOR)


def price_for_spec(spec: dict) -> int:
    """Quote (USD) for a production spec dict."""
    images = int(spec.get("image_count") or 0)
    videos = len(spec.get("videos") or [])
    cards = int(spec.get("card_count") or 0)
    return price_for_counts(images, videos, cards)


def price_breakdown(spec: dict) -> dict:
    """Itemized quote for showing the customer before they fund escrow: base package + adjustments."""
    images = int(spec.get("image_count") or 0)
    videos = len(spec.get("videos") or [])
    cards = int(spec.get("card_count") or 0)
    return {
        "base_package": BASE_PACKAGE_PRICE,
        "base_includes": {"images": BASE_IMAGES, "videos": BASE_VIDEOS, "cards": BASE_CARDS},
        "adjustments": {
            "images": {"count": images, "delta": ADD_IMAGE * max(0, images - BASE_IMAGES)},
            "videos": {"count": videos, "delta": VIDEO_DELTA * (videos - BASE_VIDEOS)},
            "cards": {"count": cards, "delta": CARD_DELTA * (cards - BASE_CARDS)},
        },
        "floor": PRICE_FLOOR,
        "total": price_for_counts(images, videos, cards),
    }


# Platform -> spec fragments the consultant merges in (format/ratios/typical content per platform).
PLATFORM_PRESETS: dict[str, dict] = {
    "instagram": {
        "image_aspect_ratios": ["4:5", "1:1"],
        "copy_channels": ["instagram"],
        "videos": [{"kind": "voiceover", "aspect_ratio": "9:16", "duration_seconds": 8}],
    },
    "instagram_stories": {
        "image_aspect_ratios": ["9:16"],
        "copy_channels": ["instagram"],
        "videos": [{"kind": "ugc", "aspect_ratio": "9:16", "duration_seconds": 8}],
    },
    "tiktok": {
        "image_aspect_ratios": ["9:16"],
        "copy_channels": ["tiktok"],
        "videos": [
            {"kind": "ugc", "aspect_ratio": "9:16", "duration_seconds": 8},
            {"kind": "voiceover", "aspect_ratio": "9:16", "duration_seconds": 8},
        ],
    },
    "amazon": {
        "image_aspect_ratios": ["1:1"],
        "copy_channels": ["amazon"],
        "videos": [{"kind": "360", "aspect_ratio": "1:1", "duration_seconds": 8}],
    },
    "web": {
        "image_aspect_ratios": ["16:9", "4:5"],
        "copy_channels": ["website"],
        "videos": [{"kind": "360", "aspect_ratio": "16:9", "duration_seconds": 8}],
    },
    "facebook": {
        "image_aspect_ratios": ["1:1", "4:5"],
        "copy_channels": ["facebook"],
        "videos": [{"kind": "voiceover", "aspect_ratio": "1:1", "duration_seconds": 8}],
    },
}


def default_spec() -> dict:
    """A sensible default plan when the user gives no preferences (keeps the 'just do it' path).

    Matches the base package (BASE_IMAGES + BASE_VIDEOS + BASE_CARDS), so it prices to
    BASE_PACKAGE_PRICE.
    """
    return {
        "platforms": ["instagram"],
        "image_count": BASE_IMAGES,
        "image_aspect_ratios": ["4:5", "1:1"],
        "videos": [
            {"kind": "360", "aspect_ratio": "16:9", "duration_seconds": 8},
            {"kind": "voiceover", "aspect_ratio": "9:16", "duration_seconds": 8},
        ],
        "card_count": BASE_CARDS,
        "card_aspect_ratio": "4:5",
        "copy_channels": ["instagram"],
        "language": "",
        "mood": "",
        "must_include": "",
        "avoid": "",
    }
