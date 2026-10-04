"""Tests for pipeline.scene_anchor_quality composite-seam detection."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from pipeline.scene_anchor_quality import (
    SceneAnchorQualityError,
    detect_composite_seam_anchor,
    scene_anchor_quality_check_enabled,
)


def _png_bytes(
    top_rgb: tuple[int, int, int], bottom_rgb: tuple[int, int, int], h: int = 200
) -> bytes:
    w = 120
    img = Image.new("RGB", (w, h))
    mid = h // 2
    for y in range(h):
        color = top_rgb if y < mid else bottom_rgb
        for x in range(w):
            img.putpixel((x, y), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_detects_white_top_green_bottom_composite():
    data = _png_bytes((250, 250, 250), (40, 90, 35))
    bad, code = detect_composite_seam_anchor(data)
    assert bad is True
    assert code


def test_uniform_forest_not_flagged():
    data = _png_bytes((45, 80, 30), (50, 85, 38))
    bad, _ = detect_composite_seam_anchor(data)
    assert bad is False


def _mostly_white_with_subject_png() -> bytes:
    """Synthetic failed animal anchor: small subject on flat white (no river scene)."""
    w, h = 120, 200
    img = Image.new("RGB", (w, h), (252, 252, 252))
    for y in range(70, 130):
        for x in range(40, 80):
            img.putpixel((x, y), (55, 38, 22))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_detects_uniform_studio_white_field():
    data = _mostly_white_with_subject_png()
    bad, code = detect_composite_seam_anchor(data)
    assert bad is True
    assert code == "uniform_studio_white_background"


def test_detects_real_seaman_white_anchor_if_present():
    from pathlib import Path

    p = Path("movie-images/18040608/anchors/06_seaman.png")
    if not p.is_file():
        pytest.skip("episode anchor not on disk")
    bad, code = detect_composite_seam_anchor(p.read_bytes())
    assert bad is True
    assert code == "uniform_studio_white_background"


def test_scene_anchor_quality_error_message():
    err = SceneAnchorQualityError(8, "portrait_white_band_above_scene", "top white 40%")
    assert "8" in str(err)
    assert "portrait_white_band_above_scene" in str(err)


def test_quality_check_env_off(monkeypatch):
    monkeypatch.setenv("FAL_SCENE_ANCHOR_QUALITY_CHECK", "0")
    assert scene_anchor_quality_check_enabled() is False
