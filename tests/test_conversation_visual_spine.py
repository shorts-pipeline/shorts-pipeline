"""Tests for conversation mini-spine / shared backdrop merge helpers."""

from __future__ import annotations

import pytest

from pipeline.conversation_visual_spine import (
    conversation_run_setting_by_segment_index,
    find_talking_head_conversation_runs,
    inject_conversation_backdrop,
    resolve_conversation_master_expression,
    resolve_conversation_shared_setting,
    sync_conversation_place_in_scene_spine,
    validate_conversation_micro_arc,
)
from pipeline.narration_phase2 import merge_phase1_phase2


def _th(seg_idx: int, subject: str) -> dict:
    return {
        "segment_type": "action",
        "visual_mode": "talking_head",
        "talking_head_subject": subject,
        "narration": "Director beat.",
        "dialogue": [{"speaker_id": subject, "text": "line"}],
        "conversation_tracking": {
            "turn_index": seg_idx,
            "speaker_focus": subject,
            "setting_state": "Windbound keelboat deck, overcast midday.",
            "continuity_carry": "thread",
            "director_note": "Tight portrait.",
        },
    }


def test_find_talking_head_conversation_runs_alternating() -> None:
    segments = [
        {"visual_mode": "b_roll", "narration": "hook"},
        _th(2, "lewis"),
        _th(3, "clark"),
        _th(4, "lewis"),
        _th(5, "clark"),
    ]
    runs = find_talking_head_conversation_runs(segments)
    assert runs == [[1, 2, 3, 4]]


def test_resolve_shared_setting_prefers_micro_arc() -> None:
    phase1 = {
        "conversation_micro_arc": {
            "shared_setting": "Same gunwale patch, tents behind.",
            "persistent_props": ["coiled hawser"],
        }
    }
    setting = resolve_conversation_shared_setting(phase1, [], [1, 2])
    assert "Same gunwale patch" in setting
    assert "coiled hawser" in setting


def test_resolve_master_expression_prefers_micro_arc() -> None:
    phase1 = {
        "conversation_micro_arc": {
            "master_expression": "Solemn grave faces, no smiles.",
        },
        "tone_register": "reflective",
    }
    assert (
        resolve_conversation_master_expression(phase1, [], [1, 2])
        == "Solemn grave faces, no smiles."
    )


def test_resolve_master_expression_falls_back_to_tone_register() -> None:
    phase1 = {"tone_register": "reflective"}
    expr = resolve_conversation_master_expression(phase1, [], [1, 2])
    assert "reflective mood" in expr


def test_sync_conversation_place_in_scene_spine() -> None:
    phase1 = {
        "scene_spine": {
            "visual_blocks": [
                {"segment_indices": [1, 2], "place_label": "River"},
                {"segment_indices": [3, 4], "place_label": "Deck of the keelboat"},
            ]
        }
    }
    assert sync_conversation_place_in_scene_spine(
        phase1, [3, 4], "Grassy hill above the Missouri River."
    )
    assert (
        phase1["scene_spine"]["visual_blocks"][1]["place_label"]
        == "Grassy hill above the Missouri River"
    )
    assert phase1["scene_spine"]["visual_blocks"][0]["place_label"] == "River"


def test_inject_conversation_backdrop_appends_when_missing() -> None:
    out = inject_conversation_backdrop(
        "Clark faces camera-left, jaw set.",
        subject_id="clark",
        shared_setting="Windbound deck, Missouri bank.",
    )
    assert "Clark faces camera-left" in out
    assert "Windbound deck, Missouri bank." in out


def test_validate_conversation_micro_arc_rejects_bad_speakers() -> None:
    with pytest.raises(ValueError, match="speakers"):
        validate_conversation_micro_arc(
            {
                "conversation_micro_arc": {
                    "shared_setting": "x",
                    "speakers": ["a"],
                    "segment_indices": [1],
                }
            },
            5,
        )


def test_merge_injects_shared_backdrop_into_conversation_opening_frames(tmp_path) -> None:
    root = tmp_path
    shared = "Windbound camp on Missouri bank: keelboat moored stage-left, tent row soft behind."
    phase1 = {
        "title": "t",
        "episode_metadata": {"location_summary": "Missouri River"},
        "conversation_micro_arc": {
            "speakers": ["lewis", "clark"],
            "segment_indices": [2, 3],
            "shared_setting": shared,
            "spatial_relationship": "same deck",
            "persistent_props": ["tent row"],
        },
        "segments": [
            {"segment_type": "hook", "visual_mode": "b_roll", "narration": "Hook.", "dialogue": []},
            _th(2, "lewis"),
            _th(3, "clark"),
        ],
    }
    from tests.phase2_fixtures import episode_visual_world_for_segments

    phase2 = {
        "video_metadata": {
            "overall_visual_tone": "grounded",
            "estimated_duration_seconds": 120,
            "map_usage": "none",
        },
        "episode_visual_world": episode_visual_world_for_segments(3),
        "scene_plan": [
            {
                "segment_index": 1,
                "segment_type": "hook",
                "visual_strategy": {
                    "primary_visual": "Camp bustle.",
                    "secondary_elements": "",
                    "motion_style": "static",
                    "pacing_note": "brief",
                },
            },
            {
                "segment_index": 2,
                "segment_type": "action",
                "core_location_override": "Forest margin",
                "visual_strategy": {
                    "primary_visual": "Lewis tight portrait.",
                    "secondary_elements": "",
                    "motion_style": "static",
                    "pacing_note": "brief",
                    "opening_frame": "Lewis, neutral mouth, eyes toward camera-right.",
                },
            },
            {
                "segment_index": 3,
                "segment_type": "action",
                "core_location_override": "River sandbar",
                "visual_strategy": {
                    "primary_visual": "Clark tight portrait.",
                    "secondary_elements": "",
                    "motion_style": "static",
                    "pacing_note": "brief",
                    "opening_frame": "Clark, steady gaze toward camera-left.",
                },
            },
        ],
    }
    merged, _ = merge_phase1_phase2(
        phase1,
        phase2,
        dialogue_mode=True,
        long_conversation_mode=True,
        repo_root=root,
    )
    assert merged.get("conversation_micro_arc", {}).get("shared_setting") == shared
    lewis = merged["narration_script"][1]
    clark = merged["narration_script"][2]
    assert shared in lewis["opening_frame"]
    assert shared in clark["opening_frame"]
    assert "core_location_override" not in lewis
    assert "core_location_override" not in clark


def test_conversation_run_setting_by_segment_index_without_micro_arc() -> None:
    segments = [
        {"visual_mode": "b_roll", "narration": "x"},
        _th(2, "lewis"),
        _th(3, "clark"),
    ]
    phase1 = {"episode_metadata": {"location_summary": "Camp Dubois"}}
    m = conversation_run_setting_by_segment_index(phase1, segments, long_conversation_mode=True)
    assert m == {
        1: "Windbound keelboat deck, overcast midday.",
        2: "Windbound keelboat deck, overcast midday.",
    }
