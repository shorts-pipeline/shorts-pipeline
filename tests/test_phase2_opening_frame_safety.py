"""Phase 2 validation rejects opening_frame / primary_visual phrases that break FAL anchors."""

from __future__ import annotations

import pytest

import pipeline.narration_phase2 as np2
from tests.phase2_fixtures import episode_visual_world_for_segments


def _scene(opening_frame: str, primary_visual: str = "Hunter walks timber with longarm.") -> dict:
    return {
        "episode_visual_world": episode_visual_world_for_segments(1),
        "scene_plan": [
            {
                "segment_index": 1,
                "visual_strategy": {
                    "primary_visual": primary_visual,
                    "secondary_elements": "",
                    "motion_style": "static",
                    "pacing_note": "brief",
                    "opening_frame": opening_frame,
                },
            }
        ],
        "open_questions": [],
        "editor_notes": "",
    }


def test_validate_rejects_gripping_deer_by_antlers() -> None:
    parsed = _scene("Drouillard stands beside the canoe, gripping a deer by its antlers.")
    with pytest.raises(ValueError, match="antler"):
        np2._validate_phase2(parsed, 1)


def test_validate_rejects_riding_deer() -> None:
    parsed = _scene(
        "Hunter on the bank.",
        primary_visual="Drouillard riding a deer through shallow river water.",
    )
    with pytest.raises(ValueError, match="riding"):
        np2._validate_phase2(parsed, 1)


def test_validate_accepts_opaque_sack_hunt_return() -> None:
    parsed = _scene(
        "Drouillard stands by a beached canoe with one hand on the gunwale.",
        primary_visual="He sets an opaque provisions sack on the muddy bank under overcast sky.",
    )
    np2._validate_phase2(parsed, 1)
