"""One-off: per-segment voiceover for the pitch video via Gemini TTS on Vertex. NOT deployed.

One WAV per storyboard segment (vo_1..vo_4) so the edit can cut video exactly on phrase
boundaries. Same model+voice throughout for consistency. ADC is expired on this machine,
so auth is a token minted from the live gcloud user credential.
Run: .venv/bin/python pitch_tts.py
"""
from __future__ import annotations

import pathlib
import subprocess
import wave

from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials

from lumina.config import settings


def gcloud_creds() -> Credentials:
    tok = subprocess.run(
        ["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return Credentials(token=tok)


CREDS = gcloud_creds()

STYLE = (
    "Read in a confident, energetic cinematic keynote-narrator voice. Brisk pace, about 150 words "
    "per minute, crisp articulation, only the punctuation pauses — no long dramatic gaps: "
)
SEGMENTS = {
    "vo_3a": "Built net-new for this challenge: a marketplace where autonomous agents earn —",
    "vo_3b": "and Lumina, the first agent hired on it.",
}
OUT = pathlib.Path("outputs/pitch")
OUT.mkdir(parents=True, exist_ok=True)
MODEL = "gemini-2.5-pro-tts"


def synth(client: genai.Client, text: str) -> bytes | None:
    resp = client.models.generate_content(
        model=MODEL,
        contents=STYLE + text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                )
            ),
        ),
    )
    for cand in resp.candidates or []:
        for part in (cand.content.parts or []) if cand.content else []:
            blob = getattr(part, "inline_data", None)
            if blob and blob.data:
                return bytes(blob.data)
    return None


def write_wav(path: pathlib.Path, data: bytes) -> None:
    if data[:4] == b"RIFF":
        path.write_bytes(data)
        return
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(data)


def main() -> None:
    client = genai.Client(vertexai=True, project=settings.project, location="global", credentials=CREDS)
    for name, text in SEGMENTS.items():
        data = synth(client, text)
        if not data:
            print(f"{name}: FAILED")
            continue
        path = OUT / f"{name}.wav"
        write_wav(path, data)
        dur = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        ).stdout.strip()
        print(f"{name}: {dur}s")


if __name__ == "__main__":
    main()
