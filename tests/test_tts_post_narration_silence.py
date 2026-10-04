"""Tests for per-segment post_narration_silence_seconds parsing and review warnings."""

from pipeline.tts_post_narration_silence import (
    MAX_POST_NARRATION_SILENCE_SECONDS,
    cap_post_narration_silence_in_script,
    parse_post_narration_silence_seconds,
    post_narration_silence_review_warnings,
    segment_post_narration_silence_seconds,
)


def test_parse_clamps_and_invalid() -> None:
    assert parse_post_narration_silence_seconds(None) == 0.0
    assert parse_post_narration_silence_seconds("bad") == 0.0
    assert parse_post_narration_silence_seconds(-1) == 0.0
    assert parse_post_narration_silence_seconds(3.5) == 3.5
    assert parse_post_narration_silence_seconds(99) == MAX_POST_NARRATION_SILENCE_SECONDS


def test_segment_helper() -> None:
    assert segment_post_narration_silence_seconds({"post_narration_silence_seconds": 2}) == 2.0
    assert segment_post_narration_silence_seconds({}) == 0.0


def test_review_warnings_overuse() -> None:
    nar = {
        "narration_script": [
            {"post_narration_silence_seconds": 4},
            {"post_narration_silence_seconds": 5},
            {"post_narration_silence_seconds": 6},
        ]
    }
    msgs = post_narration_silence_review_warnings(nar)
    assert any("at most 2" in m for m in msgs)
    assert any("12" in m for m in msgs)


def test_review_warnings_ok() -> None:
    nar = {"narration_script": [{"post_narration_silence_seconds": 3}]}
    assert post_narration_silence_review_warnings(nar) == []


def test_cap_keeps_two_longest() -> None:
    script = [
        {"post_narration_silence_seconds": 1},
        {"post_narration_silence_seconds": 5},
        {"post_narration_silence_seconds": 3},
        {"post_narration_silence_seconds": 2},
    ]
    removed = cap_post_narration_silence_in_script(script)
    assert removed == 2
    assert segment_post_narration_silence_seconds(script[0]) == 0.0
    assert segment_post_narration_silence_seconds(script[1]) == 5.0
    assert segment_post_narration_silence_seconds(script[2]) == 3.0
    assert segment_post_narration_silence_seconds(script[3]) == 0.0
    assert post_narration_silence_review_warnings({"narration_script": script}) == []


def test_cap_scales_total_budget() -> None:
    script = [
        {"post_narration_silence_seconds": 8},
        {"post_narration_silence_seconds": 8},
    ]
    cap_post_narration_silence_in_script(script)
    total = sum(segment_post_narration_silence_seconds(r) for r in script)
    assert total <= 12.0 + 0.01
