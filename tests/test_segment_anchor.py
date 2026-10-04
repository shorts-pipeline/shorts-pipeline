"""Segment anchor path resolution and t2i prompt building."""

from video_vendors.fal import FalVendor
from video_vendors.fal_scene_anchor_i2i import (
    build_scene_anchor_t2i_prompt,
    run_segment_anchor_to_disk,
)


def test_existing_segment_anchor_path_any_stem(tmp_path):
    anchors = tmp_path / "anchors"
    anchors.mkdir()
    still = anchors / "03_scene.png"
    still.write_bytes(b"png")
    assert FalVendor._existing_segment_anchor_path(tmp_path, 3) == still
    assert FalVendor._existing_segment_anchor_path(tmp_path, 4) is None


def test_build_scene_anchor_t2i_prompt_uses_opening_frame():
    prompt = build_scene_anchor_t2i_prompt(
        prompt_without_markers_sanitized="Wide river at dawn.",
        segment_index=2,
        opening_frames=[None, "Mist on the Missouri at first light."],
        world_prefix_for_i2i="Photorealistic period scene set in early 1800's.",
        aggressive=False,
        sanitize=lambda text, aggressive=False: text,
    )
    assert "Mist on the Missouri" in prompt
    assert "early 1800" in prompt


def test_build_scene_anchor_t2i_prompt_environment_uses_video_prompt_only():
    """No opening_frame and no character_id → segment video prompt, not portrait fallback intro."""
    prompt = build_scene_anchor_t2i_prompt(
        prompt_without_markers_sanitized=(
            "Wide shot of the Missouri River at dawn, keelboat mid-distance."
        ),
        segment_index=1,
        opening_frames=[None],
        world_prefix_for_i2i="Photorealistic period scene set in early 1800's.",
        aggressive=False,
        sanitize=lambda text, aggressive=False: text,
        character_id="",
    )
    assert "Wide shot of the Missouri River" in prompt
    assert "early 1800" in prompt
    assert "reference portrait" not in prompt.lower()
    assert "Match face and general likeness" not in prompt


def test_run_segment_anchor_to_disk_dispatches_to_t2i_without_portrait(monkeypatch, tmp_path):
    calls: list[str] = []

    def fake_t2i(**kwargs):
        calls.append("t2i")
        return "http://example.test/a.png", tmp_path / "anchors" / "02_scene.png"

    def fake_i2i(**kwargs):
        calls.append("i2i")
        return None, None

    monkeypatch.setattr(
        "video_vendors.fal_scene_anchor_i2i.run_scene_anchor_t2i_to_disk",
        fake_t2i,
    )
    monkeypatch.setattr(
        "video_vendors.fal_scene_anchor_i2i.run_scene_anchor_i2i_to_disk",
        fake_i2i,
    )
    run_segment_anchor_to_disk(
        repo_root=tmp_path,
        segment_index=2,
        prompt_without_markers_sanitized="Mountains in snow.",
        output_dir=tmp_path,
        opening_frames=[None, None],
        world_prefix_for_i2i="Period scene.",
        character_id=None,
    )
    assert calls == ["t2i"]
