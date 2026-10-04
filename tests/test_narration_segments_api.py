"""Tests for GET /api/narration-segments payload builder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_ui.server import (
    _segment_cue_fields_from_row,
    narration_segments_payload,
    video_preview_cues_payload,
)


@pytest.fixture
def repo_with_dialogue_narration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    (repo / "narrations").mkdir(parents=True)
    narr = {
        "title": "Test day",
        "dialogue_mode": True,
        "narration_script": [
            {
                "narration": "Wide shot of the river.",
                "video_prompt": "Missouri morning mist",
                "visual_mode": "b_roll",
            },
            {
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "dialogue": [{"speaker_id": "lewis", "text": "We should press on."}],
                "video_prompt": "Portrait interior",
                "talking_head_prompt": "calm delivery",
                "opening_frame": "Lewis at a makeshift camp table outdoors.",
            },
        ],
    }
    (repo / "narrations" / "narration18040531.json").write_text(json.dumps(narr), encoding="utf-8")
    import pipeline_ui.server as srv

    monkeypatch.setattr(srv, "_REPO_ROOT", repo)
    return repo


def test_narration_segments_payload_dialogue(repo_with_dialogue_narration: Path) -> None:
    del repo_with_dialogue_narration  # repo root patched on module
    out = narration_segments_payload("1804-05-31")
    assert out["ok"] is True
    assert out["date_id"] == "18040531"
    assert out["dialogue_mode"] is True
    assert out["segment_count"] == 2
    assert out["talking_head_segment_count"] == 1
    segs = out["segments"]
    assert segs[0]["visual_mode"] == "b_roll"
    assert segs[1]["dialogue"][0]["speaker_id"] == "lewis"
    assert "lewis" in segs[1]["talking_head_subject"]
    assert "camp table" in segs[1]["opening_frame_full"]
    assert segs[1]["opening_frame_preview"]


def test_segment_cue_fields_from_row_dialogue() -> None:
    row = {
        "visual_mode": "talking_head",
        "talking_head_subject": "lewis",
        "dialogue": [{"speaker_id": "lewis", "text": "We should press on."}],
        "video_prompt": "Portrait interior",
        "talking_head_prompt": "calm delivery",
        "opening_frame": "Lewis at a makeshift camp table outdoors.",
    }
    cue = _segment_cue_fields_from_row(row)
    assert cue["visual_mode"] == "talking_head"
    assert cue["talking_head_subject"] == "lewis"
    assert cue["dialogue"][0]["speaker_id"] == "lewis"
    assert "press on" in cue["dialogue"][0]["text"]
    assert cue["video_prompt"] == "Portrait interior"
    assert cue["talking_head_prompt"] == "calm delivery"
    assert "camp table" in cue["opening_frame"]


def test_video_preview_cues_includes_dialogue_fields(
    repo_with_dialogue_narration: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = repo_with_dialogue_narration
    (repo / "audio" / "18040531").mkdir(parents=True)
    (repo / "audio" / "18040531" / "durations.json").write_text(
        json.dumps(
            [
                {"file": "segments/01.mp3", "duration": 5.0},
                {"file": "segments/02.mp3", "duration": 4.0},
            ]
        ),
        encoding="utf-8",
    )
    out_dir = repo / "output"
    out_dir.mkdir()
    (out_dir / "lewis_clark_18040531_video.mp4").write_bytes(b"\x00")
    import pipeline_ui.server as srv

    monkeypatch.setattr(srv, "_REPO_ROOT", repo)
    payload = video_preview_cues_payload("1804-05-31")
    assert payload["ok"] is True
    assert payload["dialogue_mode"] is True
    assert payload["prompt_pack_hint"] == "lewis_clark_dialogue"
    narrative = [p for p in payload["parts"] if p.get("kind") == "narrative"]
    assert len(narrative) == 2
    th = next(p for p in narrative if p.get("narrative_segment_index") == 2)
    assert th["visual_mode"] == "talking_head"
    assert th["dialogue"][0]["speaker_id"] == "lewis"
    assert th["talking_head_prompt"] == "calm delivery"


def test_narration_segments_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    import pipeline_ui.server as srv

    monkeypatch.setattr(srv, "_REPO_ROOT", repo)
    out = narration_segments_payload("1804-05-31")
    assert out["ok"] is False
    assert out["error"] == "no_file"
