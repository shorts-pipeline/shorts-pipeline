"""Structural merge for phase1-dialogue polish (no API)."""

import unittest

from pipeline.narration_phase1_dialogue import (
    build_phase1_dialogue_user_prompt,
    merge_phase1_dialogue_texts_only,
)


class Phase1DialogueMergeTests(unittest.TestCase):
    def test_merge_updates_only_dialogue_text(self) -> None:
        base = {
            "title": "T",
            "segments": [
                {"narration": "keep", "dialogue": [{"speaker_id": "lewis", "text": "draft"}]},
                {"narration": "n2", "dialogue": []},
            ],
        }
        model_segments = [
            {"dialogue": [{"speaker_id": "lewis", "text": "polished"}]},
            {"dialogue": []},
        ]
        out = merge_phase1_dialogue_texts_only(base, model_segments)
        self.assertEqual(out["title"], "T")
        self.assertEqual(out["segments"][0]["narration"], "keep")
        self.assertEqual(out["segments"][0]["dialogue"][0]["text"], "polished")
        self.assertEqual(out["segments"][0]["dialogue"][0]["speaker_id"], "lewis")
        self.assertEqual(out["segments"][1]["dialogue"], [])

    def test_speaker_id_change_raises(self) -> None:
        base = {"segments": [{"dialogue": [{"speaker_id": "lewis", "text": "a"}]}]}
        model_segments = [{"dialogue": [{"speaker_id": "clark", "text": "b"}]}]
        with self.assertRaises(ValueError):
            merge_phase1_dialogue_texts_only(base, model_segments)

    def test_segment_count_mismatch_raises(self) -> None:
        base = {"segments": [{"dialogue": []}]}
        with self.assertRaises(ValueError):
            merge_phase1_dialogue_texts_only(base, [])

    def test_user_prompt_includes_conversation_micro_arc_hint(self) -> None:
        phase1 = {
            "conversation_micro_arc": {
                "speakers": ["clark", "lewis"],
                "segment_indices": [3, 4, 5, 6, 7],
            },
            "segments": [{"dialogue": []}],
        }
        prompt = build_phase1_dialogue_user_prompt(phase1, "18040616")
        self.assertIn("CONVERSATION MICRO-ARC", prompt)
        self.assertIn("Segments 3, 4, 5, 6, 7", prompt)
        self.assertIn("`clark` and `lewis`", prompt)
        self.assertIn("addresses the **other** roster member", prompt)
        self.assertIn("at most once or twice", prompt)

    def test_user_prompt_omits_arc_hint_without_micro_arc(self) -> None:
        prompt = build_phase1_dialogue_user_prompt({"segments": []}, "18040616")
        self.assertNotIn("CONVERSATION MICRO-ARC", prompt)


if __name__ == "__main__":
    unittest.main()
