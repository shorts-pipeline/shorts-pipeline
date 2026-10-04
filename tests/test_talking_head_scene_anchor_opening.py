"""Talking-head FAL path must not skip scene-anchor i2i when opening_frame is missing on the row."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "narration_to_video",
    _REPO / "narration-to-video.py",
)
assert _spec and _spec.loader
_ntv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ntv)
_talking_head_opening_frames_for_i2i = _ntv._talking_head_opening_frames_for_i2i
_require_talking_head_scene_anchor_source = _ntv._require_talking_head_scene_anchor_source


def test_synthesizes_opening_when_row_and_meta_empty() -> None:
    row = {"video_prompt": "Clark studies the river width. Men haul the keelboat."}
    out = _talking_head_opening_frames_for_i2i(
        row,
        segment_index=3,
        subject_id="clark",
        fal_openings=[None, None, None],
        world_prefix="Photorealistic period scene: early 1800's, Missouri River bank.",
    )
    assert len(out) >= 3
    text = out[2]
    assert text and "clark" in text.lower()
    # From video_prompt lead sentence, or full template fallback when prompt empty
    assert "river" in text.lower() or "white void" in text.lower()


def test_keeps_existing_opening_from_row() -> None:
    row = {"opening_frame": "Clark at the gunwale, river mist behind."}
    out = _talking_head_opening_frames_for_i2i(
        row,
        segment_index=1,
        subject_id="clark",
        fal_openings=[None],
        world_prefix="",
    )
    assert out[0] == "Clark at the gunwale, river mist behind."


def test_require_scene_anchor_rejects_missing_i2i_output(tmp_path: Path) -> None:
    portrait = tmp_path / "lewis.png"
    portrait.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="scene-anchor i2i did not produce"):
        _require_talking_head_scene_anchor_source(
            3,
            "lewis",
            portrait_path=portrait,
            source_image_url=None,
            source_image_path=None,
        )


def test_require_scene_anchor_accepts_on_disk_still(tmp_path: Path) -> None:
    portrait = tmp_path / "lewis.png"
    portrait.write_bytes(b"x")
    anchor = tmp_path / "03_lewis.png"
    anchor.write_bytes(b"y")
    _require_talking_head_scene_anchor_source(
        3,
        "lewis",
        portrait_path=portrait,
        source_image_url=None,
        source_image_path=anchor,
    )
