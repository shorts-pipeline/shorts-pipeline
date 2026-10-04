"""Tests for pipeline.talking_head_prompt_merge."""

from __future__ import annotations

import unittest
import unittest.mock as mock
from pathlib import Path

from pipeline.narration_phase2 import merge_phase1_phase2
from pipeline.talking_head_prompt_merge import (
    build_merged_talking_head_prompt,
    extract_eyeline_snippet,
    talking_head_prompt_missing_warning,
)


def _vs(primary: str, *, motion: str = "static", opening: str = "") -> dict:
    return {
        "primary_visual": primary,
        "secondary_elements": "",
        "motion_style": motion,
        "pacing_note": "brief",
        "opening_frame": opening,
    }


class TalkingHeadPromptMergeTests(unittest.TestCase):
    def test_extract_eyeline_snippet(self) -> None:
        frame = (
            "Head-and-shoulders portrait, lewis, neutral mouth. "
            "Eyes toward off-camera partner in three-quarter view."
        )
        snip = extract_eyeline_snippet(frame)
        self.assertIn("off-camera", snip.lower())

    def test_missing_warning_only_omnihuman_v15(self) -> None:
        self.assertIsNotNone(
            talking_head_prompt_missing_warning(3, "fal-ai/bytedance/omnihuman/v1.5")
        )
        self.assertIsNone(talking_head_prompt_missing_warning(3, "fal-ai/bytedance/omnihuman"))
        self.assertIsNone(talking_head_prompt_missing_warning(3, "fal-ai/sadtalker"))

    def test_build_merged_includes_director_note_and_motion(self) -> None:
        root = Path(__file__).resolve().parent.parent
        seg = {
            "talking_head_prompt": "He speaks firmly about the river.",
            "conversation_tracking": {"director_note": "Lean in; jaw set."},
        }
        out = build_merged_talking_head_prompt(
            seg,
            opening_frame="Steady gaze toward off-camera Clark.",
            motion_style="slow_push",
            in_ping_pong_run=False,
            repo_root=root,
        )
        assert out is not None
        self.assertIn("He speaks firmly", out)
        self.assertIn("Lean in", out)
        self.assertIn("slowly pushes in", out.lower())
        self.assertIn("off-camera", out.lower())

    def test_ping_pong_defaults_ots_shot(self) -> None:
        root = Path(__file__).resolve().parent.parent
        seg = {"talking_head_prompt": "Calm delivery while speaking."}
        out = build_merged_talking_head_prompt(
            seg,
            in_ping_pong_run=True,
            repo_root=root,
        )
        assert out is not None
        self.assertIn("over-the-listener", out.lower())

    def test_merge_enriches_talking_head_prompt(self) -> None:
        root = Path(__file__).resolve().parent.parent
        phase1 = {
            "segments": [
                {"visual_mode": "b_roll", "narration": "a", "dialogue": []},
                {
                    "visual_mode": "talking_head",
                    "talking_head_subject": "lewis",
                    "talking_head_prompt": "Speaks with quiet urgency.",
                    "conversation_tracking": {"director_note": "Eyes on Clark."},
                    "narration": "c",
                    "dialogue": [{"speaker_id": "lewis", "text": "We wait."}],
                },
                {
                    "visual_mode": "talking_head",
                    "talking_head_subject": "clark",
                    "talking_head_prompt": "Answers evenly.",
                    "narration": "d",
                    "dialogue": [{"speaker_id": "clark", "text": "Agreed."}],
                },
            ],
        }
        phase2 = {
            "scene_plan": [
                {"segment_index": 1, "visual_strategy": _vs("river")},
                {
                    "segment_index": 2,
                    "visual_strategy": _vs(
                        "lewis portrait",
                        motion="slow_push",
                        opening="Three-quarter toward off-camera listener.",
                    ),
                },
                {
                    "segment_index": 3,
                    "visual_strategy": _vs(
                        "clark portrait",
                        opening="Returns gaze toward Lewis off-camera.",
                    ),
                },
            ],
            "video_metadata": {},
            "map_insertions": [],
        }
        with mock.patch(
            "pipeline.narration_phase2.character_portrait_asset_exists",
            return_value=True,
        ):
            merged, _ = merge_phase1_phase2(
                phase1,
                phase2,
                dialogue_mode=True,
                repo_root=root,
            )
        th_lewis = merged["narration_script"][1].get("talking_head_prompt") or ""
        th_clark = merged["narration_script"][2].get("talking_head_prompt") or ""
        self.assertIn("quiet urgency", th_lewis)
        self.assertIn("Eyes on Clark", th_lewis)
        self.assertIn("slowly pushes in", th_lewis.lower())
        self.assertIn("over-the-listener", th_clark.lower())
        self.assertNotIn("video_prompt", merged["narration_script"][1])


if __name__ == "__main__":
    unittest.main()
