"""Phase 1: spoken TTS content per segment must stay 18–50 words with length diversity."""

from __future__ import annotations

import pytest

from pipeline.narration_phase1 import _PACK_DIALOGUE_STANDARD, _validate_phase1
from pipeline.phase1_speaker_dialogue_rules import (
    AIM_SPOKEN_NARRATION_WORDS_LOW,
    LENGTH_DIVERSITY_MIN_FRACTION_LONG_CONVERSATION,
    MAX_SPOKEN_NARRATION_WORDS,
    MIN_SPOKEN_CAST_LONG_CONVERSATION,
    MIN_SPOKEN_NARRATION_WORDS,
    spoken_length_diversity_need,
    validate_spoken_narration_word_limits,
)


def _words(n: int) -> str:
    return " ".join(["word"] * n)


def _seg(*, narration: str, dialogue: list | None = None, visual_mode: str = "b_roll") -> dict:
    return {
        "segment_type": "observation",
        "visual_mode": visual_mode,
        "narration": narration,
        "dialogue": dialogue if dialogue is not None else [],
    }


def test_spoken_narration_at_max_ok() -> None:
    validate_spoken_narration_word_limits([_seg(narration=_words(MAX_SPOKEN_NARRATION_WORDS))])


def test_spoken_narration_at_min_ok() -> None:
    validate_spoken_narration_word_limits([_seg(narration=_words(MIN_SPOKEN_NARRATION_WORDS))])


def test_spoken_narration_over_limit_rejected() -> None:
    with pytest.raises(ValueError, match=r"≤50 words"):
        validate_spoken_narration_word_limits(
            [_seg(narration=_words(MAX_SPOKEN_NARRATION_WORDS + 1))]
        )


def test_spoken_narration_under_min_rejected() -> None:
    with pytest.raises(ValueError, match=rf"≥{MIN_SPOKEN_NARRATION_WORDS} words"):
        validate_spoken_narration_word_limits(
            [_seg(narration=_words(MIN_SPOKEN_NARRATION_WORDS - 1))]
        )


def test_cast_segment_long_director_narration_ok() -> None:
    validate_spoken_narration_word_limits(
        [
            _seg(
                narration=_words(80),
                dialogue=[{"speaker_id": "lewis", "text": _words(MIN_SPOKEN_NARRATION_WORDS)}],
                visual_mode="talking_head",
            )
        ]
    )


def test_cast_segment_short_dialogue_rejected() -> None:
    with pytest.raises(ValueError, match=rf"≥{MIN_SPOKEN_NARRATION_WORDS} words"):
        validate_spoken_narration_word_limits(
            [
                _seg(
                    narration="Director beat for Phase 2 only.",
                    dialogue=[{"speaker_id": "lewis", "text": "We move at first light."}],
                    visual_mode="talking_head",
                )
            ]
        )


def test_uniform_minimum_length_rejected_for_diversity() -> None:
    """Four+ segments all parked at the floor fail the mid-band diversity rule."""
    segs = [_seg(narration=_words(MIN_SPOKEN_NARRATION_WORDS)) for _ in range(4)]
    with pytest.raises(ValueError, match=r"spoken length diversity"):
        validate_spoken_narration_word_limits(segs)


def test_mixed_lengths_pass_diversity() -> None:
    segs = [
        _seg(narration=_words(MIN_SPOKEN_NARRATION_WORDS)),
        _seg(narration=_words(AIM_SPOKEN_NARRATION_WORDS_LOW)),
        _seg(narration=_words(MIN_SPOKEN_NARRATION_WORDS + 2)),
        _seg(narration=_words(AIM_SPOKEN_NARRATION_WORDS_LOW + 5)),
    ]
    validate_spoken_narration_word_limits(segs)


def test_diversity_can_be_disabled() -> None:
    segs = [_seg(narration=_words(MIN_SPOKEN_NARRATION_WORDS)) for _ in range(4)]
    validate_spoken_narration_word_limits(segs, require_length_diversity=False)


def test_long_conversation_diversity_allows_fewer_mid_band() -> None:
    """10 spoken beats: default needs 5 mid-band; long-conversation (~⅓) needs 4."""
    assert spoken_length_diversity_need(10) == 5
    assert (
        spoken_length_diversity_need(
            10, min_fraction=LENGTH_DIVERSITY_MIN_FRACTION_LONG_CONVERSATION
        )
        == 4
    )
    # 4 at aim + 6 at floor: fails default half, passes long-conversation third
    segs = [_seg(narration=_words(AIM_SPOKEN_NARRATION_WORDS_LOW)) for _ in range(4)] + [
        _seg(narration=_words(MIN_SPOKEN_NARRATION_WORDS)) for _ in range(6)
    ]
    with pytest.raises(ValueError, match=r"spoken length diversity"):
        validate_spoken_narration_word_limits(segs)
    validate_spoken_narration_word_limits(
        segs,
        length_diversity_min_fraction=LENGTH_DIVERSITY_MIN_FRACTION_LONG_CONVERSATION,
    )


def test_long_conversation_cast_floor_allows_punchy_turns() -> None:
    """Cast dialogue may sit at 12 words in long-conversation; narrator VO still needs 18."""
    punchy = _seg(
        narration="Director beat only.",
        dialogue=[{"speaker_id": "clark", "text": _words(MIN_SPOKEN_CAST_LONG_CONVERSATION)}],
        visual_mode="talking_head",
    )
    with pytest.raises(ValueError, match=rf"≥{MIN_SPOKEN_NARRATION_WORDS} words"):
        validate_spoken_narration_word_limits([punchy], require_length_diversity=False)
    validate_spoken_narration_word_limits(
        [punchy],
        require_length_diversity=False,
        min_spoken_cast_words=MIN_SPOKEN_CAST_LONG_CONVERSATION,
    )
    too_short = _seg(
        narration="Director beat only.",
        dialogue=[{"speaker_id": "clark", "text": _words(MIN_SPOKEN_CAST_LONG_CONVERSATION - 1)}],
        visual_mode="talking_head",
    )
    with pytest.raises(ValueError, match=rf"≥{MIN_SPOKEN_CAST_LONG_CONVERSATION} words"):
        validate_spoken_narration_word_limits(
            [too_short],
            require_length_diversity=False,
            min_spoken_cast_words=MIN_SPOKEN_CAST_LONG_CONVERSATION,
        )


def test_validate_phase1_long_conversation_accepts_one_third_mid_band() -> None:
    from pipeline.narration_phase1 import _PACK_DIALOGUE_LONG

    mid = _words(AIM_SPOKEN_NARRATION_WORDS_LOW)
    short = _words(MIN_SPOKEN_NARRATION_WORDS)
    segs = []
    for i in range(10):
        narration = mid if i < 4 else short
        segs.append(
            {
                "segment_type": "observation",
                "visual_mode": "b_roll",
                "narration": narration,
                "dialogue": [],
            }
        )
    # Force closing segment type for tone-only path: _validate_phase1 also needs tone_register
    segs[-1]["segment_type"] = "reflection"
    parsed = {
        "title": "t",
        "tone_register": "light",
        "segments": segs,
    }
    # Long pack without conversation_mode.enabled sustained-exchange would fail other checks
    # when dialogue_mode True; here dialogue_mode False isn't valid with long pack path for
    # diversity alone — call validator through dialogue False with explicit fraction via helper:
    # Exercise _validate_phase1 long pack with dialogue_mode False still uses default fraction.
    # Use dialogue_mode + long pack with conversation disabled to skip sustained-exchange.
    _validate_phase1(
        parsed,
        dialogue_mode=True,
        prompt_pack=_PACK_DIALOGUE_LONG,
        conversation_mode={"enabled": False},
    )


def test_validate_phase1_dialogue_rejects_long_narrator_only_narration() -> None:
    long = _words(MAX_SPOKEN_NARRATION_WORDS + 1)
    close = _words(MIN_SPOKEN_NARRATION_WORDS)
    parsed = {
        "title": "t",
        "tone_register": "light",
        "segments": [
            {"segment_type": "hook", "visual_mode": "b_roll", "narration": long, "dialogue": []},
            {
                "segment_type": "reflection",
                "visual_mode": "b_roll",
                "narration": close,
                "dialogue": [],
            },
        ],
    }
    with pytest.raises(ValueError, match=r"≤50 words"):
        _validate_phase1(parsed, dialogue_mode=True, prompt_pack=_PACK_DIALOGUE_STANDARD)


def test_validate_phase1_narrator_rejects_short_narration() -> None:
    short = _words(MIN_SPOKEN_NARRATION_WORDS - 1)
    close = _words(MIN_SPOKEN_NARRATION_WORDS)
    parsed = {
        "title": "t",
        "tone_register": "light",
        "segments": [
            {"segment_type": "hook", "visual_mode": "b_roll", "narration": short},
            {"segment_type": "reflection", "visual_mode": "b_roll", "narration": close},
        ],
    }
    with pytest.raises(ValueError, match=rf"≥{MIN_SPOKEN_NARRATION_WORDS} words"):
        _validate_phase1(parsed, dialogue_mode=False, prompt_pack="lewis_clark")
