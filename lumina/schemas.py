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


class ShotPlan(BaseModel):
    shots: list[Shot] = Field(description="The planned lifestyle shots.")
    copy_channel: str = Field(description="Primary channel to write marketing copy for.")
