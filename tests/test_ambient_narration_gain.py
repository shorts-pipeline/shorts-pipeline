"""Tests for narration-driven ambient bed gain tweaks."""

from pipeline.ambient_audio import ambient_bed_gain_adjustment_db


def test_ambient_gain_tone_register() -> None:
    assert ambient_bed_gain_adjustment_db({"tone_register": "tense"}) == -1.5


def test_ambient_gain_music_intensity_curve() -> None:
    assert (
        ambient_bed_gain_adjustment_db({"audio_design": {"music_intensity_curve": "minimal"}})
        == -1.0
    )


def test_ambient_gain_combined() -> None:
    assert (
        ambient_bed_gain_adjustment_db(
            {
                "tone_register": "somber",
                "audio_design": {"music_intensity_curve": "gradual_build"},
            }
        )
        == -1.5
    )
