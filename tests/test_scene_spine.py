"""Tests for episode_visual_world → scene_spine merge and vendor continuity suffixes."""

from __future__ import annotations

import json

import pytest

from pipeline.scene_spine import (
    build_scene_spine_from_phase2,
    resolve_core_location_for_segment,
    resolve_lighting_for_segment,
    validate_episode_visual_world,
)
from tests.phase2_fixtures import episode_visual_world_for_segments
from video_vendors import build_prompts


def test_build_scene_spine_from_phase2_uses_visual_world() -> None:
    phase1 = {"episode_metadata": {"location_summary": "Panther Creek on the Missouri River"}}
    phase2 = {
        "video_metadata": {"overall_visual_tone": "grounded"},
        "episode_visual_world": episode_visual_world_for_segments(3),
    }
    spine = build_scene_spine_from_phase2(phase1, phase2)
    assert "Missouri River" in spine["core_location"]
    assert "keelboat" in spine["environmental_elements"]
    assert "Natural" in spine["lighting_progression"]
    assert "Desaturated cool-grey" in spine["color_palette"]
    assert spine["persistent_anchors"] == ["same keelboat at bank", "same tent row"]
    assert len(spine["visual_blocks"]) == 2


def test_build_scene_spine_from_phase2_falls_back_when_color_palette_absent() -> None:
    evw = episode_visual_world_for_segments(1)
    del evw["color_palette"]
    phase2 = {"video_metadata": {}, "episode_visual_world": evw}
    spine = build_scene_spine_from_phase2({}, phase2)
    assert spine["color_palette"] == "Earth tones, muted greens and browns."


def test_resolve_lighting_uses_block_then_falls_back_to_spine_arc() -> None:
    spine = build_scene_spine_from_phase2(
        {"episode_metadata": {"location_summary": "Fallback"}},
        {"video_metadata": {}, "episode_visual_world": episode_visual_world_for_segments(3)},
    )
    assert resolve_lighting_for_segment(spine, 1) == "Flat overcast midday, cool grey light."
    assert resolve_lighting_for_segment(spine, 3) == "Warm low firelight, deep shadows."
    # No visual_blocks at all -> spine-level lighting_progression.
    bare_spine = {"lighting_progression": "Dawn to dusk, natural light."}
    assert resolve_lighting_for_segment(bare_spine, 1) == "Dawn to dusk, natural light."
    assert resolve_lighting_for_segment(None, 1) == ""


def test_resolve_core_location_uses_block_place_label() -> None:
    spine = build_scene_spine_from_phase2(
        {"episode_metadata": {"location_summary": "Fallback"}},
        {"video_metadata": {}, "episode_visual_world": episode_visual_world_for_segments(2)},
    )
    assert "Missouri" in resolve_core_location_for_segment(spine, 1, None)
    assert resolve_core_location_for_segment(spine, 2, "deep woods") == "deep woods"


def test_build_prompts_does_not_append_continuity_suffix(tmp_path, monkeypatch) -> None:
    narrations_dir = tmp_path / "narrations"
    narrations_dir.mkdir()
    spine = build_scene_spine_from_phase2(
        {"episode_metadata": {"location_summary": "Panther Creek"}},
        {"video_metadata": {}, "episode_visual_world": episode_visual_world_for_segments(1)},
    )
    narration = {
        "date_id": "18040531",
        "scene_spine": spine,
        "narration_script": [
            {
                "segment_index": 1,
                "stage_direction": "static",
                "video_prompt": "Crew at camp in wind.",
            }
        ],
    }
    (narrations_dir / "narration18040531.json").write_text(json.dumps(narration), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    prompts = build_prompts("18040531", narrations_dir=narrations_dir, vendor=None)
    assert len(prompts) == 1
    assert "Continuity:" not in prompts[0]
    assert "Crew at camp in wind." in prompts[0]
    assert "missouri river" in prompts[0].lower()


def test_build_prompts_includes_lighting_and_color_grade(tmp_path, monkeypatch) -> None:
    narrations_dir = tmp_path / "narrations"
    narrations_dir.mkdir()
    spine = build_scene_spine_from_phase2(
        {"episode_metadata": {"location_summary": "Panther Creek"}},
        {"video_metadata": {}, "episode_visual_world": episode_visual_world_for_segments(1)},
    )
    narration = {
        "date_id": "18040531",
        "scene_spine": spine,
        "narration_script": [
            {
                "segment_index": 1,
                "stage_direction": "static",
                "video_prompt": "Crew at camp in wind.",
            }
        ],
    }
    (narrations_dir / "narration18040531.json").write_text(json.dumps(narration), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    prompts = build_prompts("18040531", narrations_dir=narrations_dir, vendor=None)
    assert "Lighting: Flat overcast midday, cool grey light. Color grade:" in prompts[0]
    assert "Color grade: Desaturated cool-grey grade" in prompts[0]
    # Source strings already end in "." — guard against a doubled ".." at each join point.
    assert ".." not in prompts[0]


def test_validate_episode_visual_world_requires_color_palette() -> None:
    evw = episode_visual_world_for_segments(1)
    del evw["color_palette"]
    with pytest.raises(ValueError, match="color_palette"):
        validate_episode_visual_world(evw, 1)


def test_validate_episode_visual_world_requires_block_lighting() -> None:
    evw = episode_visual_world_for_segments(1)
    del evw["visual_blocks"][0]["lighting"]
    with pytest.raises(ValueError, match="lighting"):
        validate_episode_visual_world(evw, 1)


def test_validate_episode_visual_world_accepts_full_fixture() -> None:
    validate_episode_visual_world(episode_visual_world_for_segments(3), 3)


def test_build_prompts_clips_overlong_lighting_and_palette(tmp_path, monkeypatch) -> None:
    narrations_dir = tmp_path / "narrations"
    narrations_dir.mkdir()
    evw = episode_visual_world_for_segments(1)
    evw["color_palette"] = "x" * 200
    evw["visual_blocks"][0]["lighting"] = "y" * 200
    spine = build_scene_spine_from_phase2(
        {"episode_metadata": {"location_summary": "Panther Creek"}},
        {"video_metadata": {}, "episode_visual_world": evw},
    )
    narration = {
        "date_id": "18040531",
        "scene_spine": spine,
        "narration_script": [
            {"segment_index": 1, "stage_direction": "static", "video_prompt": "Crew at camp."}
        ],
    }
    (narrations_dir / "narration18040531.json").write_text(json.dumps(narration), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    prompts = build_prompts("18040531", narrations_dir=narrations_dir, vendor=None)
    assert "y" * 200 not in prompts[0]
    assert "x" * 200 not in prompts[0]
    assert "…" in prompts[0]
