"""Central config. Loads .env and exposes verified GCP + model settings.

Verified 2026-06-01 against live Vertex APIs (see gcp.env). Gemini 3.x (text + image)
require the GLOBAL endpoint; Veo (video) is regional in us-central1.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # read .env at repo root


@dataclass(frozen=True)
class Settings:
    project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "aifreelance-hackathon")
    # Dedicated var so the Agent Engine runtime's GOOGLE_CLOUD_LOCATION (= deploy region)
    # never overrides the Gemini endpoint, which MUST be "global" for Gemini 3.x.
    gemini_location: str = os.getenv("GEMINI_LOCATION", "global")
    vertex_region: str = os.getenv("VERTEX_REGION", "us-central1")
    gcs_bucket: str = os.getenv("GCS_BUCKET", "aifreelance-hackathon-lumina-assets")

    model_reasoning: str = os.getenv("MODEL_REASONING", "gemini-3.5-flash")
    model_image: str = os.getenv("MODEL_IMAGE", "gemini-3.1-flash-image")
    model_image_pro: str = os.getenv("MODEL_IMAGE_PRO", "gemini-3-pro-image")
    model_video: str = os.getenv("MODEL_VIDEO", "veo-3.1-fast-generate-001")

    # Number of lifestyle shots to plan/generate per order. DEV ~5; FULL 12-20 for final render.
    image_count: int = int(os.getenv("IMAGE_COUNT", "5"))

    vertex_search_datastore: str = os.getenv(
        "VERTEX_SEARCH_DATASTORE",
        "projects/aifreelance-hackathon/locations/global/collections/"
        "default_collection/dataStores/aurelia-brand-kb",
    )


settings = Settings()
