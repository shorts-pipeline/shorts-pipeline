from video_vendors.fal_scene_anchor_i2i import build_scene_anchor_i2i_prompt


def test_i2i_prompt_uses_two_person_primary_for_composite_anchor():
    prompt = build_scene_anchor_i2i_prompt(
        prompt_without_markers_sanitized="Lewis and Clark at table.",
        character_id="lewis_clark",
        segment_index=1,
        opening_frames=["Lewis and Clark sit next to each other at a table."],
        world_prefix_for_i2i="Photorealistic period scene set in early 1800's.",
        aggressive=False,
        sanitize=lambda text, aggressive=False: text,
        dual_reference_speakers=("lewis", "clark"),
    )
    assert "FIRST reference portrait is lewis" in prompt
    assert "PRIMARY — single figure" not in prompt
    assert "Lewis and Clark sit next to each other at a table." in prompt


def test_i2i_prompt_dual_reference_fallback_without_opening_frame():
    prompt = build_scene_anchor_i2i_prompt(
        prompt_without_markers_sanitized=(
            "A medium shot captures Lewis and Clark overseeing the journey."
        ),
        character_id="lewis_clark",
        segment_index=10,
        opening_frames=[None] * 10,
        world_prefix_for_i2i="Photorealistic period scene set in early 1800's.",
        aggressive=False,
        sanitize=lambda text, aggressive=False: text,
        dual_reference_speakers=("lewis", "clark"),
    )
    assert "FIRST reference portrait is lewis" in prompt
    assert "overseeing the journey" in prompt
    assert "Create a single still opening frame" not in prompt


def test_i2i_prompt_preserves_likeness_not_reference_pose_for_single_anchor():
    opening = "Gass sits with hands clasped, firelight on his face."
    prompt = build_scene_anchor_i2i_prompt(
        prompt_without_markers_sanitized="Campfire scene.",
        character_id="gass",
        segment_index=7,
        opening_frames=[None] * 6 + [opening],
        world_prefix_for_i2i="Photorealistic period scene set in early 1800's.",
        aggressive=False,
        sanitize=lambda text, aggressive=False: text,
    )
    assert "PRIMARY — single figure" in prompt
    assert "garment identity (coat color, facings, insignia, hat) only" in prompt
    assert (
        "do not copy standing pose, gaze/eyeline, head angle, or held props from the reference"
        in prompt
    )
    assert opening in prompt
    assert "preserve the reference portrait face, hair, and coat" not in prompt
    assert "do not copy tools or objects from the reference portrait" in prompt


def test_fal_scene_anchor_i2i_module_imports_sys():
    """Quality-check retry logs to stderr; module must import sys (regression)."""
    import video_vendors.fal_scene_anchor_i2i as mod

    assert mod.sys is not None
