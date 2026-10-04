"""Shared video cost estimate (pipeline.cost_estimate.estimate_video_cost)."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.cost_estimate import (
    FAL_VIDEO_USD_PER_SECOND_480P,
    GOOGLE_VEO_USD_PER_SECOND_APPROX,
    OPENAI_GPT4O_MINI_INPUT_PER_1M,
    OPENAI_GPT4O_MINI_OUTPUT_PER_1M,
    OPENAI_TTS_PER_1M_CHARS,
    estimate_video_cost,
)

DATE_ID = "18040528"


def _seed(root: Path, *, meta=None, narration=None, durations=None) -> None:
    (root / "narrations").mkdir(parents=True, exist_ok=True)
    (root / "audio" / DATE_ID).mkdir(parents=True, exist_ok=True)
    if meta is not None:
        (root / "narrations" / f"narration{DATE_ID}.meta.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )
    if narration is not None:
        (root / "narrations" / f"narration{DATE_ID}.json").write_text(
            json.dumps(narration), encoding="utf-8"
        )
    if durations is not None:
        (root / "audio" / DATE_ID / "durations.json").write_text(
            json.dumps(durations), encoding="utf-8"
        )


def test_all_none_when_inputs_missing(tmp_path: Path) -> None:
    out = estimate_video_cost(DATE_ID, "openai", "openai", "fal", repo_root=tmp_path)
    assert out == {
        "narration_usd": None,
        "tts_usd": None,
        "video_usd": None,
        "total_usd": None,
    }


def test_narration_cost_from_meta_tokens(tmp_path: Path) -> None:
    _seed(tmp_path, meta={"prompt_tokens": 1_000_000, "completion_tokens": 2_000_000})
    out = estimate_video_cost(DATE_ID, "openai", "pyttsx3", "sora", repo_root=tmp_path)
    expected = OPENAI_GPT4O_MINI_INPUT_PER_1M + 2 * OPENAI_GPT4O_MINI_OUTPUT_PER_1M
    assert out["narration_usd"] == round(expected, 4)
    assert out["tts_usd"] == 0.0  # non-openai TTS is free
    assert out["video_usd"] is None
    assert out["total_usd"] == round(expected, 4)


def test_tts_cost_from_narration_chars(tmp_path: Path) -> None:
    script = {"narration_script": [{"narration": "a" * 500}, {"narration": "b" * 500}]}
    _seed(tmp_path, narration=script)
    out = estimate_video_cost(DATE_ID, "openai", "openai", "sora", repo_root=tmp_path)
    assert out["tts_usd"] == round(1000 * OPENAI_TTS_PER_1M_CHARS / 1_000_000, 4)


def test_video_cost_vendor_branches(tmp_path: Path) -> None:
    durations = [{"duration": 10}, {"duration": 5.5}]
    _seed(tmp_path, durations=durations)
    fal = estimate_video_cost(DATE_ID, "x", "pyttsx3", "fal", repo_root=tmp_path)
    goog = estimate_video_cost(DATE_ID, "x", "pyttsx3", "google", repo_root=tmp_path)
    sora = estimate_video_cost(DATE_ID, "x", "pyttsx3", "sora", repo_root=tmp_path)
    assert fal["video_usd"] == round(15.5 * FAL_VIDEO_USD_PER_SECOND_480P, 4)
    assert goog["video_usd"] == round(15.5 * GOOGLE_VEO_USD_PER_SECOND_APPROX, 4)
    assert sora["video_usd"] == 0.0


def test_total_sums_components(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        meta={"prompt_tokens": 1_000_000, "completion_tokens": 0},
        narration={"narration_script": [{"narration": "a" * 1_000_000}]},
        durations=[{"duration": 20}],
    )
    out = estimate_video_cost(DATE_ID, "openai", "openai", "fal", repo_root=tmp_path)
    expected = (
        OPENAI_GPT4O_MINI_INPUT_PER_1M
        + OPENAI_TTS_PER_1M_CHARS
        + 20 * FAL_VIDEO_USD_PER_SECOND_480P
    )
    assert out["total_usd"] == round(expected, 4)


def test_malformed_json_is_swallowed(tmp_path: Path) -> None:
    (tmp_path / "narrations").mkdir(parents=True)
    (tmp_path / "narrations" / f"narration{DATE_ID}.meta.json").write_text("{bad", encoding="utf-8")
    out = estimate_video_cost(DATE_ID, "openai", "pyttsx3", "sora", repo_root=tmp_path)
    assert out["narration_usd"] is None
