"""lewis_clark_dialogue Phase 1: character dialogue in at most three segments."""

from __future__ import annotations

import pytest

from pipeline.narration_phase1 import _PACK_DIALOGUE_STANDARD, _validate_phase1
from pipeline.phase1_speaker_dialogue_rules import AIM_SPOKEN_NARRATION_WORDS_LOW

# Mid-band so multi-segment fixtures satisfy length diversity.
_NARRATOR_VO = " ".join(["word"] * AIM_SPOKEN_NARRATION_WORDS_LOW)
_CAST_LINE = " ".join(["word"] * AIM_SPOKEN_NARRATION_WORDS_LOW)


def _minimal_phase1_with_rows(rows_by_segment: list[list[dict]]) -> dict:
    segs = []
    for i, rows in enumerate(rows_by_segment, start=1):
        st = "hook" if i == 1 else ("reflection" if i == len(rows_by_segment) else "observation")
        segs.append(
            {
                "segment_type": st,
                "visual_mode": "b_roll",
                "narration": _NARRATOR_VO
                if not rows
                else "Director beat for Phase 2 context only on cast segments.",
                "dialogue": rows,
            }
        )
    return {"title": "t", "tone_register": "light", "segments": segs}


def _char_line(speaker_id: str = "lewis") -> dict:
    return {"speaker_id": speaker_id, "text": _CAST_LINE}


def _narr_line() -> dict:
    return {"speaker_id": "narrator", "text": _CAST_LINE}


def test_validate_phase1_dialogue_three_character_dialogue_segments_ok() -> None:
    parsed = _minimal_phase1_with_rows(
        [
            [],
            [_char_line("lewis")],
            [_narr_line()],
            [_char_line("clark")],
            [_char_line("ordway")],
            [],
        ]
    )
    _validate_phase1(parsed, dialogue_mode=True, prompt_pack=_PACK_DIALOGUE_STANDARD)


def test_validate_phase1_dialogue_four_character_dialogue_segments_rejected() -> None:
    parsed = _minimal_phase1_with_rows(
        [
            [_char_line("lewis")],
            [_char_line("clark")],
            [_char_line("ordway")],
            [_char_line("gass")],
            [],
        ]
    )
    with pytest.raises(ValueError, match=r"at most 3 segments"):
        _validate_phase1(parsed, dialogue_mode=True, prompt_pack=_PACK_DIALOGUE_STANDARD)


def test_validate_phase1_dialogue_narrator_only_rows_do_not_count() -> None:
    parsed = _minimal_phase1_with_rows(
        [
            [_narr_line()],
            [_narr_line()],
            [_narr_line()],
            [_narr_line()],
            [_narr_line()],
        ]
    )
    _validate_phase1(parsed, dialogue_mode=True, prompt_pack=_PACK_DIALOGUE_STANDARD)
