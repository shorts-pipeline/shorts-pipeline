"""Tests for OmniHuman shared master + mask_url conversation anchors."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from pipeline.conversation_omnihuman_mask import (
    MasterReframe,
    build_speaker_mask_png,
    existing_omnihuman_anchor_for_segment,
    omnihuman_shorts_master_path,
    omnihuman_speaker_mask_path,
    reframe_master_for_omnihuman,
)
from pipeline.conversation_scene_anchor import (
    ConversationAnchorRun,
    conversation_master_image_size,
    conversation_scene_anchor_strategy,
    conversation_uses_shared_master_mask,
)


def _wide_master_bytes(w: int = 1440, h: int = 1280) -> bytes:
    img = Image.new("RGB", (w, h), (40, 38, 36))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_reframe_master_contain_letterbox_to_shorts() -> None:
    out_bytes, transform = reframe_master_for_omnihuman(
        _wide_master_bytes(1440, 1280), aspect_ratio="9:16"
    )
    img = Image.open(io.BytesIO(out_bytes))
    assert img.size == (720, 1280)
    assert transform.dst_w == 720
    assert transform.dst_h == 1280
    assert transform.scale == pytest.approx(0.5, rel=1e-3)
    # Center column of wide master maps near horizontal center of Shorts canvas.
    cx, cy = transform.map_point(720, 640)
    assert 340 <= cx <= 380


def test_master_reframe_map_point_clamps() -> None:
    t = MasterReframe(0.5, 10, 20, 1440, 1280, 720, 1280)
    x, y = t.map_point(10000, -100)
    assert 0 <= x < 720
    assert 0 <= y < 1280


def test_build_speaker_mask_white_ellipse_on_black() -> None:
    raw = build_speaker_mask_png(720, 1280, 360, 400)
    mask = Image.open(io.BytesIO(raw)).convert("L")
    assert mask.size == (720, 1280)
    assert mask.getpixel((360, 400)) == 255
    assert mask.getpixel((0, 0)) == 0


def test_omnihuman_paths_and_existing_anchor(tmp_path: Path) -> None:
    run = ConversationAnchorRun(
        first_segment_index=3,
        segment_indices=(3, 4),
        composite_id="lewis_clark",
        left_speaker_id="lewis",
        right_speaker_id="clark",
        shared_setting="campfire",
    )
    out = tmp_path / "movie-images" / "18030101"
    anchors = out / "anchors"
    anchors.mkdir(parents=True)
    omni = omnihuman_shorts_master_path(out, run)
    omni.write_bytes(_wide_master_bytes(720, 1280))
    mask = omnihuman_speaker_mask_path(out, 3, "lewis")
    mask.write_bytes(build_speaker_mask_png(720, 1280, 200, 300))
    manifest = anchors / "_conv_03_lewis_clark_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "anchor_mode": "omnihuman_mask",
                "first_segment_index": 3,
                "composite_id": "lewis_clark",
                "segment_masks": {"3": str(mask.name)},
                "omnihuman_master": str(omni.name),
            }
        ),
        encoding="utf-8",
    )
    found = existing_omnihuman_anchor_for_segment(out, 3, "lewis")
    assert found == omni
    assert existing_omnihuman_anchor_for_segment(out, 4, "clark") is None


@patch("pipeline.conversation_scene_anchor._conversation_scene_anchor_config")
def test_strategy_shared_master_mask(mock_cfg) -> None:
    mock_cfg.return_value = {
        "enabled": True,
        "strategy": "shared_master_mask",
        "master_width_multiplier": 1,
    }
    assert conversation_scene_anchor_strategy() == "shared_master_mask"
    assert conversation_uses_shared_master_mask() is True
    size = conversation_master_image_size("9:16")
    assert size == {"width": 720, "height": 1280}
