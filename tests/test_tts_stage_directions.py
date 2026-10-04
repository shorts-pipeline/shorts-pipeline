"""Stage-direction stripping and pause mapping for dialogue TTS."""

from __future__ import annotations

from pipeline.tts_stage_directions import (
    DEFAULT_STAGE_PAUSE_SEC,
    split_dialogue_for_tts,
    spoken_text_only_for_tts,
)


def test_strip_pause_sentence() -> None:
    chunks = split_dialogue_for_tts("Lewis pauses for a moment. The river is rising.")
    assert chunks == [("The river is rising.", DEFAULT_STAGE_PAUSE_SEC)]


def test_stands_quietly_only() -> None:
    chunks = split_dialogue_for_tts("He stands quietly.")
    assert chunks == [("", DEFAULT_STAGE_PAUSE_SEC)]


def test_parenthetical_pause() -> None:
    chunks = split_dialogue_for_tts("We should move now (brief pause) before dark.")
    spoken = [t for t, _ in chunks if t.strip()]
    assert spoken == ["We should move now before dark."]
    assert any(p > 0 for _, p in chunks)


def test_spoken_text_only_joins() -> None:
    assert spoken_text_only_for_tts("Clark waits a moment. Forward, men.") == "Forward, men."


def test_plain_dialogue_unchanged() -> None:
    line = "The river is up two feet since yesterday."
    assert split_dialogue_for_tts(line) == [(line, 0.0)]
