"""Heuristics for FAL scene-anchor i2i stills (detect portrait-on-white + forest seam composites)."""

from __future__ import annotations

import io
import statistics


class SceneAnchorQualityError(RuntimeError):
    """Raised when a scene-anchor still fails post-i2i quality checks after one retry."""

    def __init__(self, segment_index: int, reason_code: str, detail: str) -> None:
        self.segment_index = segment_index
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(
            f"Segment {segment_index}: scene anchor quality failed ({reason_code}): {detail}"
        )


def _band_metrics(img, y0: int, y1: int) -> tuple[float, float]:
    """Return (near_white_fraction, luma_stddev) for a horizontal band."""
    w, h = img.size
    y0 = max(0, min(h - 1, y0))
    y1 = max(y0 + 1, min(h, y1))
    region = img.crop((0, y0, w, y1))
    px = list(region.getdata())
    step = max(1, len(px) // 2500)
    sample = px[::step]
    if not sample:
        return 0.0, 0.0
    white = 0
    lumas: list[float] = []
    for r, g, b in sample:
        if r > 235 and g > 235 and b > 235:
            white += 1
        lumas.append(0.299 * r + 0.587 * g + 0.114 * b)
    return white / len(sample), float(statistics.pstdev(lumas) if len(lumas) > 1 else 0.0)


def _horizontal_seam_score(img) -> float:
    """Large mean luma step across a single row suggests a cut-and-paste seam."""
    w, h = img.size
    if h < 100:
        return 0.0
    row_means: list[float] = []
    step_x = max(1, w // 64)
    for y in range(h):
        acc = 0.0
        n = 0
        for x in range(0, w, step_x):
            r, g, b = img.getpixel((x, y))
            acc += 0.299 * r + 0.587 * g + 0.114 * b
            n += 1
        row_means.append(acc / max(n, 1))
    best = 0.0
    for y in range(1, h):
        jump = abs(row_means[y] - row_means[y - 1])
        if jump > best:
            best = jump
    return best


def _quality_thresholds() -> dict[str, float]:
    """Defaults for post-i2i checks; override via ``fal_scene_anchor_quality`` in narration config."""
    defaults = {
        "uniform_white_top_min": 0.55,
        "uniform_white_bottom_min": 0.45,
        "mostly_white_frame_min": 0.65,
    }
    try:
        from pipeline.narration_common import load_narration_config

        block = (load_narration_config() or {}).get("fal_scene_anchor_quality")
        if isinstance(block, dict):
            for key in defaults:
                if key in block:
                    defaults[key] = float(block[key])
    except Exception:
        pass
    return defaults


def detect_composite_seam_anchor(image_bytes: bytes) -> tuple[bool, str]:
    """
    True when a scene-anchor still is likely unusable for Wan i2v.

    Detects:
    - studio portrait band stitched above an outdoor lower band (legacy seam heuristics);
    - uniform near-white fields (failed animal/human i2i — subject on white with no scene).

    Tuned for 9:16 Wan i2i outputs; may false-positive on heavy fog/snow (rare for this pipeline).
    """
    try:
        from PIL import Image
    except ImportError:
        return False, ""

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return False, ""

    w, h = img.size
    if w < 64 or h < 96:
        return False, ""

    th = _quality_thresholds()

    top_white, top_std = _band_metrics(img, 0, int(h * 0.42))
    bot_white, bot_std = _band_metrics(img, int(h * 0.58), h)
    full_white, _full_std = _band_metrics(img, 0, h)
    seam_jump = _horizontal_seam_score(img)

    white_band = top_white >= 0.22 and bot_white <= 0.14 and top_std <= 55.0
    texture_split = top_white >= 0.18 and bot_std >= max(45.0, top_std * 2.2) and bot_white <= 0.2
    hard_seam = seam_jump >= 38.0 and top_white >= 0.15 and bot_white <= 0.22

    if white_band:
        return True, "portrait_white_band_above_scene"
    if texture_split:
        return True, "upper_studio_lower_textured_scene"
    if hard_seam:
        return True, "horizontal_luminance_seam"

    # i2i sometimes returns a subject on a flat white field (common on animal anchors); both
    # bands stay bright white, so legacy seam rules (white top + textured bottom) never fire.
    if (
        top_white >= th["uniform_white_top_min"]
        and bot_white >= th["uniform_white_bottom_min"]
        and full_white >= th["mostly_white_frame_min"]
    ):
        return True, "uniform_studio_white_background"

    return False, ""


def scene_anchor_quality_check_enabled() -> bool:
    import os

    from pipeline.narration_common import load_narration_config

    raw = (os.environ.get("FAL_SCENE_ANCHOR_QUALITY_CHECK") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    cfg = load_narration_config() or {}
    block = cfg.get("fal_scene_anchor_quality")
    if isinstance(block, dict):
        return bool(block.get("enabled", True))
    return True
