"""Tests for pipeline/video_prompt_language_stripper.py (item 6 of
ai-plans/fal-video-quality-followups-2026-09.md): deterministic, best-effort removal of
sound/abstraction language rule 2b/2c already forbids.
"""

from __future__ import annotations

import unittest

from pipeline.narration_phase2 import _synthesize_video_prompt
from pipeline.video_prompt_language_stripper import strip_unrenderable_language


class NoOpTests(unittest.TestCase):
    def test_empty_string(self) -> None:
        self.assertEqual(strip_unrenderable_language(""), "")

    def test_clean_text_unchanged(self) -> None:
        text = "He hauls the rope taut across the deck, boots braced against the gunwale."
        self.assertEqual(strip_unrenderable_language(text), text)

    def test_unmatched_phrasing_is_a_silent_noop(self) -> None:
        # Not in the curated pattern list — should pass through unchanged, not error.
        text = "The men murmur among themselves, a strange feeling passing between them."
        self.assertEqual(strip_unrenderable_language(text), text)


class TrailingTelegraphClauseTests(unittest.TestCase):
    def test_symbolizing_clause_removed(self) -> None:
        text = "The river reflects the muted light, symbolizing the lingering mystery."
        out = strip_unrenderable_language(text)
        self.assertEqual(out, "The river reflects the muted light.")

    def test_heightening_clause_removed(self) -> None:
        text = "The crew is tense, heightening their alertness."
        out = strip_unrenderable_language(text)
        self.assertEqual(out, "The crew is taut.")

    def test_demanding_attention_clause_removed_and_sound_word_swapped(self) -> None:
        text = "The loud report echoes across the landscape, demanding attention."
        out = strip_unrenderable_language(text)
        self.assertNotIn("demanding", out)
        self.assertNotIn("echoes", out)
        self.assertEqual(out, "The loud report carries across the landscape.")


class SoundWordSwapTests(unittest.TestCase):
    def test_crackles_swapped(self) -> None:
        out = strip_unrenderable_language("The fire crackles beside the tent.")
        self.assertEqual(out, "The fire flickers beside the tent.")

    def test_audible_swapped(self) -> None:
        out = strip_unrenderable_language("His breathing is audible in the stillness.")
        self.assertIn("visible", out)
        self.assertNotIn("audible", out)


class InvisibleStateWordSwapTests(unittest.TestCase):
    def test_tense_swapped_to_taut(self) -> None:
        out = strip_unrenderable_language("The crew is tense, gripping their oars tighter.")
        self.assertEqual(out, "The crew is taut, gripping their oars tighter.")


class SameReferentSwapTests(unittest.TestCase):
    def test_wardrobe_callback_stripped(self) -> None:
        text = (
            "William Clark, in the same dark blue field coat, stands at the mud bank near the "
            "moored keelboat."
        )
        out = strip_unrenderable_language(text)
        self.assertNotIn("same", out)
        self.assertIn("in the dark blue field coat", out)

    def test_prop_callback_stripped(self) -> None:
        out = strip_unrenderable_language("Tents visible beyond the same row of canoes.")
        self.assertEqual(out, "Tents visible beyond the row of canoes.")

    def test_capitalized_the_same_preserves_case(self) -> None:
        out = strip_unrenderable_language("The same tent row sits behind him.")
        self.assertEqual(out, "The tent row sits behind him.")

    def test_a_same_and_his_same_left_alone(self) -> None:
        # Only "the same" is a determiner+adjective continuity callback; other phrasings are
        # out of the curated pattern's narrow scope and pass through unchanged.
        text = "He returns to a same spot as always."
        self.assertEqual(strip_unrenderable_language(text), text)


class StandaloneInvisibleStateSentenceTests(unittest.TestCase):
    def test_thick_with_sentence_dropped_between_real_content(self) -> None:
        text = (
            "They row upstream against the current. "
            "The air is thick with anticipation as they discuss potential threats. "
            "The bank narrows ahead of the bow."
        )
        out = strip_unrenderable_language(text)
        self.assertNotIn("thick with", out)
        self.assertIn("They row upstream against the current.", out)
        self.assertIn("The bank narrows ahead of the bow.", out)
        self.assertNotIn("  ", out)

    def test_sense_of_phrase_sentence_dropped(self) -> None:
        text = (
            "He sets his pack down. A sense of relief settles over the party. The fire burns low."
        )
        out = strip_unrenderable_language(text)
        self.assertNotIn("sense of relief", out)
        self.assertIn("He sets his pack down.", out)
        self.assertIn("The fire burns low.", out)

    def test_fallback_to_original_when_stripping_empties_the_field(self) -> None:
        text = "The air is thick with anticipation."
        out = strip_unrenderable_language(text)
        # Entire field was the banned sentence; keep the original rather than ship empty text.
        self.assertEqual(out, text)


class SynthesizeVideoPromptIntegrationTests(unittest.TestCase):
    def _vs(self, primary: str, secondary: str = "", closing: str = "") -> dict:
        return {
            "primary_visual": primary,
            "secondary_elements": secondary,
            "motion_style": "static",
            "pacing_note": "brief",
            "opening_frame": "",
            "closing_frame": closing,
        }

    def test_primary_visual_is_cleaned(self) -> None:
        vs = self._vs("The fire crackles beside the tent, symbolizing warmth and home.")
        text = _synthesize_video_prompt(vs)
        self.assertNotIn("crackles", text)
        self.assertNotIn("symbolizing", text)
        self.assertEqual(text, "The fire flickers beside the tent.")

    def test_closing_frame_is_cleaned_before_clause_wrap(self) -> None:
        vs = self._vs(
            "He hauls the rope taut across the deck.",
            closing="the lashing is cinched tight, underscoring his resolve",
        )
        text = _synthesize_video_prompt(vs)
        self.assertNotIn("underscoring", text)
        self.assertIn("By the end of the shot, the lashing is cinched tight.", text)


if __name__ == "__main__":
    unittest.main()
