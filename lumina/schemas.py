"""Structured schemas used as ADK output_schema targets (controlled generation)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CreativeBrief(BaseModel):
    product_name: str = Field(description="The product's name.")
    brand_name: str = Field(description="The brand/label name (used for research grounding).")
    product_type: str = Field(description="Category, e.g. skincare, apparel, jewelry.")
    key_features: str = Field(description="Comma-separated key features/benefits.")
    brand_voice: str = Field(description="Concise brand tone, e.g. 'minimalist, premium, calm'.")
    channels: list[str] = Field(description="Target channels, e.g. ['instagram','amazon'].")
    language: str = Field(
        description="Language the USER wrote the brief in (e.g. 'Russian', 'English'). ALL "
        "customer-facing copy and card text must be produced in this language."
    )


class Shot(BaseModel):
    channel: str = Field(description="Channel this shot targets.")
    aspect_ratio: str = Field(description="One of 1:1, 4:5, 9:16, 16:9.")
    scene_description: str = Field(description="Vivid, on-brand scene/setting description.")
    shot_type: str = Field(
        description="Shot type: 'hero', 'ecommerce', 'macro', 'lifestyle', 'flatlay', or 'on_model'."
    )


class ShotPlan(BaseModel):
    shots: list[Shot] = Field(description="The planned lifestyle shots.")
    copy_channel: str = Field(description="Primary channel to write marketing copy for.")


class VideoClip(BaseModel):
    kind: str = Field(description="Clip type: '360' (orbit), 'voiceover' (narrated ad), 'ugc', or 'macro'.")
    aspect_ratio: str = Field(default="9:16", description="One of 9:16, 16:9, 1:1.")
    duration_seconds: int = Field(default=8, description="Clip length in seconds (4-8).")


class ProductionSpec(BaseModel):
    """The agreed production plan from the consultant — parameterizes the whole pipeline."""

    platforms: list[str] = Field(
        default_factory=list, description="Target platforms, e.g. ['instagram','amazon','tiktok']."
    )
    image_count: int = Field(default=6, description="How many images to produce.")
    image_aspect_ratios: list[str] = Field(
        default_factory=lambda: ["4:5", "1:1"], description="Allowed image aspect ratios."
    )
    videos: list[VideoClip] = Field(
        default_factory=list, description="Video clips to produce (empty = no video)."
    )
    card_count: int = Field(default=2, description="How many product cards.")
    card_aspect_ratio: str = Field(default="4:5", description="Product-card aspect ratio.")
    copy_channels: list[str] = Field(
        default_factory=lambda: ["instagram"], description="Channels to write marketing copy for."
    )
    language: str = Field(default="", description="Output language; empty = match the user's brief.")
    mood: str = Field(default="", description="Optional overall mood/style direction.")
    must_include: str = Field(default="", description="Optional elements that MUST appear.")
    avoid: str = Field(default="", description="Optional elements to avoid.")
