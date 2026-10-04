"""FAL MiniMax Speech-02 HD for dialogue lines that use custom_voice_id (see character-portraits/voice_prompts.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from pipeline_logging import log_api_call_with_bodies

FAL_MINIMAX_SPEECH_MODEL = "fal-ai/minimax/speech-02-hd"


def load_fal_custom_voice_ids(voice_prompts_path: Path) -> dict[str, str]:
    """Map lowercased speaker_id (e.g. lewis, clark) to MiniMax custom_voice_id strings."""
    if not voice_prompts_path.is_file():
        return {}
    try:
        data = json.loads(voice_prompts_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    voices = data.get("voices")
    if not isinstance(voices, dict):
        return {}
    out: dict[str, str] = {}
    for key, block in voices.items():
        if not isinstance(block, dict):
            continue
        vid = str(block.get("custom_voice_id") or "").strip()
        if vid:
            out[str(key).strip().lower()] = vid
    return out


def fal_tts_credentials_available() -> bool:
    return bool((os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY") or "").strip())


def synthesize_fal_minimax_speech_mp3(text: str, out_path: Path, custom_voice_id: str) -> None:
    """
    Call fal-ai/minimax/speech-02-hd and save returned MP3 to out_path.
    Uses 24 kHz mono MP3 to align with OpenAI tts-1-hd segments for ffmpeg concat.
    """
    if not fal_tts_credentials_available():
        raise RuntimeError("Set FAL_KEY (or FAL_API_KEY) for FAL MiniMax TTS.")
    try:
        import fal_client
    except ImportError as e:
        raise RuntimeError("pip install fal-client") from e

    cid = (custom_voice_id or "").strip()
    if not cid:
        raise ValueError("custom_voice_id is empty")

    arguments = {
        "text": text,
        "voice_setting": {"custom_voice_id": cid},
        "output_format": "url",
        "language_boost": "English",
        "audio_setting": {"sample_rate": 24000, "format": "mp3", "channel": 1},
    }
    result = fal_client.subscribe(FAL_MINIMAX_SPEECH_MODEL, arguments)
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected FAL result type: {type(result)}")
    audio = result.get("audio")
    url = ""
    if isinstance(audio, dict):
        url = str(audio.get("url") or "").strip()
    if not url:
        raise RuntimeError(f"fal.ai returned no audio URL: {result!r}")

    log_api_call_with_bodies(
        "fal",
        "fal_client.subscribe",
        request_body=arguments,
        response_body=result,
        model=FAL_MINIMAX_SPEECH_MODEL,
        extra={"custom_voice_id_prefix": cid[:28] + "..." if len(cid) > 28 else cid},
    )

    r = requests.get(url, timeout=120)
    r.raise_for_status()
    out_path.write_bytes(r.content)
    log_api_call_with_bodies(
        "fal",
        "requests.get",
        request_body={"url": url},
        response_body={
            "status_code": r.status_code,
            "content_length": len(r.content),
            "out_path": str(out_path),
        },
        model="download_mp3",
    )
