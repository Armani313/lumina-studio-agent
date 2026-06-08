"""Shared ADK model adapters with retry on transient errors (429 RESOURCE_EXHAUSTED / 503 / 500).

ADK agents make their own LLM calls (separate from the tool calls in lumina/clients.py), so they
need their own retry config — otherwise a quota spike fails an agent before it even starts.
"""
import os

from google.adk.models import Gemini
from google.adk.planners import BuiltInPlanner
from google.genai import types

from .config import settings

_RETRY = types.HttpRetryOptions(
    attempts=6, initial_delay=2.0, max_delay=60.0, exp_base=2.0, http_status_codes=[429, 503, 500]
)


def reasoning_model() -> Gemini:
    """A fresh retry-enabled Gemini adapter for the reasoning model (one per agent — safe for the
    concurrent ParallelAgent stages)."""
    return Gemini(model=settings.model_reasoning, retry_options=_RETRY)


def _thoughts_enabled() -> bool:
    """Surface the model's own reasoning summaries unless explicitly disabled (SHOW_THOUGHTS=0)."""
    return os.getenv("SHOW_THOUGHTS", "1").strip().lower() not in ("0", "false", "no", "off", "")


def thinking_planner() -> BuiltInPlanner | None:
    """Planner that asks Gemini to emit thought *summaries*, so the UI can show HOW it reasons.

    Gemini 3.x already thinks by default; ``include_thoughts`` only surfaces a short summary of that
    thinking (a little extra output, no extra reasoning latency). Returns None when disabled, leaving
    agents untouched.
    """
    if not _thoughts_enabled():
        return None
    return BuiltInPlanner(thinking_config=types.ThinkingConfig(include_thoughts=True))
