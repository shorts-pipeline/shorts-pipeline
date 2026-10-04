"""Approximate USD cost estimate for a produced video (manifest ``cost_estimate``).

Shared by ``run-daily.py`` (writes it after a pipeline run) and
``pipeline_ui/server.py`` (writes it after a UI-driven assemble). Update the
pricing constants when the APIs change.
"""

from __future__ import annotations

import json
from pathlib import Path

# Approximate API pricing for cost_estimate in manifest (USD). Update as needed.
OPENAI_GPT4O_MINI_INPUT_PER_1M = 0.15
OPENAI_GPT4O_MINI_OUTPUT_PER_1M = 0.60
OPENAI_TTS_PER_1M_CHARS = 15.0
FAL_VIDEO_USD_PER_SECOND_480P = 0.05
GOOGLE_VEO_USD_PER_SECOND_APPROX = 0.08  # placeholder; check current Veo pricing


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def estimate_video_cost(
    date_id: str,
    narration_vendor: str,
    tts_vendor: str,
    video_vendor: str,
    *,
    repo_root: Path | None = None,
) -> dict:
    """Estimate approximate USD cost for this video from narration, TTS, and video APIs.

    Returns a dict for manifest ``cost_estimate`` (``narration_usd``, ``tts_usd``,
    ``video_usd``, ``total_usd``; each ``None`` when its inputs are unavailable).
    Reads ``narrations/`` and ``audio/<date_id>/`` under ``repo_root`` (default:
    the repository root; both callers run from there).
    """
    root = Path(repo_root) if repo_root is not None else _repo_root()
    out: dict = {
        "narration_usd": None,
        "tts_usd": None,
        "video_usd": None,
        "total_usd": None,
    }
    narration_usd = 0.0
    tts_usd = 0.0
    video_usd = 0.0

    # Narration (openai: token usage from .meta.json)
    if narration_vendor == "openai":
        meta_path = root / "narrations" / f"narration{date_id}.meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                pin = meta.get("prompt_tokens", 0) or 0
                pout = meta.get("completion_tokens", 0) or 0
                narration_usd = (
                    pin * OPENAI_GPT4O_MINI_INPUT_PER_1M + pout * OPENAI_GPT4O_MINI_OUTPUT_PER_1M
                ) / 1_000_000
                out["narration_usd"] = round(narration_usd, 4)
            except (json.JSONDecodeError, OSError):
                pass

    # TTS (openai: character count from narration JSON)
    if tts_vendor == "openai":
        nar_path = root / "narrations" / f"narration{date_id}.json"
        if nar_path.exists():
            try:
                nar = json.loads(nar_path.read_text(encoding="utf-8"))
                chars = sum(
                    len(s.get("narration", "") or "") for s in nar.get("narration_script", [])
                )
                tts_usd = (chars * OPENAI_TTS_PER_1M_CHARS) / 1_000_000
                out["tts_usd"] = round(tts_usd, 4)
            except (json.JSONDecodeError, OSError):
                pass
    else:
        out["tts_usd"] = 0.0  # pyttsx3 is free

    # Video (total seconds from durations.json × vendor rate)
    durations_path = root / "audio" / date_id / "durations.json"
    if durations_path.exists():
        try:
            durations = json.loads(durations_path.read_text(encoding="utf-8"))
            total_seconds = sum(d.get("duration", 0) or 0 for d in durations)
            if video_vendor == "fal":
                video_usd = total_seconds * FAL_VIDEO_USD_PER_SECOND_480P
            elif video_vendor == "google":
                video_usd = total_seconds * GOOGLE_VEO_USD_PER_SECOND_APPROX
            else:
                video_usd = 0.0  # sora or unknown
            out["video_usd"] = round(video_usd, 4)
        except (json.JSONDecodeError, OSError):
            pass

    total = narration_usd + tts_usd + video_usd
    out["total_usd"] = round(total, 4) if total else None
    return out
