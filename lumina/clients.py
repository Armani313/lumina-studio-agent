"""Lazily-built google-genai + GCS clients.

Gemini 3.x (text + image) use the GLOBAL endpoint; Veo (video) is regional (us-central1),
so it gets its own client. Uses the Google Gen AI SDK (google-genai) on the Vertex backend —
NOT the deprecated vertexai.* SDK (removed 2026-06-24).
"""
from __future__ import annotations

from functools import lru_cache

from google import genai
from google.cloud import storage

from .config import settings


@lru_cache(maxsize=1)
def gemini_client() -> genai.Client:
    """Client for Gemini text + image models (global endpoint)."""
    return genai.Client(
        vertexai=True, project=settings.project, location=settings.gemini_location
    )


@lru_cache(maxsize=1)
def veo_client() -> genai.Client:
    """Client for Veo video models (regional: us-central1)."""
    return genai.Client(
        vertexai=True, project=settings.project, location=settings.vertex_region
    )


@lru_cache(maxsize=1)
def gcs_bucket():
    return storage.Client(project=settings.project).bucket(settings.gcs_bucket)
