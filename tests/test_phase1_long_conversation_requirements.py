"""Validation guards for lewis_clark_long_conversation Phase 1 outputs."""

from __future__ import annotations

import unittest.mock as mock

import pytest

from pipeline.narration_phase1 import _PACK_DIALOGUE_LONG, _validate_phase1
from pipeline.phase1_speaker_dialogue_rules import (
    AIM_SPOKEN_NARRATION_WORDS_LOW,
    MIN_SPOKEN_NARRATION_WORDS,
)

# Mid-band spoken length so fixtures clear floor + diversity (≥ half at aim low).
_NARRATOR_VO = " ".join(["word"] * AIM_SPOKEN_NARRATION_WORDS_LOW)
# Four alternating cast lines → 28 spoken words (aim low).
_DIALOGUE_LINE = " ".join(["word"] * 7)
_DIALOGUE_LINE_12 = " ".join(["word"] * 12)
_TH_TURN = " ".join(["word"] * AIM_SPOKEN_NARRATION_WORDS_LOW)


def _line(speaker_id: str, text: str = _DIALOGUE_LINE) -> dict:
    return {"speaker_id": speaker_id, "text": text}


def _segment(rows: list[dict], *, mode: str = "b_roll", segment_type: str = "observation") -> dict:
    return {
        "segment_type": segment_type,
        "visual_mode": mode,
        "narration": _NARRATOR_VO
        if not rows
        else "Director beat for Phase 2 context only on cast segments.",
        "dialogue": rows,
    }


def _talking_head_segment(speaker_id: str, texts: tuple[str, ...]) -> dict:
    dialogue = [{"speaker_id": speaker_id, "text": t} for t in texts]
    if len(texts) == 1 and len(texts[0].split()) < MIN_SPOKEN_NARRATION_WORDS:
        dialogue[0]["text"] = _TH_TURN
    return {
        "segment_type": "action",
        "visual_mode": "talking_head",
        "talking_head_subject": speaker_id,
        "talking_head_prompt": "Neutral pre-hold; clear delivery; soft settle after lines.",
        "narration": "Director beat for Phase 2 context only on cast segments.",
        "dialogue": dialogue,
    }


def _phase1(segments: list[dict]) -> dict:
    return {
        "title": "t",
        "tone_register": "warm",
        "segments": segments,
    }


def _long_convo_cfg(**overrides: int) -> dict:
    base = {
        "enabled": True,
        "min_segments": 6,
        "min_lines_per_segment": 1,
        "min_lines_per_talking_head_segment": 1,
        "max_lines_per_talking_head_segment": 1,
        "min_lines_per_b_roll_segment": 4,
    }
    base.update(overrides)
    return base


def test_long_conversation_requires_sustained_two_speaker_exchange() -> None:
    parsed = _phase1(
        [
            _segment([], segment_type="hook"),
            _segment([_line("narrator"), _line("narrator"), _line("narrator"), _line("narrator")]),
            _segment([_line("lewis"), _line("lewis"), _line("lewis"), _line("lewis")]),
            _segment(
                [_line("clark"), _line("clark"), _line("clark"), _line("clark")],
                segment_type="reflection",
            ),
        ]
    )
    with pytest.raises(ValueError, match=r"sustained two-speaker exchange"):
        _validate_phase1(
            parsed,
            dialogue_mode=True,
            prompt_pack=_PACK_DIALOGUE_LONG,
            conversation_mode=_long_convo_cfg(),
        )


def test_long_conversation_accepts_consecutive_b_roll_alternation() -> None:
    parsed = _phase1(
        [
            _segment([], segment_type="hook"),
            _segment([_line("lewis"), _line("clark"), _line("lewis"), _line("clark")]),
            _segment([_line("clark"), _line("lewis"), _line("clark"), _line("lewis")]),
            _segment([_line("lewis"), _line("clark"), _line("lewis"), _line("clark")]),
            _segment([_line("clark"), _line("lewis"), _line("clark"), _line("lewis")]),
            _segment([_line("lewis"), _line("clark"), _line("lewis"), _line("clark")]),
            _segment([_line("clark"), _line("lewis"), _line("clark"), _line("lewis")]),
            _segment(
                [_line("narrator"), _line("narrator"), _line("narrator"), _line("narrator")],
                segment_type="reflection",
            ),
        ]
    )
    _validate_phase1(
        parsed,
        dialogue_mode=True,
        prompt_pack=_PACK_DIALOGUE_LONG,
        conversation_mode=_long_convo_cfg(min_segments=6),
    )


def test_long_conversation_rejects_b_roll_stacked_speakers() -> None:
    """Same two speakers but L,L,C,C blocks do not count as a conversation."""
    parsed = _phase1(
        [
            _segment([], segment_type="hook"),
            _segment([_line("lewis"), _line("lewis"), _line("clark"), _line("clark")]),
            _segment([_line("lewis"), _line("clark"), _line("lewis"), _line("clark")]),
            _segment(
                [_line("narrator"), _line("narrator"), _line("narrator"), _line("narrator")],
                segment_type="reflection",
            ),
        ]
    )
    with pytest.raises(ValueError, match=r"sustained two-speaker exchange"):
        _validate_phase1(
            parsed,
            dialogue_mode=True,
            prompt_pack=_PACK_DIALOGUE_LONG,
            conversation_mode=_long_convo_cfg(),
        )


def test_long_conversation_rejects_consecutive_talking_head_same_subject() -> None:
    parsed = _phase1(
        [
            _segment([], segment_type="hook"),
            _segment([]),
            _talking_head_segment("lewis", ("one",)),
            _talking_head_segment("lewis", ("two",)),
            _segment(
                [_line("narrator"), _line("narrator"), _line("narrator"), _line("narrator")],
                segment_type="reflection",
            ),
        ]
    )
    with mock.patch(
        "pipeline.narration_characters.storage.character_portrait_asset_exists",
        return_value=True,
    ):
        with pytest.raises(ValueError, match=r"sustained two-speaker exchange"):
            _validate_phase1(
                parsed,
                dialogue_mode=True,
                prompt_pack=_PACK_DIALOGUE_LONG,
                conversation_mode=_long_convo_cfg(),
            )


def test_long_conversation_rejects_two_dialogue_rows_in_talking_head() -> None:
    parsed = _phase1(
        [
            _segment([], segment_type="hook"),
            _segment([]),
            _talking_head_segment("lewis", (_DIALOGUE_LINE_12, _DIALOGUE_LINE_12)),
            _segment(
                [_line("narrator"), _line("narrator"), _line("narrator"), _line("narrator")],
                segment_type="reflection",
            ),
        ]
    )
    with mock.patch(
        "pipeline.narration_characters.storage.character_portrait_asset_exists",
        return_value=True,
    ):
        with pytest.raises(ValueError, match=r"exactly 1 non-narrator"):
            _validate_phase1(
                parsed,
                dialogue_mode=True,
                prompt_pack=_PACK_DIALOGUE_LONG,
                conversation_mode=_long_convo_cfg(),
            )


def test_long_conversation_rejects_four_line_talking_head_monologue() -> None:
    parsed = _phase1(
        [
            _segment([], segment_type="hook"),
            _segment([]),
            _talking_head_segment(
                "lewis", (_DIALOGUE_LINE, _DIALOGUE_LINE, _DIALOGUE_LINE, _DIALOGUE_LINE)
            ),
            _talking_head_segment(
                "clark", (_DIALOGUE_LINE, _DIALOGUE_LINE, _DIALOGUE_LINE, _DIALOGUE_LINE)
            ),
            _segment(
                [_line("narrator"), _line("narrator"), _line("narrator"), _line("narrator")],
                segment_type="reflection",
            ),
        ]
    )
    with mock.patch(
        "pipeline.narration_characters.storage.character_portrait_asset_exists",
        return_value=True,
    ):
        with pytest.raises(ValueError, match=r"exactly 1 non-narrator"):
            _validate_phase1(
                parsed,
                dialogue_mode=True,
                prompt_pack=_PACK_DIALOGUE_LONG,
                conversation_mode=_long_convo_cfg(),
            )


def test_long_conversation_accepts_ping_pong_talking_head_segments() -> None:
    exchange = []
    for i in range(6):
        sid = "lewis" if i % 2 == 0 else "clark"
        exchange.append(_talking_head_segment(sid, (f"turn {i}.",)))
    parsed = _phase1(
        [
            _segment([], segment_type="hook"),
            _segment([]),
            *exchange,
            _segment(
                [_line("narrator"), _line("narrator"), _line("narrator"), _line("narrator")],
                segment_type="reflection",
            ),
        ]
    )
    with mock.patch(
        "pipeline.narration_characters.storage.character_portrait_asset_exists",
        return_value=True,
    ):
        _validate_phase1(
            parsed,
            dialogue_mode=True,
            prompt_pack=_PACK_DIALOGUE_LONG,
            conversation_mode=_long_convo_cfg(),
        )


def test_long_conversation_require_talking_head_rejects_b_roll_exchange_plus_unrelated_clip() -> (
    None
):
    """A week-arc-required talking head must be INSIDE the sustained exchange, not just anywhere.

    Regression for a validation gap: an all-`b_roll` exchange plus an isolated,
    unrelated `talking_head` clip elsewhere in the episode used to satisfy both checks
    independently even though the actual conversation never showed a face.
    """
    # Talking-head text supplied at exactly the 18-word floor (rather than letting
    # _talking_head_segment pad a short line up to the 22-word _TH_TURN) and the reflection
    # segment trimmed to 3 narrator lines (still clears the 18-word floor): both keep this
    # fixture's total spoken words under MAX_TOTAL_SPOKEN_WORDS (275) so the episode-level
    # word-cap check doesn't fire before the exchange-placement check this test is about.
    _unrelated_aside = " ".join(["word"] * MIN_SPOKEN_NARRATION_WORDS)
    parsed = _phase1(
        [
            _segment([], segment_type="hook"),
            _segment([]),
            _talking_head_segment("lewis", (_unrelated_aside,)),
            _segment([]),
            _segment([_line("lewis"), _line("clark"), _line("lewis"), _line("clark")]),
            _segment([_line("clark"), _line("lewis"), _line("clark"), _line("lewis")]),
            _segment([_line("lewis"), _line("clark"), _line("lewis"), _line("clark")]),
            _segment([_line("clark"), _line("lewis"), _line("clark"), _line("lewis")]),
            _segment([_line("lewis"), _line("clark"), _line("lewis"), _line("clark")]),
            _segment([_line("clark"), _line("lewis"), _line("clark"), _line("lewis")]),
            _segment(
                [_line("narrator"), _line("narrator"), _line("narrator")],
                segment_type="reflection",
            ),
        ]
    )
    with mock.patch(
        "pipeline.narration_characters.storage.character_portrait_asset_exists",
        return_value=True,
    ):
        with pytest.raises(ValueError, match=r"inside.*the sustained exchange"):
            _validate_phase1(
                parsed,
                dialogue_mode=True,
                prompt_pack=_PACK_DIALOGUE_LONG,
                conversation_mode=_long_convo_cfg(),
                require_talking_head=True,
            )


def test_long_conversation_require_talking_head_accepts_mixed_window() -> None:
    """A talking_head clip inside the sustained exchange itself satisfies the requirement."""
    parsed = _phase1(
        [
            _segment([], segment_type="hook"),
            _segment([]),
            _segment([_line("lewis"), _line("clark"), _line("lewis"), _line("clark")]),
            _segment([_line("clark"), _line("lewis"), _line("clark"), _line("lewis")]),
            _talking_head_segment("lewis", ("Turn inside the live exchange.",)),
            _segment([_line("clark"), _line("lewis"), _line("clark"), _line("lewis")]),
            _segment([_line("lewis"), _line("clark"), _line("lewis"), _line("clark")]),
            _segment([_line("clark"), _line("lewis"), _line("clark"), _line("lewis")]),
            _segment(
                [_line("narrator"), _line("narrator"), _line("narrator"), _line("narrator")],
                segment_type="reflection",
            ),
        ]
    )
    with mock.patch(
        "pipeline.narration_characters.storage.character_portrait_asset_exists",
        return_value=True,
    ):
        _validate_phase1(
            parsed,
            dialogue_mode=True,
            prompt_pack=_PACK_DIALOGUE_LONG,
            conversation_mode=_long_convo_cfg(),
            require_talking_head=True,
        )


def test_long_conversation_accepts_narrator_interleaved_b_roll_alternation() -> None:
    parsed = _phase1(
        [
            _segment([], segment_type="hook"),
            _segment(
                [
                    _line("lewis"),
                    _line("narrator"),
                    _line("clark"),
                    _line("narrator"),
                    _line("lewis"),
                    _line("narrator"),
                    _line("clark"),
                ]
            ),
            _segment([_line("clark"), _line("lewis"), _line("clark"), _line("lewis")]),
            _segment([_line("lewis"), _line("clark"), _line("lewis"), _line("clark")]),
            _segment([_line("clark"), _line("lewis"), _line("clark"), _line("lewis")]),
            _segment([_line("lewis"), _line("clark"), _line("lewis"), _line("clark")]),
            _segment([_line("clark"), _line("lewis"), _line("clark"), _line("lewis")]),
            _segment(
                [_line("narrator"), _line("narrator"), _line("narrator"), _line("narrator")],
                segment_type="reflection",
            ),
        ]
    )
    _validate_phase1(
        parsed,
        dialogue_mode=True,
        prompt_pack=_PACK_DIALOGUE_LONG,
        conversation_mode=_long_convo_cfg(min_segments=6),
    )
