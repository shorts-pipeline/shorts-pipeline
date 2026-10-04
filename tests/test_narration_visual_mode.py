"""Tests for talking_head / b_roll Phase 1 validation."""

from __future__ import annotations

import unittest
import unittest.mock as mock

from pipeline.narration_characters.types import PairPortraitRule
from pipeline.narration_visual_mode import (
    MAX_CONSECUTIVE_TALKING_HEAD_LONG,
    VISUAL_MODE_B_ROLL,
    VISUAL_MODE_TALKING_HEAD,
    validate_dialogue_visual_modes,
    visual_modes_for_narration_script,
)


class NarrationVisualModeTests(unittest.TestCase):
    def test_visual_modes_for_narration_script_defaults(self) -> None:
        data = {
            "narration_version": "2.0",
            "narration_script": [
                {"segment_index": 1, "narration": "a"},
                {"segment_index": 2, "narration": "b", "visual_mode": "talking_head"},
            ],
        }
        self.assertEqual(
            visual_modes_for_narration_script(data),
            [VISUAL_MODE_B_ROLL, VISUAL_MODE_TALKING_HEAD],
        )

    def test_validate_talking_head_ok(self) -> None:
        segments = [
            {"visual_mode": "b_roll", "narration": "x", "dialogue": []},
            {"visual_mode": "b_roll", "narration": "y", "dialogue": []},
            {
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "narration": "z",
                "dialogue": [{"speaker_id": "lewis", "text": "I speak alone."}],
            },
        ]
        allowed = frozenset({"lewis", "clark", "narrator"})
        with mock.patch(
            "pipeline.narration_characters.storage.character_portrait_asset_exists",
            return_value=True,
        ):
            validate_dialogue_visual_modes(segments, allowed_speakers=allowed)

    def test_validate_talking_head_too_early(self) -> None:
        segments = [
            {"visual_mode": "b_roll", "narration": "x", "dialogue": []},
            {
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "narration": "z",
                "dialogue": [{"speaker_id": "lewis", "text": "Too soon."}],
            },
        ]
        allowed = frozenset({"lewis", "narrator"})
        with mock.patch(
            "pipeline.narration_characters.storage.character_portrait_asset_exists",
            return_value=True,
        ):
            with self.assertRaisesRegex(ValueError, r"First .*talking_head"):
                validate_dialogue_visual_modes(segments, allowed_speakers=allowed)

    def test_validate_talking_head_rejects_pair_composite_reference(self) -> None:
        segments = [
            {"visual_mode": "b_roll", "narration": "a", "dialogue": []},
            {"visual_mode": "b_roll", "narration": "b", "dialogue": []},
            {
                "visual_mode": "talking_head",
                "talking_head_subject": "clark",
                "reference_character_id": "clark_ordway",
                "narration": "z",
                "dialogue": [
                    {"speaker_id": "clark", "text": "One."},
                    {"speaker_id": "clark", "text": "Two."},
                    {"speaker_id": "clark", "text": "Three."},
                    {"speaker_id": "clark", "text": "Four."},
                ],
            },
        ]
        allowed = frozenset({"clark", "ordway", "narrator"})
        rule = PairPortraitRule(
            composite_id="clark_ordway",
            member_ids=frozenset({"clark", "ordway"}),
            joint_shot_hints=(),
            implicit_when=None,
        )
        with (
            mock.patch(
                "pipeline.narration_characters.storage.character_portrait_asset_exists",
                return_value=True,
            ),
            mock.patch(
                "pipeline.narration_characters.storage.load_pair_portrait_rules",
                return_value=(rule,),
            ),
        ):
            with self.assertRaisesRegex(ValueError, r"pair composite"):
                validate_dialogue_visual_modes(segments, allowed_speakers=allowed)

    def test_validate_four_consecutive_talking_head_when_cap_long(self) -> None:
        """Long-conversation gate allows up to four consecutive TH (e.g. alternating two speakers)."""
        segments = [
            {"visual_mode": "b_roll", "narration": "a", "dialogue": []},
            {"visual_mode": "b_roll", "narration": "b", "dialogue": []},
            *(
                {
                    "visual_mode": "talking_head",
                    "talking_head_subject": "lewis",
                    "narration": "z",
                    "dialogue": [{"speaker_id": "lewis", "text": f"{k}."}],
                }
                for k in range(4)
            ),
        ]
        allowed = frozenset({"lewis", "narrator"})
        with mock.patch(
            "pipeline.narration_characters.storage.character_portrait_asset_exists",
            return_value=True,
        ):
            validate_dialogue_visual_modes(
                segments,
                allowed_speakers=allowed,
                max_consecutive_talking_head=MAX_CONSECUTIVE_TALKING_HEAD_LONG,
            )

    def test_validate_thirteenth_consecutive_talking_head_raises_with_long_cap(self) -> None:
        segments = [
            {"visual_mode": "b_roll", "narration": "a", "dialogue": []},
            {"visual_mode": "b_roll", "narration": "b", "dialogue": []},
            *(
                {
                    "visual_mode": "talking_head",
                    "talking_head_subject": "lewis",
                    "narration": "z",
                    "dialogue": [{"speaker_id": "lewis", "text": f"{k}."}],
                }
                for k in range(13)
            ),
        ]
        allowed = frozenset({"lewis", "narrator"})
        with mock.patch(
            "pipeline.narration_characters.storage.character_portrait_asset_exists",
            return_value=True,
        ):
            with self.assertRaisesRegex(ValueError, r"consecutive"):
                validate_dialogue_visual_modes(
                    segments,
                    allowed_speakers=allowed,
                    max_consecutive_talking_head=MAX_CONSECUTIVE_TALKING_HEAD_LONG,
                )

    def test_validate_talking_head_multi_speaker(self) -> None:
        segments = [
            {"visual_mode": "b_roll", "narration": "a", "dialogue": []},
            {"visual_mode": "b_roll", "narration": "b", "dialogue": []},
            {
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "narration": "z",
                "dialogue": [
                    {"speaker_id": "lewis", "text": "One."},
                    {"speaker_id": "clark", "text": "Two."},
                ],
            },
        ]
        allowed = frozenset({"lewis", "clark", "narrator"})
        with mock.patch(
            "pipeline.narration_characters.storage.character_portrait_asset_exists",
            return_value=True,
        ):
            with self.assertRaisesRegex(ValueError, "on-camera"):
                validate_dialogue_visual_modes(segments, allowed_speakers=allowed)

    def test_validate_requires_talking_head_when_flagged(self) -> None:
        segments = [
            {"visual_mode": "b_roll", "narration": "a", "dialogue": []},
            {"visual_mode": "b_roll", "narration": "b", "dialogue": []},
            {"visual_mode": "b_roll", "narration": "c", "dialogue": []},
        ]
        allowed = frozenset({"lewis", "clark", "narrator"})
        with self.assertRaises(ValueError):
            validate_dialogue_visual_modes(
                segments, allowed_speakers=allowed, require_talking_head=True
            )
        # Same all-b_roll episode is fine when the flag is off (default).
        validate_dialogue_visual_modes(segments, allowed_speakers=allowed)

    def test_validate_requires_talking_head_satisfied(self) -> None:
        segments = [
            {"visual_mode": "b_roll", "narration": "a", "dialogue": []},
            {"visual_mode": "b_roll", "narration": "b", "dialogue": []},
            {
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "narration": "z",
                "dialogue": [{"speaker_id": "lewis", "text": "I speak alone."}],
            },
        ]
        allowed = frozenset({"lewis", "clark", "narrator"})
        with mock.patch(
            "pipeline.narration_characters.storage.character_portrait_asset_exists",
            return_value=True,
        ):
            validate_dialogue_visual_modes(
                segments, allowed_speakers=allowed, require_talking_head=True
            )


if __name__ == "__main__":
    unittest.main()
