"""Tests for closing_frame (rule 2 closing_frame): begin/end trajectory in the FAL/Wan video_prompt.

See ai-plans/fal-video-quality-followups-2026-09.md item 5.
"""

from __future__ import annotations

import unittest
import unittest.mock as mock
from pathlib import Path

import pytest

import pipeline.narration_phase2 as np2
from pipeline.narration_phase2 import _synthesize_video_prompt, merge_phase1_phase2
from tests.phase2_fixtures import episode_visual_world_for_segments


def _vs(primary: str, closing: str = "") -> dict:
    return {
        "primary_visual": primary,
        "secondary_elements": "",
        "motion_style": "static",
        "pacing_note": "brief",
        "opening_frame": "",
        "closing_frame": closing,
    }


class SynthesizeVideoPromptTests(unittest.TestCase):
    def test_appends_closing_clause_lowercased(self) -> None:
        vs = _vs("He hauls the rope taut.", "the lashing is cinched tight against the gunwale.")
        text = _synthesize_video_prompt(vs)
        self.assertTrue(text.startswith("He hauls the rope taut."))
        self.assertIn("By the end of the shot, the lashing is cinched tight", text)

    def test_strips_trailing_period_before_rejoin(self) -> None:
        vs = _vs("He hauls the rope taut.", "The lashing is cinched tight.")
        text = _synthesize_video_prompt(vs)
        self.assertNotIn("..", text)
        self.assertTrue(text.endswith("."))

    def test_no_closing_frame_is_unchanged(self) -> None:
        vs = _vs("He hauls the rope taut.")
        text = _synthesize_video_prompt(vs)
        self.assertEqual(text, "He hauls the rope taut.")

    def test_closing_alone_when_primary_missing(self) -> None:
        vs = {"closing_frame": "the canoe drifts clear of the eddy."}
        text = _synthesize_video_prompt(vs)
        self.assertEqual(text, "By the end of the shot, the canoe drifts clear of the eddy.")


class ValidatePhase2ClosingFrameTests(unittest.TestCase):
    def _parsed(self, closing_frame: str) -> dict:
        return {
            "episode_visual_world": episode_visual_world_for_segments(1),
            "scene_plan": [
                {
                    "segment_index": 1,
                    "visual_strategy": _vs("Hunter walks timber with longarm.", closing_frame),
                }
            ],
            "open_questions": [],
            "editor_notes": "",
        }

    def test_rejects_missing_closing_frame_for_b_roll(self) -> None:
        parsed = self._parsed("")
        phase1_segments = [{"visual_mode": "b_roll"}]
        with pytest.raises(ValueError, match="closing_frame"):
            np2._validate_phase2(parsed, 1, None, phase1_segments=phase1_segments)

    def test_accepts_closing_frame_for_b_roll(self) -> None:
        parsed = self._parsed("he lowers the longarm, sign read on the muddy trail.")
        phase1_segments = [{"visual_mode": "b_roll"}]
        np2._validate_phase2(parsed, 1, None, phase1_segments=phase1_segments)

    def test_talking_head_does_not_require_closing_frame(self) -> None:
        parsed = self._parsed("")
        phase1_segments = [{"visual_mode": "talking_head", "talking_head_subject": "lewis"}]
        with mock.patch.object(np2, "character_portrait_asset_exists", return_value=False):
            np2._validate_phase2(parsed, 1, None, phase1_segments=phase1_segments)

    def test_skipped_without_phase1_segments(self) -> None:
        parsed = self._parsed("")
        np2._validate_phase2(parsed, 1)


class MergeIncludesClosingFrameTests(unittest.TestCase):
    def test_merge_video_prompt_has_trajectory(self) -> None:
        root = Path(__file__).resolve().parent.parent
        phase1 = {
            "segments": [
                {"visual_mode": "b_roll", "narration": "a", "dialogue": []},
            ],
        }
        phase2 = {
            "scene_plan": [
                {
                    "segment_index": 1,
                    "visual_strategy": _vs(
                        "He hauls the rope taut across the deck.",
                        "the lashing is cinched tight and the load stops rocking.",
                    ),
                },
            ],
            "video_metadata": {},
            "map_insertions": [],
        }
        merged, _ = merge_phase1_phase2(phase1, phase2, dialogue_mode=True, repo_root=root)
        video_prompt = merged["narration_script"][0]["video_prompt"]
        self.assertIn("He hauls the rope taut", video_prompt)
        self.assertIn("By the end of the shot, the lashing is cinched tight", video_prompt)


if __name__ == "__main__":
    unittest.main()
