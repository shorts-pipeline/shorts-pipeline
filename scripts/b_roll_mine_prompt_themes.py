#!/usr/bin/env python3
"""
Mine merged narrations for video_prompt themes (B-roll library seeding).

Reads canonical narration<date_id>.json files (8-digit date only), skips sidecars.
Reports top unigrams/bigrams, optional bucket hits from b_roll_library/bucket_keywords.json,
and per-segment rows with optional movie-images/<date_id>/NN.mp4 path when the file exists.

Usage:
  python scripts/b_roll_mine_prompt_themes.py [--narrations-dir narrations] [--out-dir b_roll_library]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

# Minimal English stopwords for theme token counts (extend as needed).
_STOPWORDS = frozenset(
    """
    a an the and or but if in on at to for of as is are was were be been being
    with from by it its this that these those into over under upon through
    about than then so not no yes very more most some any all each both few
    such same other another one two first second their they them his her she
    he him we you our your my me i
    """.split()
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _normalize_for_tokens(text: str) -> str:
    if not text or not isinstance(text, str):
        return ""
    t = text.lower()
    t = re.sub(r"[^a-z0-9\s]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _tokens(text: str) -> list[str]:
    return [w for w in _normalize_for_tokens(text).split() if w and w not in _STOPWORDS]


def _bigrams(tokens: list[str]) -> list[str]:
    if len(tokens) < 2:
        return []
    return [f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)]


def _match_bucket(norm_prompt: str, buckets: dict[str, list[str]]) -> str:
    """First bucket whose any keyword substring appears in norm_prompt (lower)."""
    low = norm_prompt.lower()
    for name, keys in buckets.items():
        for k in keys:
            if k.lower() in low:
                return name
    return ""


def _iter_merged_narrations(narrations_dir: Path):
    """Yield (date_id, data) for narrationDDDDDDDD.json only."""
    for p in sorted(narrations_dir.glob("narration*.json")):
        if p.name.endswith("_voice.json") or p.name.endswith("_visual.json"):
            continue
        if ".meta." in p.name:
            continue
        m = re.fullmatch(r"narration(\d{8})\.json", p.name)
        if not m:
            continue
        date_id = m.group(1)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        yield date_id, data


def _ffprobe_duration_seconds(path: Path) -> float | None:
    if not path.is_file():
        return None
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if r.returncode != 0:
            return None
        return float((r.stdout or "").strip())
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mine video_prompt themes from merged narration JSON files."
    )
    parser.add_argument(
        "--narrations-dir",
        type=Path,
        default=_repo_root() / "narrations",
        help="Directory containing narration*.json",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_repo_root() / "b_roll_library",
        help="Directory for CSV, JSON report, and default bucket_keywords.json location",
    )
    parser.add_argument(
        "--buckets",
        type=Path,
        default=None,
        help="Optional path to bucket_keywords.json (default: <out-dir>/bucket_keywords.json)",
    )
    parser.add_argument(
        "--probe-mp4",
        action="store_true",
        help="If movie-images file exists, ffprobe duration (slow for many files)",
    )
    args = parser.parse_args()

    narrations_dir: Path = args.narrations_dir
    out_dir: Path = args.out_dir
    buckets_path = args.buckets or (out_dir / "bucket_keywords.json")

    if not narrations_dir.is_dir():
        print(f"[ERROR] Narrations dir not found: {narrations_dir}")
        return

    buckets: dict[str, list[str]] = {}
    if buckets_path.is_file():
        try:
            raw = json.loads(buckets_path.read_text(encoding="utf-8"))
            b = raw.get("buckets") if isinstance(raw, dict) else None
            if isinstance(b, dict):
                buckets = {str(k): list(v) if isinstance(v, list) else [] for k, v in b.items()}
        except (json.JSONDecodeError, OSError) as e:
            print(f"[WARN] Could not load buckets from {buckets_path}: {e}")

    unigram_counts: Counter[str] = Counter()
    bigram_counts: Counter[str] = Counter()
    style_segment_counts: Counter[str] = Counter()

    rows: list[dict[str, Any]] = []
    merged_file_count = 0

    for date_id, data in _iter_merged_narrations(narrations_dir):
        merged_file_count += 1
        style = data.get("visual_style") or {}
        style_name = (style.get("name") or "").strip() or "(none)"
        script = data.get("narration_script") or []
        if not isinstance(script, list):
            continue
        for j, seg in enumerate(script):
            if not isinstance(seg, dict):
                continue
            seg_idx = seg.get("segment_index")
            if seg_idx is None:
                seg_i = j + 1
            else:
                try:
                    seg_i = int(seg_idx)
                except (TypeError, ValueError):
                    seg_i = j + 1
            vp = seg.get("video_prompt") or ""
            if not isinstance(vp, str):
                vp = str(vp)
            ref = seg.get("reference_character_id")
            has_ref = bool(ref)

            toks = _tokens(vp)
            unigram_counts.update(w for w in toks if len(w) > 1)
            bigram_counts.update(_bigrams(toks))
            style_segment_counts[style_name] += 1

            norm_full = _normalize_for_tokens(vp)
            bucket = _match_bucket(norm_full, buckets) if buckets else ""

            mp4_rel = Path("movie-images") / date_id / f"{seg_i:02d}.mp4"
            mp4_abs = _repo_root() / mp4_rel
            exists = mp4_abs.is_file()
            dur = None
            if args.probe_mp4 and exists:
                dur = _ffprobe_duration_seconds(mp4_abs)

            rows.append(
                {
                    "date_id": date_id,
                    "segment_index": seg_i,
                    "video_prompt": vp[:500] + ("…" if len(vp) > 500 else ""),
                    "bucket": bucket,
                    "style_name": style_name,
                    "has_reference_character": has_ref,
                    "mp4_path": str(mp4_rel).replace("\\", "/") if exists else "",
                    "mp4_duration_seconds": round(dur, 3) if dur is not None else "",
                }
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "prompt_theme_report.csv"
    json_path = out_dir / "prompt_mine_report.json"

    top_uni = unigram_counts.most_common(80)
    top_bi = bigram_counts.most_common(80)

    fieldnames = [
        "date_id",
        "segment_index",
        "video_prompt",
        "bucket",
        "style_name",
        "has_reference_character",
        "mp4_path",
        "mp4_duration_seconds",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})

    report = {
        "narrations_dir": str(narrations_dir.resolve()),
        "merged_files_scanned": merged_file_count,
        "unique_date_ids_in_rows": len({r["date_id"] for r in rows}),
        "total_segments": len(rows),
        "top_unigrams": top_uni,
        "top_bigrams": top_bi,
        "segments_per_visual_style": dict(style_segment_counts.most_common()),
        "buckets_file": str(buckets_path) if buckets_path else "",
        "csv": str(csv_path.resolve()),
    }
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")
    print(f"Segments: {len(rows)}  Top unigrams (sample): {top_uni[:10]}")


if __name__ == "__main__":
    main()
