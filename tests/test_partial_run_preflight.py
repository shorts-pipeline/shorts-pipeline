"""Preflight gates for partial pipeline steps (narration-to-mp3 / narration-to-video)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.automation_gates import (
    audio_segment_count_mismatch,
    preflight_narration_to_mp3,
    preflight_narration_to_video,
    validate_tts_one_speaker_policy,
)


def _write_narration(tmp_path: Path, date_id: str, script: list[dict], **extra: object) -> Path:
    p = tmp_path / "narrations" / f"narration{date_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "narration_version": "2.0",
        "title": "t",
        "narration_script": script,
        **extra,
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _segment(**kwargs: object) -> dict:
    base = {
        "stage_direction": "[CUT TO]",
        "narration": "Beat.",
        "video_prompt": "Visual.",
    }
    base.update(kwargs)
    return base


def test_preflight_mp3_rejects_two_cast_speakers_standard_dialogue(tmp_path: Path) -> None:
    date_id = "18040101"
    _write_narration(
        tmp_path,
        date_id,
        [
            _segment(
                dialogue=[
                    {"speaker_id": "clark", "text": "One."},
                    {"speaker_id": "lewis", "text": "Two."},
                ]
            )
        ],
    )
    r = preflight_narration_to_mp3(repo_root=tmp_path, date_id=date_id)
    assert any("One-speaker-per-segment" in e for e in r.errors)


def test_preflight_mp3_allows_long_conversation_b_roll_alternation(tmp_path: Path) -> None:
    date_id = "18040102"
    _write_narration(
        tmp_path,
        date_id,
        [
            _segment(
                visual_mode="b_roll",
                dialogue=[
                    {"speaker_id": "lewis", "text": "One."},
                    {"speaker_id": "clark", "text": "Two."},
                ],
            )
        ],
        long_conversation_mode=True,
    )
    r = preflight_narration_to_mp3(repo_root=tmp_path, date_id=date_id)
    assert r.errors == []


def test_preflight_mp3_partial_segments_requires_existing_files(tmp_path: Path) -> None:
    date_id = "18040103"
    _write_narration(
        tmp_path,
        date_id,
        [_segment(), _segment(), _segment()],
    )
    segs = tmp_path / "audio" / date_id / "segments"
    segs.mkdir(parents=True)
    (segs / "01.mp3").write_bytes(b"x")
    r = preflight_narration_to_mp3(
        repo_root=tmp_path,
        date_id=date_id,
        segment_indices={3},
    )
    assert any("Partial TTS" in e and "segment 2" in e for e in r.errors)


def test_preflight_mp3_partial_segments_ok_when_files_exist(tmp_path: Path) -> None:
    date_id = "18040104"
    _write_narration(
        tmp_path,
        date_id,
        [_segment(), _segment()],
    )
    segs = tmp_path / "audio" / date_id / "segments"
    segs.mkdir(parents=True)
    (segs / "01.mp3").write_bytes(b"x")
    r = preflight_narration_to_mp3(
        repo_root=tmp_path,
        date_id=date_id,
        segment_indices={2},
    )
    assert not any("Partial TTS" in e for e in r.errors)


def test_preflight_video_segment_count_mismatch(tmp_path: Path) -> None:
    date_id = "18040105"
    _write_narration(tmp_path, date_id, [_segment(), _segment()])
    durations = tmp_path / "audio" / date_id / "durations.json"
    durations.parent.mkdir(parents=True)
    durations.write_text(
        json.dumps([{"file": "segments/01.mp3", "duration": 1.0}]),
        encoding="utf-8",
    )
    r = preflight_narration_to_video(
        repo_root=tmp_path,
        date_id=date_id,
        vendor="fal",
    )
    assert any("Segment count mismatch" in e for e in r.errors)


def test_preflight_video_partial_run_warns_on_count_drift(tmp_path: Path) -> None:
    date_id = "18040106"
    _write_narration(tmp_path, date_id, [_segment(), _segment()])
    durations = tmp_path / "audio" / date_id / "durations.json"
    durations.parent.mkdir(parents=True)
    durations.write_text(
        json.dumps([{"file": "segments/01.mp3", "duration": 1.0}]),
        encoding="utf-8",
    )
    r = preflight_narration_to_video(
        repo_root=tmp_path,
        date_id=date_id,
        vendor="fal",
        segment_indices={1},
    )
    assert r.errors == []
    assert any("Segment count mismatch" in w for w in r.warnings)


def test_preflight_video_partial_strict_mismatch_is_error(tmp_path: Path) -> None:
    date_id = "18040107"
    _write_narration(tmp_path, date_id, [_segment(), _segment()])
    durations = tmp_path / "audio" / date_id / "durations.json"
    durations.parent.mkdir(parents=True)
    durations.write_text(
        json.dumps([{"file": "segments/01.mp3", "duration": 1.0}]),
        encoding="utf-8",
    )
    r = preflight_narration_to_video(
        repo_root=tmp_path,
        date_id=date_id,
        vendor="fal",
        segment_indices={1},
        strict=True,
    )
    assert any("Segment count mismatch" in e for e in r.errors)


def test_preflight_video_fal_talking_head_missing_portrait(tmp_path: Path) -> None:
    date_id = "18040108"
    _write_narration(
        tmp_path,
        date_id,
        [
            _segment(
                visual_mode="talking_head",
                talking_head_subject="lewis",
                dialogue=[{"speaker_id": "lewis", "text": "Report."}],
            )
        ],
    )
    segs = tmp_path / "audio" / date_id / "segments"
    segs.mkdir(parents=True)
    (segs / "01.mp3").write_bytes(b"x")
    durations = tmp_path / "audio" / date_id / "durations.json"
    durations.write_text(
        json.dumps([{"file": "segments/01.mp3", "duration": 1.0}]),
        encoding="utf-8",
    )
    with (
        patch(
            "pipeline.automation_gates.talking_head_subject_has_dedicated_voice",
            return_value=True,
        ),
        patch(
            "pipeline.automation_gates.portrait_path_for_talking_head",
            side_effect=FileNotFoundError("no portrait"),
        ),
    ):
        r = preflight_narration_to_video(
            repo_root=tmp_path,
            date_id=date_id,
            vendor="fal",
            segment_indices={1},
        )
    assert any("talking_head portrait" in e for e in r.errors)


def test_validate_tts_one_speaker_policy_rejects_multi_speaker() -> None:
    nar = {
        "narration_script": [
            {
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "dialogue": [
                    {"speaker_id": "lewis", "text": "a"},
                    {"speaker_id": "clark", "text": "b"},
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="at most one cast speaker"):
        validate_tts_one_speaker_policy(nar)


def test_validate_tts_one_speaker_policy_reads_long_conversation_flag() -> None:
    nar = {
        "long_conversation_mode": True,
        "narration_script": [
            {
                "visual_mode": "b_roll",
                "dialogue": [
                    {"speaker_id": "lewis", "text": "a"},
                    {"speaker_id": "clark", "text": "b"},
                ],
            }
        ],
    }
    validate_tts_one_speaker_policy(nar)


def test_audio_segment_count_mismatch_detects_missing_mp3(tmp_path: Path) -> None:
    date_id = "18040109"
    durations = tmp_path / "audio" / date_id / "durations.json"
    durations.parent.mkdir(parents=True)
    durations.write_text(
        json.dumps(
            [
                {"file": "segments/01.mp3", "duration": 1.0},
                {"file": "segments/02.mp3", "duration": 2.0},
            ]
        ),
        encoding="utf-8",
    )
    mismatch, detail = audio_segment_count_mismatch(tmp_path, date_id, 2)
    assert mismatch
    assert "audio=0" in detail
