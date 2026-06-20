"""One-off: generate 3 Veo background clips for the 30s pitch video. NOT deployed.

Pure text-to-video (no first-frame product image), 16:9, 8s, no audio — the soundtrack
(voiceover + music) is muxed in ffmpeg later. Downloads results to outputs/pitch/.
Auth: ADC is expired on this machine, so we mint a token from the live gcloud user
credential instead of relying on application-default.
Run: .venv/bin/python pitch_clips.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from google import genai
from google.cloud import storage
from google.genai import types
from google.oauth2.credentials import Credentials

from lumina.config import settings

OUT = pathlib.Path("outputs/pitch")
OUT.mkdir(parents=True, exist_ok=True)


def gcloud_creds() -> Credentials:
    tok = subprocess.run(
        ["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return Credentials(token=tok)


CREDS = gcloud_creds()

CLIPS = {
    "a_orchestration": (
        "Abstract cinematic visualization of an AI orchestration pipeline: glowing nodes of light "
        "connect one after another into a chain, the chain splits into three parallel luminous "
        "streams flowing simultaneously, then they converge and circle into a loop. Dark navy-black "
        "void, elegant thin light trails with blue, red, yellow and green accents, volumetric glow, "
        "shallow depth of field, slow confident dolly forward, premium tech keynote aesthetic, "
        "photoreal, no text, no logos, no people."
    ),
    "b_cloud": (
        "Cinematic slow glide through a futuristic cloud data center made of light: endless rows of "
        "translucent glowing server racks, streams of data rising as particles of light into a vast "
        "luminous network overhead, cool blue with warm amber accents, anamorphic lens flares, "
        "shallow depth of field, dark premium aesthetic, photoreal, no text, no logos, no people."
    ),
    "c_market": (
        "Cinematic abstract marketplace of light: floating holographic storefront panels arranged in "
        "a gentle arc over a dark reflective floor, one panel glows brighter and rises forward while "
        "fine golden particles stream toward it like earnings being paid, soft bokeh, slow orbital "
        "camera move, deep blue and gold palette, premium futuristic aesthetic, photoreal, no text, "
        "no logos, no people."
    ),
    "d_endcard": (
        "Very slow drifting dark gradient backdrop: deep navy fading to black with a soft luminous "
        "horizon glow low in frame and sparse tiny particles floating gently, minimal, elegant, "
        "premium keynote background, almost still motion, no text, no logos, no people."
    ),
}


def veo() -> genai.Client:
    return genai.Client(
        vertexai=True, project=settings.project, location=settings.vertex_region, credentials=CREDS
    )


def fetch(uri: str, dest: pathlib.Path) -> None:
    b, _, p = uri[5:].partition("/")
    storage.Client(project=settings.project, credentials=CREDS).bucket(b).blob(p).download_to_filename(str(dest))


def make(name: str, prompt: str) -> tuple[str, dict]:
    client = veo()
    op = client.models.generate_videos(
        model=settings.model_video,
        prompt=prompt,
        config=types.GenerateVideosConfig(
            number_of_videos=1,
            aspect_ratio="16:9",
            resolution="1080p",
            duration_seconds=8,
            person_generation="dont_allow",
            output_gcs_uri=f"gs://{settings.gcs_bucket}/videos/pitch/{uuid.uuid4().hex}/",
            generate_audio=False,
        ),
    )
    waited = 0
    while not getattr(op, "done", False) and waited < 420:
        time.sleep(12)
        waited += 12
        op = client.operations.get(op)
    if not getattr(op, "done", False):
        return name, {"error": "timeout"}
    if getattr(op, "error", None):
        return name, {"error": str(op.error)[:300]}
    resp = getattr(op, "response", None) or getattr(op, "result", None)
    vids = (getattr(resp, "generated_videos", None) or []) if resp else []
    if not vids:
        return name, {"error": "no video", "detail": str(getattr(resp, "rai_media_filtered_reasons", ""))[:300]}
    uri = vids[0].video.uri
    fetch(uri, OUT / f"{name}_1080.mp4")
    return name, {"gs_uri": uri}


def main() -> None:
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = dict(ex.map(lambda kv: make(*kv), CLIPS.items()))
    print(json.dumps(results, indent=2, ensure_ascii=False))
    failed = [n for n, r in results.items() if not r.get("gs_uri")]
    print("FAILED:", failed if failed else "none")


if __name__ == "__main__":
    main()
