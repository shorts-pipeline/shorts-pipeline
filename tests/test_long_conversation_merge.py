"""Merge behavior for long-conversation optional segment metadata."""

from __future__ import annotations

from pathlib import Path

from pipeline.narration_phase2 import merge_phase1_phase2
from pipeline.narration_utils import narration_json_expects_long_conversation_mode


def test_merge_prepends_director_note_and_keeps_conversation_tracking(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    phase1 = {
        "title": "t",
        "episode_metadata": {"location_summary": "River camp"},
        "segments": [
            {
                "segment_type": "hook",
                "visual_mode": "b_roll",
                "narration": "Men worked the line.",
                "dialogue": [],
                "conversation_tracking": {
                    "turn_index": 1,
                    "director_note": "Deck-level; coil still slack.",
                    "setting_state": "afternoon keelboat deck",
                    "continuity_carry": "",
                    "speaker_focus": "narrator",
                },
            }
        ],
    }
    from tests.phase2_fixtures import episode_visual_world_for_segments

    phase2 = {
        "video_metadata": {
            "overall_visual_tone": "grounded",
            "estimated_duration_seconds": 120,
            "map_usage": "none",
        },
        "episode_visual_world": episode_visual_world_for_segments(1),
        "scene_plan": [
            {
                "segment_index": 1,
                "segment_type": "hook",
                "visual_strategy": {
                    "primary_visual": "Deck hands tighten a hawser.",
                    "secondary_elements": "",
                    "motion_style": "static",
                    "pacing_note": "brief",
                },
            }
        ],
    }
    merged, _ = merge_phase1_phase2(
        phase1,
        phase2,
        dialogue_mode=True,
        long_conversation_mode=True,
        repo_root=root,
    )
    assert merged["long_conversation_mode"] is True
    assert "episode_visual_world" not in merged
    ns = merged["narration_script"][0]
    assert ns.get("segment_type") == "hook"
    assert ns["video_prompt"].startswith("Deck-level; coil still slack.")
    ct = ns.get("conversation_tracking")
    assert isinstance(ct, dict)
    assert ct.get("director_note") == "Deck-level; coil still slack."


def test_expects_long_conversation_from_bool() -> None:
    assert narration_json_expects_long_conversation_mode({"long_conversation_mode": True}) is True
    assert narration_json_expects_long_conversation_mode({"long_conversation_mode": False}) is False
