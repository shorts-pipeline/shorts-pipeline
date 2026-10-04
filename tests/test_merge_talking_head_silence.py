"""Merge: talking_head rows omit video_prompt; director notes stay off b-roll-style fields."""

from __future__ import annotations

import unittest
import unittest.mock as mock

from pipeline.narration_phase2 import merge_phase1_phase2


def _vs(primary: str) -> dict:
    return {
        "primary_visual": primary,
        "secondary_elements": "",
        "motion_style": "static",
        "pacing_note": "brief",
        "opening_frame": "",
    }


class MergeTalkingHeadSilenceTests(unittest.TestCase):
    def test_talking_head_omits_video_prompt(self) -> None:
        phase1 = {
            "segments": [
                {"visual_mode": "b_roll", "narration": "a", "dialogue": []},
                {"visual_mode": "b_roll", "narration": "b", "dialogue": []},
                {
                    "visual_mode": "talking_head",
                    "talking_head_subject": "lewis",
                    "narration": "c",
                    "dialogue": [{"speaker_id": "lewis", "text": "River's up."}],
                },
            ],
        }
        phase2 = {
            "scene_plan": [
                {"segment_index": 1, "visual_strategy": _vs("wide river")},
                {"segment_index": 2, "visual_strategy": _vs("camp morning")},
                {"segment_index": 3, "visual_strategy": _vs("tight portrait lewis")},
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
            )
        rows = merged["narration_script"]
        self.assertNotIn("video_prompt", rows[2])
        self.assertIn("talking_head_prompt", rows[2])
        of = (rows[2].get("opening_frame") or "").strip()
        self.assertTrue(
            of, "merge should supply opening_frame for talking_head when Phase 2 omits it"
        )

    def test_b_roll_segment_has_no_silence_suffix(self) -> None:
        phase1 = {
            "segments": [
                {"visual_mode": "b_roll", "narration": "a", "dialogue": []},
            ],
        }
        phase2 = {
            "scene_plan": [{"segment_index": 1, "visual_strategy": _vs("hook action")}],
            "video_metadata": {},
            "map_insertions": [],
        }
        merged, _ = merge_phase1_phase2(phase1, phase2, dialogue_mode=False)
        vp = merged["narration_script"][0]["video_prompt"]
        self.assertNotIn("leading silence", vp)


if __name__ == "__main__":
    unittest.main()
