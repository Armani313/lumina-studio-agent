"""Veo video tool: a short product video via image-to-video (product photo as the first frame).

Veo is async/long-running and regional (us-central1, via veo_client). It writes the result
straight to GCS using output_gcs_uri.
"""
from __future__ import annotations

import time
import uuid

from google.adk.tools import ToolContext
from google.genai import types

from ..clients import veo_client
from ..config import settings
from .delivery import mime_for_uri, public_https_url


def generate_product_video(
    concept: str,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 8,
    person_generation: str = "allow_adult",
    generate_audio: bool = False,
    tool_context: ToolContext = None,
) -> dict:
    """Generate a short product video with Veo, animating from the product photo.

    Veo 3.1 generates native audio. For a voiceover, set generate_audio=True and write the spoken
    line directly into `concept` (e.g. 'no visible speaker, voiceover only; warm narrator says:
    "..."'); Veo times the narration to the clip. Keep spoken lines short enough to fit the duration.

    Args:
        concept: Motion/mood concept, plus any spoken voiceover lines (in quotes) and SFX/ambience.
        aspect_ratio: '9:16' (vertical) or '16:9' (landscape).
        duration_seconds: Clip length (Veo supports up to ~8s).
        person_generation: 'allow_adult' (people allowed) or 'dont_allow' (product-only, no person).
        generate_audio: True to generate native audio (voiceover/ambience described in `concept`).

    Returns:
        A dict with 'gs_uri' and 'https_url' of the video, or 'error'.
    """
    product_uri = tool_context.state.get("product_image_uri") if tool_context else None
    client = veo_client()
    out_prefix = f"gs://{settings.gcs_bucket}/videos/{uuid.uuid4().hex}/"
    image = (
        types.Image(gcs_uri=product_uri, mime_type=mime_for_uri(product_uri))
        if product_uri
        else None
    )

    op = client.models.generate_videos(
        model=settings.model_video,
        prompt=concept,
        image=image,
        config=types.GenerateVideosConfig(
            number_of_videos=1,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            person_generation=person_generation,
            output_gcs_uri=out_prefix,
            generate_audio=generate_audio,
        ),
    )

    waited = 0
    while not getattr(op, "done", False) and waited < 360:
        time.sleep(12)
        waited += 12
        op = client.operations.get(op)

    if not getattr(op, "done", False):
        return {"error": "video generation timed out"}
    if getattr(op, "error", None):
        return {"error": str(op.error)[:200]}

    resp = getattr(op, "response", None) or getattr(op, "result", None)
    vids = (getattr(resp, "generated_videos", None) or []) if resp else []
    if not vids:
        return {"error": "no video produced", "detail": str(getattr(resp, "rai_media_filtered_reasons", ""))[:200]}

    uri = vids[0].video.uri
    return {"gs_uri": uri, "https_url": public_https_url(uri)}
