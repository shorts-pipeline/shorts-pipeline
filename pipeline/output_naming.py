"""Video output filename helpers (prefix + eight-digit episode id)."""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_VIDEO_OUTPUT_PREFIX = "lewis_clark"

# Pipeline-assembled MP4: <prefix>_<YYYYMMDD>_video.mp4 ; Sora may use video_<id>.mp4
_EPISODE_VIDEO_STEM = re.compile(
    r"^([A-Za-z][A-Za-z0-9_]*)_(\d{8})_video$",
    re.IGNORECASE,
)
_SORA_STYLE_STEM = re.compile(r"^video_(\d{8})$", re.IGNORECASE)


ANCHOR_PREVIEW_OUTPUT_PREFIX = "lewis_clark_anchor_preview"


def output_video_filename(output_prefix: str, date_id: str) -> str:
    """Basename for muxed output, e.g. lewis_clark_18030901_video.mp4."""
    p = (output_prefix or DEFAULT_VIDEO_OUTPUT_PREFIX).strip() or DEFAULT_VIDEO_OUTPUT_PREFIX
    return f"{p}_{date_id}_video.mp4"


def anchor_preview_video_filename(output_prefix: str | None = None, date_id: str = "") -> str:
    """Basename for anchor+TTS preview, e.g. lewis_clark_anchor_preview_18030901_video.mp4."""
    p = (output_prefix or ANCHOR_PREVIEW_OUTPUT_PREFIX).strip() or ANCHOR_PREVIEW_OUTPUT_PREFIX
    return f"{p}_{date_id}_video.mp4"


def output_video_path(output_dir: Path, output_prefix: str, date_id: str) -> Path:
    return Path(output_dir) / output_video_filename(output_prefix, date_id)


def date_id_from_output_video_stem(stem: str) -> str | None:
    """Parse eight-digit id from assembled filename stem, or Sora video_<id> stem."""
    m = _EPISODE_VIDEO_STEM.fullmatch(stem.strip())
    if m:
        return m.group(2)
    m2 = _SORA_STYLE_STEM.fullmatch(stem.strip())
    if m2:
        return m2.group(1)
    return None


def output_prefix_from_assembled_stem(stem: str) -> str | None:
    """Return the prefix portion for <prefix>_<id>_video stems; None for Sora-style names."""
    m = _EPISODE_VIDEO_STEM.fullmatch(stem.strip())
    if m:
        return m.group(1)
    return None
