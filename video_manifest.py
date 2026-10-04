#!/usr/bin/env python3
"""
Manage the video manifest JSON next to each output MP4.

Manifest path: output/<output_prefix>_{date_id}_video.manifest.json (default prefix lewis_clark)
Schema: date_id, output, aspect_ratio, length_seconds, narration_vendor, tts_vendor,
        video_vendor, cost_estimate; optionally youtube_video_id, youtube_url after upload.

run-daily creates the manifest after producing a video; youtube_upload updates it with
the YouTube link when provided.
"""

import json
import re
from pathlib import Path

# Assembled filename: <prefix>_<date_id>_video.mp4 (prefix is letters/digits/underscore; default lewis_clark)
DATE_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*_(\d{8})_video", re.IGNORECASE)


def manifest_path_for_video(video_path: Path) -> Path:
    """Return the manifest path for a video (e.g. output/lewis_clark_18030906_video.mp4 -> .../lewis_clark_18030906_video.manifest.json)."""
    return video_path.with_suffix(".manifest.json")


def load(manifest_path: Path) -> dict | None:
    """Load manifest JSON. Returns None if file does not exist or is invalid."""
    path = Path(manifest_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save(manifest_path: Path, data: dict) -> None:
    """Write manifest JSON (UTF-8, indent=2)."""
    Path(manifest_path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def create_after_pipeline(
    manifest_path: Path,
    *,
    date_id: str,
    output_name: str,
    aspect_ratio: str,
    length_seconds: float | None,
    narration_vendor: str,
    tts_vendor: str,
    video_vendor: str,
    cost_estimate: dict,
) -> None:
    """Create or overwrite manifest with pipeline metadata (used by run-daily)."""
    data = {
        "date_id": date_id,
        "output": output_name,
        "aspect_ratio": aspect_ratio,
        "length_seconds": length_seconds,
        "narration_vendor": narration_vendor,
        "tts_vendor": tts_vendor,
        "video_vendor": video_vendor,
        "cost_estimate": cost_estimate,
    }
    save(manifest_path, data)


def write_pipeline_manifest(
    video_path: Path,
    *,
    date_id: str,
    aspect_ratio: str,
    narration_vendor: str,
    tts_vendor: str,
    video_vendor: str,
    repo_root: Path | None = None,
) -> Path:
    """Probe length, estimate cost, and write the manifest next to ``video_path``.

    Shared by run-daily (after producing a video) and the Pipeline UI (after a
    stand-alone assemble). Returns the manifest path.
    """
    from pipeline.cost_estimate import estimate_video_cost

    video_path = Path(video_path)
    manifest_path = manifest_path_for_video(video_path)
    length_seconds: float | None = None
    try:
        import ffmpeg

        info = ffmpeg.probe(str(video_path))
        length_seconds = round(float(info["format"]["duration"]), 2)
    except Exception:
        pass
    try:
        cost_estimate = estimate_video_cost(
            date_id, narration_vendor, tts_vendor, video_vendor, repo_root=repo_root
        )
    except Exception:
        cost_estimate = {
            "narration_usd": None,
            "tts_usd": None,
            "video_usd": None,
            "total_usd": None,
        }
    create_after_pipeline(
        manifest_path,
        date_id=date_id,
        output_name=video_path.name,
        aspect_ratio=aspect_ratio,
        length_seconds=length_seconds,
        narration_vendor=narration_vendor,
        tts_vendor=tts_vendor,
        video_vendor=video_vendor,
        cost_estimate=cost_estimate,
    )
    return manifest_path


def _date_id_from_video_path(video_path: Path) -> str | None:
    """Extract eight-digit episode id from assembled filename stem (any output prefix)."""
    from pipeline.output_naming import date_id_from_output_video_stem

    return date_id_from_output_video_stem(Path(video_path).stem)


def refresh_from_video(
    video_path: Path,
    manifest_path: Path,
    template_manifest_path: Path | None = None,
) -> None:
    """
    Create or refresh manifest by probing the video file (length_seconds, aspect_ratio).
    If template_manifest_path is provided, copy its metadata (date_id, output, vendors,
    cost_estimate) and only override length_seconds and aspect_ratio; omit youtube_*
    so the manifest is ready for a new upload.
    """
    video_path = Path(video_path)
    manifest_path = Path(manifest_path)
    try:
        import ffmpeg

        info = ffmpeg.probe(str(video_path))
    except Exception:
        info = {}
    length_seconds = None
    dur = (info.get("format") or {}).get("duration")
    if dur is not None:
        length_seconds = round(float(dur), 2)
    aspect_ratio = None
    for s in info.get("streams") or []:
        if s.get("codec_type") == "video":
            w, h = s.get("width"), s.get("height")
            if w and h:
                aspect_ratio = "9:16" if int(h) > int(w) else "16:9"
            break
    if aspect_ratio is None:
        aspect_ratio = "16:9"
    if template_manifest_path and Path(template_manifest_path).exists():
        data = load(Path(template_manifest_path)) or {}
        for key in ("youtube_video_id", "youtube_url"):
            data.pop(key, None)
        data["output"] = video_path.name
        data["length_seconds"] = length_seconds
        data["aspect_ratio"] = aspect_ratio
        if data.get("date_id") is None:
            data["date_id"] = _date_id_from_video_path(video_path) or "unknown"
    else:
        data = {
            "date_id": _date_id_from_video_path(video_path) or "unknown",
            "output": video_path.name,
            "aspect_ratio": aspect_ratio,
            "length_seconds": length_seconds,
        }
    save(manifest_path, data)


def update_youtube(
    manifest_path: Path,
    video_id: str,
    url: str,
    video_path: Path | None = None,
) -> bool:
    """
    Update manifest with youtube_video_id and youtube_url.
    If the manifest does not exist and video_path is provided, create a minimal manifest
    (date_id derived from filename, output name) then add the YouTube fields.
    Returns True if updated or created, False if file missing and video_path not provided.
    """
    path = Path(manifest_path)
    data = load(path)
    if data is None and video_path is not None:
        date_id = _date_id_from_video_path(Path(video_path))
        output_name = Path(video_path).name
        data = {
            "date_id": date_id or "unknown",
            "output": output_name,
        }
    if data is None:
        return False
    data["youtube_video_id"] = video_id
    data["youtube_url"] = url
    save(path, data)
    return True


def update_instagram(
    manifest_path: Path,
    media_id: str,
    url: str | None = None,
    video_path: Path | None = None,
) -> bool:
    """
    Update manifest with instagram_media_id and (once known) instagram_url.
    Same create-if-missing behavior as update_youtube.
    """
    path = Path(manifest_path)
    data = load(path)
    if data is None and video_path is not None:
        date_id = _date_id_from_video_path(Path(video_path))
        output_name = Path(video_path).name
        data = {
            "date_id": date_id or "unknown",
            "output": output_name,
        }
    if data is None:
        return False
    data["instagram_media_id"] = media_id
    if url:
        data["instagram_url"] = url
    save(path, data)
    return True
