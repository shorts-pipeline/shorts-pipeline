"""Tests for pipeline.broll_scene_anchor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.anchor_preview import scene_anchor_eligible_segments
from pipeline.broll_scene_anchor import (
    broll_scene_anchor_enabled,
    plan_eligible_for_scene_anchor,
    plan_is_b_roll,
    scene_anchor_eligible_for_visual_mode,
    scene_anchor_eligible_plans,
    segment_eligible_for_scene_anchor,
    segment_has_portrait_backed_character,
    segment_is_b_roll,
)
from pipeline.segment_plan import build_episode_segment_plans


@pytest.fixture
def repo_with_modes(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path
    did = "18030101"
    narr_dir = repo / "narrations"
    narr_dir.mkdir(parents=True)
    cfg_dir = repo / "config"
    cfg_dir.mkdir()
    (cfg_dir / "narration_config.json").write_text(
        json.dumps({"fal_broll_scene_anchor": {"enabled": False}}),
        encoding="utf-8",
    )
    script = [
        {
            "segment_index": 1,
            "narration": "Wide river.",
            "video_prompt": "River bank.",
            "visual_mode": "b_roll",
        },
        {
            "segment_index": 2,
            "narration": "Clark on the river.",
            "video_prompt": "Close on Clark.",
            "visual_mode": "b_roll",
            "reference_character_id": "clark",
        },
        {
            "segment_index": 3,
            "narration": "Lewis speaks.",
            "video_prompt": "Close on Lewis.",
            "visual_mode": "talking_head",
            "talking_head_subject": "lewis",
        },
    ]
    (narr_dir / f"narration{did}.json").write_text(
        json.dumps(
            {
                "narration_version": "2.0",
                "narration_script": script,
                "scene_spine": {"core_location": "Missouri River"},
            }
        ),
        encoding="utf-8",
    )
    portraits = repo / "character-portraits"
    portraits.mkdir()
    (portraits / "clark.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (portraits / "lewis.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return repo, did


def test_broll_scene_anchor_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NARRATION_CONFIG", raising=False)
    monkeypatch.setattr(
        "pipeline.broll_scene_anchor.load_narration_config",
        lambda *a, **k: {},
    )
    assert broll_scene_anchor_enabled() is False


def test_segment_is_b_roll() -> None:
    narr = {
        "narration_script": [
            {"segment_index": 1, "visual_mode": "b_roll", "narration": "x"},
            {"segment_index": 2, "visual_mode": "talking_head", "narration": "y"},
        ]
    }
    assert segment_is_b_roll(narr, 1) is True
    assert segment_is_b_roll(narr, 2) is False


def test_portrait_backed_b_roll_eligible_when_global_flag_off(
    repo_with_modes: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, did = repo_with_modes
    monkeypatch.setattr(
        "pipeline.broll_scene_anchor.load_narration_config",
        lambda *a, **k: {"fal_broll_scene_anchor": {"enabled": False}},
    )
    narr = json.loads((repo / "narrations" / f"narration{did}.json").read_text())
    assert segment_eligible_for_scene_anchor(narr, 1) is False
    assert segment_eligible_for_scene_anchor(narr, 2) is True
    assert segment_eligible_for_scene_anchor(narr, 3) is True
    eligible = scene_anchor_eligible_segments(did, repo_root=repo)
    assert [e.segment_index for e in eligible] == [2, 3]


def test_plan_helpers_match_row_helpers(
    repo_with_modes: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, did = repo_with_modes
    monkeypatch.setattr(
        "pipeline.broll_scene_anchor.load_narration_config",
        lambda *a, **k: {"fal_broll_scene_anchor": {"enabled": False}},
    )
    narr = json.loads((repo / "narrations" / f"narration{did}.json").read_text())
    episode = build_episode_segment_plans(narr, date_id=did, repo_root=repo)
    broll_no_char = episode.get(1)
    broll_clark = episode.get(2)
    th_plan = episode.get(3)
    assert broll_no_char is not None and broll_clark is not None and th_plan is not None
    assert segment_is_b_roll(narr, 1) and plan_is_b_roll(broll_no_char)
    assert segment_is_b_roll(narr, 2) and plan_is_b_roll(broll_clark)
    assert segment_eligible_for_scene_anchor(narr, 1) == plan_eligible_for_scene_anchor(
        broll_no_char
    )
    assert segment_eligible_for_scene_anchor(narr, 2) == plan_eligible_for_scene_anchor(broll_clark)
    assert segment_eligible_for_scene_anchor(narr, 3) == plan_eligible_for_scene_anchor(th_plan)
    row = narr["narration_script"][1]
    assert segment_has_portrait_backed_character(row) is True
    assert scene_anchor_eligible_for_visual_mode("b_roll", segment_row=row) is True
    assert scene_anchor_eligible_for_visual_mode("talking_head") is True
    assert [p.index for p in scene_anchor_eligible_plans(episode)] == [2, 3]


def test_eligible_includes_all_b_roll_when_global_enabled(
    repo_with_modes: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, did = repo_with_modes
    monkeypatch.setattr(
        "pipeline.broll_scene_anchor.load_narration_config",
        lambda *a, **k: {"fal_broll_scene_anchor": {"enabled": True}},
    )
    eligible = scene_anchor_eligible_segments(did, repo_root=repo)
    assert len(eligible) == 3
