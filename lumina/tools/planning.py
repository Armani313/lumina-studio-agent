"""Consultant tool: lock the agreed ProductionSpec (+ price quote) into session state."""
from __future__ import annotations

from google.adk.tools import ToolContext

from ..pricing import price_breakdown
from ..schemas import ProductionSpec, VideoClip

_RATIOS = {"1:1", "4:5", "9:16", "16:9"}
_VIDEO_KINDS = {"360", "voiceover", "ugc", "macro"}


def finalize_plan(
    platforms: list[str],
    image_count: int,
    image_aspect_ratios: list[str],
    video_kinds: list[str],
    video_aspect_ratio: str = "9:16",
    card_count: int = 2,
    card_aspect_ratio: str = "4:5",
    copy_channels: list[str] = None,
    language: str = "",
    mood: str = "",
    must_include: str = "",
    avoid: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Lock the agreed production plan. Call this ONLY after the customer has confirmed the plan.

    Writes the ProductionSpec to session state (key 'spec') and returns it with an itemized quote.

    Args:
        platforms: target platforms, e.g. ['instagram','amazon','tiktok'].
        image_count: how many images (1-20).
        image_aspect_ratios: allowed image ratios, from '1:1','4:5','9:16','16:9'.
        video_kinds: video clips to make, each one of '360','voiceover','ugc','macro' (empty = none).
        video_aspect_ratio: aspect ratio for the video clips ('9:16','16:9' or '1:1').
        card_count: how many product cards (0-5).
        card_aspect_ratio: product-card ratio ('1:1','4:5' or '9:16').
        copy_channels: channels to write copy for, e.g. ['instagram','amazon'].
        language: output language (empty = match the user's brief).
        mood: optional overall mood/style.
        must_include: optional elements that must appear.
        avoid: optional elements to avoid.
    """
    ratios = [r for r in (image_aspect_ratios or []) if r in _RATIOS] or ["4:5", "1:1"]
    var = video_aspect_ratio if video_aspect_ratio in _RATIOS else "9:16"
    kinds, seen = [], set()
    for k in (video_kinds or []):
        kk = str(k).strip().lower()
        if kk in _VIDEO_KINDS and kk not in seen:
            seen.add(kk)
            kinds.append(kk)
    videos = [VideoClip(kind=k, aspect_ratio=var, duration_seconds=8) for k in kinds[:4]]
    spec = ProductionSpec(
        platforms=[str(p).strip().lower() for p in (platforms or [])],
        image_count=max(1, min(int(image_count or 16), 20)),
        image_aspect_ratios=ratios,
        videos=videos,
        card_count=max(0, min(int(card_count or 0), 5)),
        card_aspect_ratio=card_aspect_ratio if card_aspect_ratio in _RATIOS else "4:5",
        copy_channels=[str(c).strip().lower() for c in (copy_channels or ["instagram"])],
        language=language or "",
        mood=mood or "",
        must_include=must_include or "",
        avoid=avoid or "",
    ).model_dump()
    quote = price_breakdown(spec)
    if tool_context is not None:
        tool_context.state["spec"] = spec
        tool_context.state["quote"] = quote
        tool_context.state["plan_finalized"] = True
    return {"status": "finalized", "spec": spec, "quote": quote}
