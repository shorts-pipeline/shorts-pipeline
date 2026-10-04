"""Tests for talking-head / dialogue TTS voice eligibility."""

from __future__ import annotations

from pipeline.narration_visual_mode import VISUAL_MODE_B_ROLL, VISUAL_MODE_TALKING_HEAD
from pipeline.tts_speaker_voice import (
    apply_talking_head_voice_downgrades_to_modes,
    resolve_openai_tts_voice_for_speaker,
    talking_head_subject_has_dedicated_voice,
)


def test_resolve_openai_falls_back_to_default() -> None:
    v = resolve_openai_tts_voice_for_speaker({}, "ordway", "onyx")
    assert v == "onyx"


def test_resolve_openai_uses_explicit() -> None:
    v = resolve_openai_tts_voice_for_speaker({"ordway": "echo"}, "ordway", "onyx")
    assert v == "echo"


def test_th_voice_fal_id_and_creds() -> None:
    assert talking_head_subject_has_dedicated_voice(
        "lewis",
        fal_voice_ids={"lewis": "ttv-x"},
        voice_by_speaker={},
        narrator_openai_voice="onyx",
        use_openai_tts=True,
        fal_credentials_ok=True,
    )


def test_th_voice_openai_distinct_from_narrator() -> None:
    assert talking_head_subject_has_dedicated_voice(
        "ordway",
        fal_voice_ids={},
        voice_by_speaker={"ordway": "echo"},
        narrator_openai_voice="onyx",
        use_openai_tts=True,
        fal_credentials_ok=False,
    )


def test_th_voice_missing_when_same_as_narrator() -> None:
    assert not talking_head_subject_has_dedicated_voice(
        "ordway",
        fal_voice_ids={},
        voice_by_speaker={},
        narrator_openai_voice="onyx",
        use_openai_tts=True,
        fal_credentials_ok=False,
    )


def test_th_voice_pyttsx3_false() -> None:
    assert not talking_head_subject_has_dedicated_voice(
        "lewis",
        fal_voice_ids={"lewis": "ttv-x"},
        voice_by_speaker={"lewis": "echo"},
        narrator_openai_voice="onyx",
        use_openai_tts=False,
        fal_credentials_ok=False,
    )


def test_apply_downgrades_ordway_without_voice() -> None:
    nar = {
        "narration_script": [
            {
                "visual_mode": "talking_head",
                "talking_head_subject": "ordway",
                "dialogue": [{"speaker_id": "ordway", "text": "Hi."}],
            },
        ]
    }
    modes = [VISUAL_MODE_TALKING_HEAD]
    out = apply_talking_head_voice_downgrades_to_modes(
        nar,
        modes,
        fal_voice_ids={},
        voice_by_speaker={},
        narrator_openai_voice="onyx",
        use_openai_tts=True,
        fal_credentials_ok=False,
    )
    assert out == [VISUAL_MODE_B_ROLL]


def test_apply_keeps_lewis_with_fal_voice() -> None:
    nar = {
        "narration_script": [
            {
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "dialogue": [{"speaker_id": "lewis", "text": "Hi."}],
            },
        ]
    }
    modes = [VISUAL_MODE_TALKING_HEAD]
    out = apply_talking_head_voice_downgrades_to_modes(
        nar,
        modes,
        fal_voice_ids={"lewis": "ttv-1"},
        voice_by_speaker={},
        narrator_openai_voice="onyx",
        use_openai_tts=True,
        fal_credentials_ok=True,
    )
    assert out == [VISUAL_MODE_TALKING_HEAD]
