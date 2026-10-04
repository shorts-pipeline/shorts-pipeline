"""Tests for pipeline/automation_gates.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from pipeline.automation_gates import (
    map_usage_consistency_warnings,
    narration_audio_segment_mismatch,
    non_empty_open_questions,
    postflight_estimated_duration_warnings,
    postflight_output_video,
    preflight_run_daily,
)


def test_preflight_skips_when_no_narration(tmp_path: Path) -> None:
    r = preflight_run_daily(
        repo_root=tmp_path,
        date_id="18040101",
        narration_path=tmp_path / "narrations" / "narration18040101.json",
        audio_final=tmp_path / "audio" / "18040101" / "final.mp3",
        vendor="fal",
        narration_only=False,
        skip_existing=True,
        dialogue_effective=True,
    )
    assert r.errors == []
    assert r.warnings == []


def test_preflight_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "n.json"
    p.write_text("{not json", encoding="utf-8")
    r = preflight_run_daily(
        repo_root=tmp_path,
        date_id="18040101",
        narration_path=p,
        audio_final=tmp_path / "final.mp3",
        vendor="fal",
        narration_only=False,
        skip_existing=False,
        dialogue_effective=True,
    )
    assert len(r.errors) == 1
    assert "Invalid JSON" in r.errors[0]


def test_preflight_dialogue_mismatch_when_skip_existing(tmp_path: Path) -> None:
    p = tmp_path / "n.json"
    p.write_text(
        json.dumps(
            {
                "narration_script": [
                    {
                        "segment_index": 1,
                        "narration": "x",
                        "visual_mode": "b_roll",
                        "dialogue": [{"speaker_id": "lewis", "text": "hi"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    r = preflight_run_daily(
        repo_root=tmp_path,
        date_id="18040101",
        narration_path=p,
        audio_final=tmp_path / "final.mp3",
        vendor="fal",
        narration_only=False,
        skip_existing=True,
        dialogue_effective=False,
    )
    assert any("dialogue" in e.lower() for e in r.errors)


def test_preflight_dialogue_mismatch_ok_when_regenerating_narration(tmp_path: Path) -> None:
    p = tmp_path / "n.json"
    p.write_text(
        json.dumps(
            {
                "dialogue_mode": True,
                "narration_script": [
                    {
                        "segment_index": 1,
                        "narration": "x",
                        "visual_mode": "b_roll",
                        "dialogue": [{"speaker_id": "lewis", "text": "hi"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    r = preflight_run_daily(
        repo_root=tmp_path,
        date_id="18040101",
        narration_path=p,
        audio_final=tmp_path / "final.mp3",
        vendor="fal",
        narration_only=True,
        skip_existing=False,
        dialogue_effective=False,
    )
    assert not r.errors
    assert any("regenerate without dialogue" in w.lower() for w in r.warnings)


def test_preflight_long_conversation_mismatch(tmp_path: Path) -> None:
    p = tmp_path / "n.json"
    p.write_text(
        json.dumps(
            {
                "long_conversation_mode": True,
                "dialogue_mode": True,
                "narration_script": [
                    {
                        "segment_index": 1,
                        "narration": "x",
                        "visual_mode": "b_roll",
                        "dialogue": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    r = preflight_run_daily(
        repo_root=tmp_path,
        date_id="18040101",
        narration_path=p,
        audio_final=tmp_path / "final.mp3",
        vendor="fal",
        narration_only=False,
        skip_existing=True,
        dialogue_effective=True,
        long_conversation_effective=False,
    )
    assert any("long-conversation" in e.lower() for e in r.errors)


def test_preflight_fal_talking_head_missing_portrait(tmp_path: Path) -> None:
    portraits = tmp_path / "character-portraits"
    portraits.mkdir(parents=True)
    p = tmp_path / "n.json"
    p.write_text(
        json.dumps(
            {
                "narration_script": [
                    {
                        "segment_index": 3,
                        "narration": "x",
                        "visual_mode": "talking_head",
                        "talking_head_subject": "nobody_xyz",
                        "dialogue": [{"speaker_id": "nobody_xyz", "text": "hello"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with (
        patch(
            "pipeline.automation_gates.talking_head_subject_has_dedicated_voice", return_value=True
        ),
        patch(
            "pipeline.automation_gates.portrait_path_for_talking_head",
            side_effect=FileNotFoundError("character-portraits/nobody_xyz.png"),
        ),
    ):
        r = preflight_run_daily(
            repo_root=tmp_path,
            date_id="18040101",
            narration_path=p,
            audio_final=tmp_path / "final.mp3",
            vendor="fal",
            narration_only=False,
            skip_existing=False,
            dialogue_effective=True,
        )
    assert any(("portrait" in e.lower()) or ("character-portraits" in e) for e in r.errors)


def test_preflight_google_talking_head_warns_not_error(tmp_path: Path) -> None:
    p = tmp_path / "n.json"
    p.write_text(
        json.dumps(
            {
                "narration_script": [
                    {
                        "segment_index": 3,
                        "narration": "x",
                        "visual_mode": "talking_head",
                        "talking_head_subject": "lewis",
                        "dialogue": [{"speaker_id": "lewis", "text": "hello"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    r = preflight_run_daily(
        repo_root=tmp_path,
        date_id="18040101",
        narration_path=p,
        audio_final=tmp_path / "final.mp3",
        vendor="google",
        narration_only=False,
        skip_existing=False,
        dialogue_effective=True,
    )
    assert r.errors == []
    assert any("b-roll" in w.lower() for w in r.warnings)


def test_narration_audio_segment_mismatch(tmp_path: Path) -> None:
    date_id = "18040101"
    audio_dir = tmp_path / "audio" / date_id
    audio_dir.mkdir(parents=True)
    nar = tmp_path / "narration18040101.json"
    nar.write_text(
        json.dumps({"narration_script": [{"narration": "a"}, {"narration": "b"}]}),
        encoding="utf-8",
    )
    (audio_dir / "durations.json").write_text(
        json.dumps([{"file": "intro"}, {"file": "01"}]),
        encoding="utf-8",
    )
    assert narration_audio_segment_mismatch(date_id, nar) is True


def test_stale_audio_strict(tmp_path: Path) -> None:
    date_id = "18040101"
    audio_dir = tmp_path / "audio" / date_id
    audio_dir.mkdir(parents=True)
    nar = tmp_path / "n.json"
    nar.write_text(
        json.dumps({"narration_script": [{"narration": "a"}, {"narration": "b"}]}), encoding="utf-8"
    )
    (audio_dir / "durations.json").write_text(
        json.dumps([{"file": "intro"}, {"file": "01"}]),
        encoding="utf-8",
    )
    final = audio_dir / "final.mp3"
    final.write_bytes(b"x")
    r_warn = preflight_run_daily(
        repo_root=tmp_path,
        date_id=date_id,
        narration_path=nar,
        audio_final=final,
        vendor="fal",
        narration_only=False,
        skip_existing=True,
        dialogue_effective=True,
        strict=False,
    )
    assert r_warn.errors == []
    assert r_warn.warnings

    r_err = preflight_run_daily(
        repo_root=tmp_path,
        date_id=date_id,
        narration_path=nar,
        audio_final=final,
        vendor="fal",
        narration_only=False,
        skip_existing=True,
        dialogue_effective=True,
        strict=True,
    )
    assert r_err.errors


def test_non_empty_open_questions_filters() -> None:
    assert non_empty_open_questions({"open_questions": ["  a  ", "", 1]}) == ["a"]


def test_preflight_open_questions_warning(tmp_path: Path) -> None:
    p = tmp_path / "n.json"
    p.write_text(
        json.dumps(
            {
                "narration_script": [
                    {
                        "segment_index": 1,
                        "stage_direction": "static",
                        "narration": "x",
                        "video_prompt": "y",
                    }
                ],
                "open_questions": ["Verify river width"],
            }
        ),
        encoding="utf-8",
    )
    r = preflight_run_daily(
        repo_root=tmp_path,
        date_id="18040101",
        narration_path=p,
        audio_final=tmp_path / "final.mp3",
        vendor="fal",
        narration_only=True,
        skip_existing=True,
        dialogue_effective=False,
    )
    assert any("open_questions" in w for w in r.warnings)


def test_map_usage_consistency_mid_without_insertion() -> None:
    w = map_usage_consistency_warnings(
        {
            "narration_version": "2.0",
            "video_metadata": {"map_usage": "mid-episode"},
            "map_insertions": [],
        }
    )
    assert any("mid-episode" in x for x in w)


def test_postflight_estimated_duration_warns_on_drift() -> None:
    w = postflight_estimated_duration_warnings(
        200.0,
        {"video_metadata": {"estimated_duration_seconds": 100}},
        tolerance_ratio=0.25,
    )
    assert len(w) == 1


def test_postflight_estimated_duration_ok_within_tolerance() -> None:
    assert (
        postflight_estimated_duration_warnings(
            110.0,
            {"video_metadata": {"estimated_duration_seconds": 100}},
        )
        == []
    )


def test_postflight_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "missing.mp4"
    r = postflight_output_video(p)
    assert r.errors


def test_postflight_probe_ok(tmp_path: Path) -> None:
    p = tmp_path / "out.mp4"
    p.write_bytes(b"\x00" * 2048)
    fake_info = {
        "format": {"duration": "12.5"},
        "streams": [{"codec_type": "video", "codec_name": "h264"}],
    }
    with patch("ffmpeg.probe", return_value=fake_info):
        r = postflight_output_video(
            p,
            narration_data={"video_metadata": {"estimated_duration_seconds": 100}},
        )
    assert r.errors == []
    assert any("estimated_duration" in w for w in r.warnings)


def test_postflight_no_video_stream(tmp_path: Path) -> None:
    p = tmp_path / "out.mp4"
    p.write_bytes(b"\x00" * 2048)
    fake_info = {"format": {"duration": "10"}, "streams": [{"codec_type": "audio"}]}
    with patch("ffmpeg.probe", return_value=fake_info):
        r = postflight_output_video(p)
    assert any("video stream" in e.lower() for e in r.errors)
