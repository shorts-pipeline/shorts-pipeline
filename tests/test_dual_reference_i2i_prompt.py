"""Tests for shared dual-reference Wan i2i prompt builder."""

from __future__ import annotations

from pipeline.dual_reference_i2i_prompt import build_dual_reference_i2i_prompt


def test_dual_reference_prompt_binds_portraits_and_scene() -> None:
    prompt = build_dual_reference_i2i_prompt(
        left_speaker_id="lewis",
        right_speaker_id="clark",
        scene_text="Missouri River afternoon, men working boats.",
        world_prefix="Photorealistic period scene set in early 1800's, reflective mood",
    )
    assert len(prompt) < 1200
    assert "FIRST reference portrait is lewis" in prompt
    assert "SECOND is clark" in prompt
    assert "Missouri River afternoon" in prompt
    assert "Physique:" in prompt
    assert "lean build" in prompt.lower()


def test_segment10_style_prompt_uses_video_prompt_when_no_opening_frame() -> None:
    scene = (
        "A medium shot captures Lewis and Clark overseeing the journey, their expressions "
        "a mix of caution and optimism."
    )
    prompt = build_dual_reference_i2i_prompt(
        left_speaker_id="lewis",
        right_speaker_id="clark",
        scene_text=scene,
        world_prefix="Photorealistic period scene set in early 1800's, Missouri River, midday passage",
    )
    assert "overseeing the journey" in prompt
    assert "FIRST reference portrait" in prompt
