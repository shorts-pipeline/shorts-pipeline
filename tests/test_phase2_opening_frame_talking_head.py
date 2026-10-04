"""Phase 2 validation: talking_head segments require opening_frame when portrait exists."""

from __future__ import annotations

import unittest.mock as mock

import pytest

import pipeline.narration_phase2 as np2
from tests.phase2_fixtures import episode_visual_world_for_segments


def _minimal_phase2_scene(opening_frame: str) -> dict:
    return {
        "episode_visual_world": episode_visual_world_for_segments(1),
        "scene_plan": [
            {
                "segment_index": 1,
                "visual_strategy": {
                    "primary_visual": "Tight head-and-shoulders portrait.",
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


def test_validate_rejects_empty_opening_frame_for_talking_head() -> None:
    parsed = _minimal_phase2_scene("")
    phase1_segments = [{"visual_mode": "talking_head", "talking_head_subject": "lewis"}]
    with mock.patch.object(np2, "character_portrait_asset_exists", return_value=True):
        with pytest.raises(ValueError, match="talking_head"):
            np2._validate_phase2(parsed, 1, None, phase1_segments=phase1_segments)


def test_validate_accepts_opening_frame_for_talking_head() -> None:
    parsed = _minimal_phase2_scene(
        "Lewis head and shoulders, neutral mouth, firelit tent interior behind him."
    )
    phase1_segments = [{"visual_mode": "talking_head", "talking_head_subject": "lewis"}]
    with mock.patch.object(np2, "character_portrait_asset_exists", return_value=True):
        np2._validate_phase2(parsed, 1, None, phase1_segments=phase1_segments)


def test_validate_skips_talking_head_when_no_portrait_asset() -> None:
    parsed = _minimal_phase2_scene("")
    phase1_segments = [{"visual_mode": "talking_head", "talking_head_subject": "nobody_xyz"}]
    with mock.patch.object(np2, "character_portrait_asset_exists", return_value=False):
        np2._validate_phase2(parsed, 1, None, phase1_segments=phase1_segments)
