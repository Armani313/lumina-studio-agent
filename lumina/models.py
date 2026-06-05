"""Shared ADK model adapters with retry on transient errors (429 RESOURCE_EXHAUSTED / 503 / 500).

ADK agents make their own LLM calls (separate from the tool calls in lumina/clients.py), so they
need their own retry config — otherwise a quota spike fails an agent before it even starts.
"""
from google.adk.models import Gemini
from google.genai import types

from .config import settings

_RETRY = types.HttpRetryOptions(
    attempts=6, initial_delay=2.0, max_delay=60.0, exp_base=2.0, http_status_codes=[429, 503, 500]
)


def reasoning_model() -> Gemini:
    """A fresh retry-enabled Gemini adapter for the reasoning model (one per agent — safe for the
    concurrent ParallelAgent stages)."""
    return Gemini(model=settings.model_reasoning, retry_options=_RETRY)
