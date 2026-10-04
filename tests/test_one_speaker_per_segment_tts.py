"""One speaker per segment: TTS-time validation and voice routing policy."""

from __future__ import annotations

import pytest

from pipeline.phase1_speaker_dialogue_rules import (
    segment_expects_one_cast_speaker_per_segment,
    validate_one_speaker_per_segment_tts,
    validate_speaker_dialogue_rules,
)
from pipeline.tts_speaker_voice import segment_audio_is_cast_dialogue_only


def test_validate_rejects_narrator_and_cast_dialogue_same_segment() -> None:
    segs = [
        {
            "segment_index": 5,
            "dialogue": [
                {"speaker_id": "narrator", "text": "Bridge line."},
                {"speaker_id": "drouillard", "text": "Scout report."},
            ],
        }
    ]
    with pytest.raises(ValueError, match=r"One-speaker-per-segment TTS policy"):
        validate_one_speaker_per_segment_tts(segs)


def test_validate_rejects_two_cast_speakers_same_segment() -> None:
    segs = [
        {
            "dialogue": [
                {"speaker_id": "clark", "text": "Line one."},
                {"speaker_id": "lewis", "text": "Line two."},
            ],
        }
    ]
    with pytest.raises(ValueError, match=r"at most one cast speaker"):
        validate_one_speaker_per_segment_tts(segs)


def test_validate_single_cast_speaker_ok() -> None:
    segs = [
        {
            "dialogue": [{"speaker_id": "drouillard", "text": "Seven days out."}],
        }
    ]
    validate_speaker_dialogue_rules(segs)


def test_long_conversation_b_roll_allows_two_cast_speakers() -> None:
    seg = {
        "visual_mode": "b_roll",
        "dialogue": [
            {"speaker_id": "lewis", "text": "Line one."},
            {"speaker_id": "clark", "text": "Line two."},
        ],
    }
    assert not segment_expects_one_cast_speaker_per_segment(seg, long_conversation_mode=True)
    validate_one_speaker_per_segment_tts([seg], long_conversation_mode=True)


def test_long_conversation_talking_head_still_one_cast_speaker() -> None:
    seg = {
        "visual_mode": "talking_head",
        "talking_head_subject": "lewis",
        "dialogue": [
            {"speaker_id": "lewis", "text": "One."},
            {"speaker_id": "clark", "text": "Two."},
        ],
    }
    assert segment_expects_one_cast_speaker_per_segment(seg, long_conversation_mode=True)
    with pytest.raises(ValueError, match=r"at most one cast speaker"):
        validate_one_speaker_per_segment_tts([seg], long_conversation_mode=True)


def test_validate_only_requested_segment_indices() -> None:
    segs = [
        {"dialogue": [{"speaker_id": "lewis", "text": "ok"}]},
        {
            "dialogue": [
                {"speaker_id": "clark", "text": "a"},
                {"speaker_id": "lewis", "text": "b"},
            ]
        },
    ]
    validate_one_speaker_per_segment_tts(segs, segment_indices={1})
    with pytest.raises(ValueError, match=r"segment 2"):
        validate_one_speaker_per_segment_tts(segs, segment_indices={2})


def test_segment_audio_is_cast_dialogue_only_when_fal_voice() -> None:
    seg = {
        "dialogue": [{"speaker_id": "drouillard", "text": "Trail held."}],
    }
    assert segment_audio_is_cast_dialogue_only(
        seg,
        fal_voice_ids={"drouillard": "ttv-x"},
        voice_by_speaker={},
        narrator_openai_voice="onyx",
        use_openai_tts=True,
        fal_credentials_ok=True,
    )


def test_segment_audio_is_not_cast_only_without_voice() -> None:
    seg = {
        "dialogue": [{"speaker_id": "ordway", "text": "Rouse the guard."}],
    }
    assert not segment_audio_is_cast_dialogue_only(
        seg,
        fal_voice_ids={},
        voice_by_speaker={},
        narrator_openai_voice="onyx",
        use_openai_tts=True,
        fal_credentials_ok=False,
    )
