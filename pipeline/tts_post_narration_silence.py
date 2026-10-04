"""Per-segment trailing silence after spoken audio (narration-to-mp3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ffmpeg

# FAL Wan clips cap at 10s; assembly stretches video to match audio duration.
MAX_POST_NARRATION_SILENCE_SECONDS = 10.0


def parse_post_narration_silence_seconds(raw: Any) -> float:
    """Return clamped trailing silence seconds (0 if missing/invalid)."""
    if raw is None:
        return 0.0
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0.0:
        return 0.0
    return min(MAX_POST_NARRATION_SILENCE_SECONDS, v)


def segment_post_narration_silence_seconds(entry: dict[str, Any] | None) -> float:
    if not isinstance(entry, dict):
        return 0.0
    return parse_post_narration_silence_seconds(entry.get("post_narration_silence_seconds"))


def post_narration_silence_review_warnings(nar: dict[str, Any]) -> list[str]:
    """Non-blocking warnings when Phase 1 overuses trailing silence."""
    script = nar.get("narration_script")
    if not isinstance(script, list):
        return []
    holds: list[tuple[int, float]] = []
    for i, row in enumerate(script, start=1):
        if not isinstance(row, dict):
            continue
        sec = segment_post_narration_silence_seconds(row)
        if sec > 0.0:
            holds.append((i, sec))
    if not holds:
        return []
    warnings: list[str] = []
    total = sum(s for _, s in holds)
    if len(holds) > 2:
        warnings.append(
            f"post_narration_silence_seconds on {len(holds)} segments "
            f"({', '.join(str(i) for i, _ in holds)}); aim for at most 2 per episode."
        )
    if total > 12.0:
        warnings.append(
            f"post_narration_silence_seconds total {total:.1f}s exceeds ~12s episode budget."
        )
    return warnings


def cap_post_narration_silence_in_script(
    script: list[Any],
    *,
    max_segments: int = 2,
    max_total_seconds: float = 12.0,
) -> int:
    """Drop excess post_narration_silence_seconds in place (Phase 1 overuse). Returns keys removed."""
    if not script:
        return 0
    holders: list[tuple[int, float]] = []
    for idx, row in enumerate(script):
        if not isinstance(row, dict):
            continue
        sec = segment_post_narration_silence_seconds(row)
        if sec > 0.0:
            holders.append((idx, sec))
    if not holders:
        return 0
    removed = 0
    keep: list[tuple[int, float]]
    if len(holders) > max_segments:
        keep = sorted(holders, key=lambda x: (-x[1], x[0]))[:max_segments]
        keep_set = {i for i, _ in keep}
        for idx, row in enumerate(script):
            if not isinstance(row, dict):
                continue
            if idx not in keep_set and row.pop("post_narration_silence_seconds", None) is not None:
                removed += 1
    else:
        keep = holders
    total = sum(s for _, s in keep)
    if total > max_total_seconds and total > 0:
        factor = max_total_seconds / total
        for idx, sec in keep:
            row = script[idx]
            if isinstance(row, dict):
                row["post_narration_silence_seconds"] = round(sec * factor, 2)
    return removed


def _probe_audio_format(path: Path) -> tuple[int, int]:
    info = ffmpeg.probe(str(path))
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            return int(s.get("sample_rate") or 24000), int(s.get("channels") or 1)
    return 24000, 1


def _silence_mp3(out_path: Path, duration_sec: float, sample_rate: int, channels: int) -> None:
    layout = "mono" if channels == 1 else "stereo"
    src = ffmpeg.input(f"anullsrc=r={sample_rate}:cl={layout}", f="lavfi", t=float(duration_sec))
    (
        ffmpeg.output(src, str(out_path), acodec="libmp3lame", audio_bitrate="192k")
        .overwrite_output()
        .run(quiet=True)
    )


def append_post_narration_silence_to_mp3(
    mp3_path: Path,
    silence_sec: float,
    *,
    work_dir: Path,
    segment_index: int,
) -> None:
    """Concatenate trailing silence onto an existing segment MP3 in place."""
    sec = parse_post_narration_silence_seconds(silence_sec)
    if sec <= 0.0 or not mp3_path.is_file():
        return
    sr, ch = _probe_audio_format(mp3_path)
    pause_part = work_dir / f"_part_{segment_index:02}_post_narr.mp3"
    _silence_mp3(pause_part, sec, sr, ch)
    tmp_out = work_dir / f"_part_{segment_index:02}_with_post_narr.mp3"
    streams = [ffmpeg.input(str(mp3_path)), ffmpeg.input(str(pause_part))]
    joined = ffmpeg.concat(*streams, v=0, a=1).node[0]
    (ffmpeg.output(joined, str(tmp_out), acodec="libmp3lame").overwrite_output().run(quiet=True))
    tmp_out.replace(mp3_path)
    try:
        pause_part.unlink()
    except OSError:
        pass
