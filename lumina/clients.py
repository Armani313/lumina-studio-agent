"""Lazily-built google-genai + GCS clients, with retry on transient errors (429/503/500).

Gemini 3.x (text + image) use the GLOBAL endpoint; Veo (video) is regional (us-central1), so it
gets its own client. Uses the Google Gen AI SDK (google-genai) on the Vertex backend — NOT the
deprecated vertexai.* SDK (removed 2026-06-24).
"""
from __future__ import annotations

from functools import lru_cache

from google import genai
from google.cloud import storage
from google.genai import types

from .config import settings

# Retry transient quota/capacity errors (429 RESOURCE_EXHAUSTED, 503, 500) with exp backoff.
RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=6, initial_delay=2.0, max_delay=60.0, exp_base=2.0, http_status_codes=[429, 503, 500]
)


@lru_cache(maxsize=1)
def gemini_client() -> genai.Client:
    """Client for Gemini text + image models (global endpoint)."""
    return genai.Client(
        vertexai=True,
        project=settings.project,
        location=settings.gemini_location,
        http_options=types.HttpOptions(retry_options=RETRY_OPTIONS),
    )


@lru_cache(maxsize=1)
def veo_client() -> genai.Client:
    """Client for Veo video models (regional: us-central1)."""
    return genai.Client(
        vertexai=True,
        project=settings.project,
        location=settings.vertex_region,
        http_options=types.HttpOptions(retry_options=RETRY_OPTIONS),
    )


@lru_cache(maxsize=1)
def gcs_bucket():
    return storage.Client(project=settings.project).bucket(settings.gcs_bucket)
