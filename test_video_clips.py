"""One-off local check of the two reworked Veo clips (360 orbit + voiceover). NOT deployed.

Generates both clips from the sample product photo, downloads them, and ffprobes for streams —
the voiceover clip MUST have an audio stream; the orbit clip should be video-only.
Run: .venv/bin/python test_video_clips.py   (needs ADC: gcloud auth application-default login)
"""
from __future__ import annotations

import json
import subprocess

from google.cloud import storage

from lumina.config import settings
from lumina.tools.delivery import upload_bytes
from lumina.tools.video import generate_product_video


class TC:
    def __init__(self, state):
        self.state = state


def ffprobe(path: str) -> str:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,duration,channels", "-of", "json", path],
        capture_output=True, text=True,
    )
    return out.stdout


def fetch(uri: str, dest: str) -> None:
    b, _, p = uri[5:].partition("/")
    storage.Client(project=settings.project).bucket(b).blob(p).download_to_filename(dest)


def main() -> None:
    data = open("assets/sample_product.png", "rb").read()
    product_uri = upload_bytes(data, "inputs/test_voiceover.png", "image/png")
    print("product_uri:", product_uri)
    tc = TC({"product_image_uri": product_uri})

    print("\n=== generating 360 orbit (no audio) ===", flush=True)
    r1 = generate_product_video(
        concept=("Smooth continuous 360-degree orbital turntable: the camera slowly circles a full "
                 "revolution around the product, centered, sharp and identical throughout, clean "
                 "seamless studio backdrop, even premium lighting, photoreal, no text."),
        aspect_ratio="16:9", duration_seconds=8, person_generation="dont_allow",
        generate_audio=False, tool_context=tc,
    )
    print("orbit:", r1, flush=True)

    print("\n=== generating voiceover ad (audio) ===", flush=True)
    r2 = generate_product_video(
        concept=('Cinematic product b-roll of the perfume bottle: gentle push-ins and tasteful '
                 'close-ups on a marble surface in soft premium light, including a close-up that '
                 'highlights the faceted glass. No visible speaker, voiceover only. Warm, confident '
                 'narrator says: "Этот аромат раскрывается стойким шлейфом на весь день — '
                 'идеальный выбор, очень рекомендую." Clear persuasive tone, medium pace. '
                 'Quiet ambient room tone only. No on-screen text.'),
        aspect_ratio="9:16", duration_seconds=8, person_generation="dont_allow",
        generate_audio=True, tool_context=tc,
    )
    print("voiceover:", r2, flush=True)

    for name, r in [("orbit", r1), ("voiceover", r2)]:
        uri = (r or {}).get("gs_uri")
        if not uri:
            print(f"\n{name} FAILED -> {r}")
            continue
        dest = f"/tmp/test_{name}.mp4"
        fetch(uri, dest)
        info = ffprobe(dest)
        streams = json.loads(info).get("streams", []) if info else []
        kinds = [s.get("codec_type") for s in streams]
        print(f"\n--- {name}: {dest} | streams={kinds} ---")
        print(info)


if __name__ == "__main__":
    main()
