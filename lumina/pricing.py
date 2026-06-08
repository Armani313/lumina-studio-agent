"""Dynamic pricing + sensible defaults + platform presets for a ProductionSpec.

The consultant agent turns a customer's wishes (platforms, how much content) into a ProductionSpec;
the marketplace quotes a price from it (base + per-asset) before funding escrow.
"""
from __future__ import annotations

# Per-item pricing (USD). Quote = BASE + per-image + per-video + per-card.
BASE_PRICE = 7
PER_IMAGE = 1
PER_VIDEO = 2
PER_CARD = 1


def price_for_spec(spec: dict) -> int:
    """Quote (USD) for a production spec dict."""
    images = int(spec.get("image_count") or 0)
    videos = len(spec.get("videos") or [])
    cards = int(spec.get("card_count") or 0)
    return BASE_PRICE + PER_IMAGE * images + PER_VIDEO * videos + PER_CARD * cards


def price_breakdown(spec: dict) -> dict:
    """Itemized quote for showing the customer before they fund escrow."""
    images = int(spec.get("image_count") or 0)
    videos = len(spec.get("videos") or [])
    cards = int(spec.get("card_count") or 0)
    return {
        "base": BASE_PRICE,
        "images": {"count": images, "subtotal": PER_IMAGE * images},
        "videos": {"count": videos, "subtotal": PER_VIDEO * videos},
        "cards": {"count": cards, "subtotal": PER_CARD * cards},
        "total": price_for_spec(spec),
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
    """A sensible default plan when the user gives no preferences (keeps the 'just do it' path)."""
    return {
        "platforms": ["instagram"],
        "image_count": 6,
        "image_aspect_ratios": ["4:5", "1:1"],
        "videos": [
            {"kind": "360", "aspect_ratio": "16:9", "duration_seconds": 8},
            {"kind": "voiceover", "aspect_ratio": "9:16", "duration_seconds": 8},
        ],
        "card_count": 2,
        "card_aspect_ratio": "4:5",
        "copy_channels": ["instagram"],
        "language": "",
        "mood": "",
        "must_include": "",
        "avoid": "",
    }
