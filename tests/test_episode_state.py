"""Tests for pipeline/episode_state.py."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.episode_state import (
    build_episode_state,
    episode_state_sidecar_path,
    load_episode_state_sidecar,
    refresh_episode_state_sidecar,
)


def _write_narration(root: Path, date_id: str, script: list[dict]) -> None:
    narr_dir = root / "narrations"
    narr_dir.mkdir(parents=True, exist_ok=True)
    (narr_dir / f"narration{date_id}.json").write_text(
        json.dumps(
            {
                "narration_version": "2.0",
                "title": "Test day",
                "narration_script": script,
            }
        ),
        encoding="utf-8",
    )


def test_build_episode_state_no_narration(tmp_path: Path) -> None:
    state = build_episode_state(tmp_path, "18040101")
    assert state["narration"]["exists"] is False
    assert "Missing narration JSON" in state["summary"]["blocking"]


def test_build_episode_state_tracks_artifacts(tmp_path: Path) -> None:
    date_id = "18040102"
    _write_narration(
        tmp_path,
        date_id,
        [
            {
                "segment_index": 1,
                "visual_mode": "b_roll",
                "narration": "Wide.",
                "video_prompt": "River.",
            },
            {
                "segment_index": 2,
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "narration": "Line.",
                "video_prompt": "Camp.",
                "dialogue": [{"speaker_id": "lewis", "text": "Report."}],
            },
        ],
    )
    audio_seg = tmp_path / "audio" / date_id / "segments"
    audio_seg.mkdir(parents=True)
    (audio_seg / "01.mp3").write_bytes(b"mp3")
    (audio_seg / "02.mp3").write_bytes(b"mp3")
    (tmp_path / "audio" / date_id / "durations.json").write_text(
        json.dumps(
            [
                {"file": "segments/01.mp3", "duration": 1.0},
                {"file": "segments/02.mp3", "duration": 2.0},
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "audio" / date_id / "final.mp3").write_bytes(b"mp3")
    movie = tmp_path / "movie-images" / date_id
    movie.mkdir(parents=True)
    (movie / "01.mp4").write_bytes(b"mp4")

    state = build_episode_state(tmp_path, date_id)
    assert state["narration"]["segment_count"] == 2
    assert state["tts"]["ready"] is True
    assert state["video"]["clip_count"] == 1
    assert state["video"]["missing_clip_indices"] == [2]
    assert state["segments"][1]["visual_mode"] == "talking_head"
    assert any("Missing video clips" in b for b in state["summary"]["blocking"])


def test_refresh_writes_sidecar(tmp_path: Path) -> None:
    date_id = "18040103"
    _write_narration(
        tmp_path,
        date_id,
        [{"visual_mode": "b_roll", "narration": "a", "video_prompt": "a"}],
    )
    state = refresh_episode_state_sidecar(tmp_path, date_id, source="test")
    path = episode_state_sidecar_path(tmp_path, date_id)
    assert path.is_file()
    loaded = load_episode_state_sidecar(tmp_path, date_id)
    assert loaded is not None
    assert loaded["source"] == "test"
    assert loaded["date_id"] == date_id
    assert state["episode_state_version"] == "1.0"
