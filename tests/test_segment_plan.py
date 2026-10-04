"""Tests for pipeline/segment_plan.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.segment_plan import build_episode_segment_plans


def _write_narration(root: Path, date_id: str, script: list[dict], **extra: object) -> dict:
    payload = {
        "narration_version": "2.0",
        "title": "t",
        "narration_script": script,
        **extra,
    }
    narr_dir = root / "narrations"
    narr_dir.mkdir(parents=True, exist_ok=True)
    (narr_dir / f"narration{date_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_build_episode_segment_plans_paths_and_flags(tmp_path: Path) -> None:
    date_id = "18040101"
    narr = _write_narration(
        tmp_path,
        date_id,
        [
            {
                "segment_index": 1,
                "stage_direction": "[CUT]",
                "narration": "Beat.",
                "video_prompt": "River.",
                "visual_mode": "b_roll",
            },
            {
                "segment_index": 2,
                "stage_direction": "[CUT]",
                "narration": "Director.",
                "video_prompt": "Camp.",
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "dialogue": [{"speaker_id": "lewis", "text": "Report."}],
            },
        ],
    )
    episode = build_episode_segment_plans(narr, date_id=date_id, repo_root=tmp_path)

    assert episode.date_id == date_id
    assert len(episode) == 2
    assert episode.visual_modes() == ["b_roll", "talking_head"]
    assert episode.talking_head_indices() == {2}
    assert episode.wan_indices() == {1}

    broll = episode.get(1)
    assert broll is not None
    assert broll.is_b_roll
    assert not broll.is_talking_head
    assert broll.expects_one_cast_speaker
    assert broll.audio_mp3 == tmp_path / "audio" / date_id / "segments" / "01.mp3"
    assert broll.clip_mp4 == tmp_path / "movie-images" / date_id / "01.mp4"

    th = episode.get(2)
    assert th is not None
    assert th.is_talking_head
    assert th.talking_head_subject == "lewis"
    assert th.cast_speakers == ("lewis",)
    assert th.talking_head_drive_mp3.name == "02_talking_head_drive.mp3"


def test_long_conversation_b_roll_allows_multi_speaker(tmp_path: Path) -> None:
    date_id = "18040102"
    narr = _write_narration(
        tmp_path,
        date_id,
        [
            {
                "stage_direction": "[CUT]",
                "narration": "Beat.",
                "video_prompt": "River.",
                "visual_mode": "b_roll",
                "dialogue": [
                    {"speaker_id": "lewis", "text": "One."},
                    {"speaker_id": "clark", "text": "Two."},
                ],
            }
        ],
        long_conversation_mode=True,
    )
    plan = build_episode_segment_plans(narr, date_id=date_id, repo_root=tmp_path).get(1)
    assert plan is not None
    assert plan.cast_speakers == ("lewis", "clark")
    assert not plan.expects_one_cast_speaker


def test_scene_anchor_eligible_excludes_b_roll_without_character(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "pipeline.broll_scene_anchor.load_narration_config",
        lambda *a, **k: {"fal_broll_scene_anchor": {"enabled": False}},
    )
    date_id = "18040105"
    narr = _write_narration(
        tmp_path,
        date_id,
        [
            {"visual_mode": "b_roll", "narration": "a", "video_prompt": "a"},
            {
                "visual_mode": "b_roll",
                "narration": "b",
                "video_prompt": "b",
                "reference_character_id": "lewis",
            },
            {
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "narration": "c",
                "video_prompt": "c",
            },
        ],
    )
    portraits = tmp_path / "character-portraits"
    portraits.mkdir()
    (portraits / "lewis.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    episode = build_episode_segment_plans(narr, date_id=date_id, repo_root=tmp_path)
    assert [p.index for p in episode.scene_anchor_eligible()] == [2, 3]


def test_in_scope_filters_segment_indices(tmp_path: Path) -> None:
    date_id = "18040103"
    narr = _write_narration(
        tmp_path,
        date_id,
        [
            {
                "stage_direction": "[CUT]",
                "narration": "a",
                "video_prompt": "a",
                "visual_mode": "b_roll",
            },
            {
                "stage_direction": "[CUT]",
                "narration": "b",
                "video_prompt": "b",
                "visual_mode": "talking_head",
                "talking_head_subject": "clark",
                "dialogue": [{"speaker_id": "clark", "text": "hi"}],
            },
        ],
    )
    episode = build_episode_segment_plans(narr, date_id=date_id, repo_root=tmp_path)
    assert episode.talking_head_indices({2}) == {2}
    assert episode.wan_indices({1}) == {1}
    assert [p.index for p in episode.in_scope({2})] == [2]


def test_anchor_exists_when_still_on_disk(tmp_path: Path) -> None:
    date_id = "18040104"
    narr = _write_narration(
        tmp_path,
        date_id,
        [
            {
                "stage_direction": "[CUT]",
                "narration": "Beat.",
                "video_prompt": "River.",
                "visual_mode": "talking_head",
                "talking_head_subject": "clark",
                "dialogue": [{"speaker_id": "clark", "text": "Line."}],
            }
        ],
    )
    anchors = tmp_path / "movie-images" / date_id / "anchors"
    anchors.mkdir(parents=True)
    still = anchors / "01_clark.png"
    still.write_bytes(b"png")

    plan = build_episode_segment_plans(narr, date_id=date_id, repo_root=tmp_path).get(1)
    assert plan is not None
    assert plan.anchor_path == still
    assert plan.anchor_exists
