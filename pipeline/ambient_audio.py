#!/usr/bin/env python3
"""
Optional ambient bed under narration, mixed at assembly (videos-mp3-to-movie).

Reads audio_design from narration JSON and loops/clips from ambient_library/ (repo root).
Default: episode-level audio_design.ambient_tag for every duration slice.

Optional audio_design.ambient_regions: list of
  {"segment_indices": [1, 2, ...], "ambient_tag": "river" | "none" | ...}
Segment indices are 1-based, aligned with durations entries whose file is segments/NN.mp3.
Intro (file "intro") is segment_index 0 if present. Segments not listed use ambient_tag.

At most two distinct non-none ambient_tag values are allowed across ambient_regions.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path


def ambient_library_dir(repo_root: Path) -> Path:
    return repo_root / "ambient_library"


def load_manifest(repo_root: Path) -> dict | None:
    p = ambient_library_dir(repo_root) / "manifest.json"
    if not p.exists():
        print(f"[WARN] Ambient manifest missing: {p}", file=sys.stderr)
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[WARN] Ambient manifest invalid: {e}", file=sys.stderr)
        return None


def _normalize_tag(tag: str) -> str:
    t = str(tag).strip().lower().replace(" ", "_")
    return t or "prairie_wind"


def resolve_tag_to_path(
    tag: str,
    manifest: dict,
    library_dir: Path,
    *,
    allow_default: bool = True,
) -> tuple[Path | None, float]:
    """
    Return (path or None if missing, gain_db_offset from manifest entry).
    """
    t = _normalize_tag(tag)
    if t == "none":
        return None, 0.0
    tags = manifest.get("tags") or {}
    entry = tags.get(t)
    if entry is None:
        for k, v in tags.items():
            if str(k).lower() == t:
                entry = v
                break
    if entry is None and allow_default:
        df = manifest.get("default_tag")
        if df and str(df).strip() and _normalize_tag(df) != t:
            return resolve_tag_to_path(str(df), manifest, library_dir, allow_default=False)
    if not isinstance(entry, dict):
        print(f"[WARN] Unknown ambient tag {tag!r}; no bed for this slice.", file=sys.stderr)
        return None, 0.0
    fn = entry.get("file")
    off = float(entry.get("gain_db_offset", 0))
    if not fn:
        return None, off
    path = library_dir / str(fn)
    if path.exists():
        return path, off
    print(f"[WARN] Ambient file missing: {path}; using silence for this slice.", file=sys.stderr)
    return None, off


def _duration_entry_segment_index(entry: dict) -> int | None:
    """Map durations.json entry to segment index: intro -> 0, segments/07.mp3 -> 7."""
    f = str(entry.get("file") or "").replace("\\", "/").strip()
    if f == "intro":
        return 0
    m = re.match(r"^segments/(\d+)\.mp3$", f, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def build_piece_specs(
    durations: list[dict],
    narration_data: dict | None,
    manifest: dict,
) -> list[tuple[float, str]]:
    """Each entry: (duration_seconds, normalized_tag)."""
    audio_design = (narration_data or {}).get("audio_design") or {}
    ep_tag = audio_design.get("ambient_tag")
    default_t = _normalize_tag(
        ep_tag if ep_tag and str(ep_tag).strip() else manifest.get("default_tag", "prairie_wind")
    )

    seg_to_tag: dict[int, str] = {}
    regions = audio_design.get("ambient_regions")
    if isinstance(regions, list) and regions:
        manifest_keys = {_normalize_tag(str(k)) for k in (manifest.get("tags") or {})}
        non_none_distinct: set[str] = set()
        for block in regions:
            if not isinstance(block, dict):
                continue
            raw_tag = block.get("ambient_tag")
            tag_s = str(raw_tag).strip() if raw_tag is not None else ""
            if tag_s.lower() == "none" or tag_s == "":
                nt = "none"
            else:
                nt = _normalize_tag(tag_s)
                if nt != "none":
                    if nt not in manifest_keys:
                        print(
                            f"[WARN] ambient_regions unknown ambient_tag {raw_tag!r}; using none.",
                            file=sys.stderr,
                        )
                        nt = "none"
                    else:
                        non_none_distinct.add(nt)
            indices = block.get("segment_indices") or []
            if not isinstance(indices, list):
                continue
            for idx in indices:
                if isinstance(idx, int):
                    seg_to_tag[idx] = nt
        if len(non_none_distinct) > 2:
            raise ValueError(
                "audio_design.ambient_regions allows at most 2 distinct non-none "
                f"ambient_tag values; got {sorted(non_none_distinct)}"
            )

    specs: list[tuple[float, str]] = []
    for entry in durations:
        dur = float(entry["duration"])
        si = _duration_entry_segment_index(entry)
        if si is not None and si in seg_to_tag:
            tag = seg_to_tag[si]
        else:
            tag = default_t
        specs.append((dur, tag))
    return specs


def merge_consecutive_same_tag(specs: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """
    Combine adjacent timeline pieces that use the same ambient tag so one looped bed
    runs continuously instead of restarting the WAV at every segment boundary.
    """
    if not specs:
        return []
    out: list[tuple[float, str]] = []
    acc = 0.0
    cur_tag: str | None = None
    for dur, tag in specs:
        if dur <= 0.001:
            continue
        if cur_tag is None:
            acc = dur
            cur_tag = tag
        elif tag == cur_tag:
            acc += dur
        else:
            out.append((acc, cur_tag))
            acc = dur
            cur_tag = tag
    if cur_tag is not None:
        out.append((acc, cur_tag))
    return out


def ambience_level_from_narration(narration_data: dict | None) -> str:
    ad = (narration_data or {}).get("audio_design") or {}
    return str(ad.get("ambience_level", "none")).strip().lower()


# Small dB offsets under the manifest ambience_level_gain_db baseline (not a full music mixer).
_TONE_REGISTER_AMBIENT_DB: dict[str, float] = {
    "light": 1.0,
    "warm": 0.5,
    "reflective": 0.0,
    "tense": -1.5,
    "somber": -2.0,
}

_MUSIC_INTENSITY_CURVE_AMBIENT_DB: dict[str, float] = {
    "flat": 0.0,
    "minimal": -1.0,
    "gradual_build": 0.5,
    "swell_then_fade": 1.0,
}


def ambient_bed_gain_adjustment_db(narration_data: dict | None) -> float:
    """
    Optional narration-driven tweak to ambient bed level (tone_register, music_intensity_curve).

    ``music_intensity_curve`` is a coarse offset until a dedicated music bed exists.
    """
    if not narration_data:
        return 0.0
    adj = 0.0
    tone = str(narration_data.get("tone_register") or "").strip().lower()
    if tone in _TONE_REGISTER_AMBIENT_DB:
        adj += _TONE_REGISTER_AMBIENT_DB[tone]
    ad = narration_data.get("audio_design")
    if isinstance(ad, dict):
        curve = str(ad.get("music_intensity_curve") or "").strip().lower()
        if curve in _MUSIC_INTENSITY_CURVE_AMBIENT_DB:
            adj += _MUSIC_INTENSITY_CURVE_AMBIENT_DB[curve]
    return adj


def maybe_mix_ambient_under_voice(
    voice_stream,
    *,
    narration_data: dict | None,
    durations: list[dict],
    use_ambient: bool,
    voice_sample_rate: int,
    repo_root: Path,
) -> tuple[object | None, str | None]:
    """
    If use_ambient and narration requests light/moderate ambience, return
    (ffmpeg audio stream mixing voice + ambient, temp_dir_to_delete).
    Otherwise return (None, None).

    With ambient_regions, the bed can switch tags across segment boundaries (e.g. river, none, river).
    Per-merged-slice copies of library files go under
    temp_dir so ffmpeg-python does not merge duplicate -i paths (which would break aloop chains).
    """
    import ffmpeg

    if not use_ambient:
        return None, None
    level = ambience_level_from_narration(narration_data)
    if level not in ("light", "moderate"):
        return None, None
    manifest = load_manifest(repo_root)
    if not manifest:
        return None, None
    gains = manifest.get("ambience_level_gain_db") or {}
    base_db = gains.get(level)
    if base_db is None:
        base_db = -20.0
    else:
        base_db = float(base_db)
    base_db += ambient_bed_gain_adjustment_db(narration_data)
    highpass_hz = int(manifest.get("highpass_hz", 120))
    library_dir = ambient_library_dir(repo_root)
    segment_specs = build_piece_specs(durations, narration_data, manifest)
    specs = merge_consecutive_same_tag(segment_specs)
    temp_dir = tempfile.mkdtemp(prefix="lc_ambient_")
    streams = []
    try:
        for i, (dur, tag) in enumerate(specs):
            if dur <= 0.001:
                continue
            path, off = resolve_tag_to_path(tag, manifest, library_dir)
            piece_db = base_db + float(off)
            if path is not None:
                dst = Path(temp_dir) / f"slice_{i:04d}{path.suffix}"
                shutil.copy2(path, dst)
                s = (
                    ffmpeg.input(str(dst))
                    .audio.filter("aloop", loop=-1, size=2147483647)
                    .filter("atrim", duration=dur)
                    .filter("asetpts", "N/SR/TB")
                )
            else:
                s = ffmpeg.input(f"anullsrc=r={voice_sample_rate}:cl=mono", f="lavfi", t=dur).audio
            s = s.filter("volume", f"{piece_db}dB")
            streams.append(s)
        if not streams:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None, None
        if len(streams) == 1:
            amb = streams[0]
        else:
            amb = ffmpeg.filter(streams, "concat", n=len(streams), v=0, a=1)
        amb = amb.filter("highpass", f=highpass_hz)
        amb = amb.filter("aresample", voice_sample_rate).filter(
            "aformat",
            sample_fmts="fltp",
            sample_rates=voice_sample_rate,
            channel_layouts="mono",
        )
        voice_fmt = voice_stream.filter(
            "aformat",
            sample_fmts="fltp",
            sample_rates=voice_sample_rate,
            channel_layouts="mono",
        )
        total_seg = len(segment_specs)
        total_merged = len(specs)
        seg_note = (
            f"{total_seg} segment(s) -> {total_merged} bed slice(s)"
            if total_merged != total_seg
            else f"{total_merged} bed slice(s)"
        )
        print(
            f"[OK] Mixing ambient (ambience_level={level}, {seg_note}, "
            f"~{sum(d for d, _ in specs):.1f}s).",
        )
        mixed = ffmpeg.filter(
            [voice_fmt, amb],
            "amix",
            inputs=2,
            duration="first",
            dropout_transition=0,
        )
        return mixed, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
