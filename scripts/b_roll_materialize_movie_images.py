#!/usr/bin/env python3
"""
Copy or symlink library (or other repo) MP4s into movie-images/<date_id>/NN.mp4
for a subset of narration segments, and write b_roll_episode.json (provenance).

Usage:
  python scripts/b_roll_materialize_movie_images.py 18040513 \\
    1=b_roll_library/clips/18040510_01.mp4 \\
    3=movie-images/18040507/07.mp4

  python scripts/b_roll_materialize_movie_images.py 18040513 --clear-sidecar-only

Each assignment is segment_index=repo_relative_path (split on the first '=').
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pipeline.narration_utils import load_narration  # noqa: E402

SIDECAR_NAME = "b_roll_episode.json"


def _narration_segment_count(data: dict[str, Any]) -> int:
    script = data.get("narration_script")
    if not isinstance(script, list):
        return 0
    return len(script)


def _safe_repo_file(repo_root: Path, rel: str) -> Path | None:
    if not rel or not isinstance(rel, str):
        return None
    norm = rel.replace("\\", "/").strip().lstrip("/")
    if ".." in norm or "//" in norm:
        return None
    root = repo_root.resolve()
    p = (root / norm).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    return p if p.is_file() else None


def _parse_assignments(tokens: list[str]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if "=" not in tok:
            raise ValueError(f"Expected segment=path, got: {tok!r}")
        left, right = tok.split("=", 1)
        left = left.strip()
        right = right.strip()
        if not left or not right:
            raise ValueError(f"Expected segment=path, got: {tok!r}")
        try:
            seg_i = int(left, 10)
        except ValueError as e:
            raise ValueError(f"Invalid segment index in: {tok!r}") from e
        out.append((seg_i, right))
    return out


def _fal_cover_segment_keys(video_dir: Path) -> set[int]:
    p = video_dir / "fal_segment_covers.json"
    if not p.is_file():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    segs = data.get("segments")
    if not isinstance(segs, dict):
        return set()
    keys: set[int] = set()
    for k in segs:
        try:
            keys.add(int(str(k), 10))
        except ValueError:
            continue
    return keys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize selected segment clips from B-roll or other repo MP4 paths."
    )
    parser.add_argument(
        "date_id",
        help="Eight-digit date id (e.g. 18040513); writes under movie-images/<date_id>/",
    )
    parser.add_argument(
        "assignments",
        nargs="*",
        metavar="SEG=PATH",
        help="e.g. 1=b_roll_library/clips/foo.mp4 (split on first '=')",
    )
    parser.add_argument(
        "--symlink",
        action="store_true",
        help="Symlink instead of copy (default: copy)",
    )
    parser.add_argument(
        "--clear-sidecar-only",
        action="store_true",
        help=f"Remove movie-images/<date_id>/{SIDECAR_NAME} only; no copies.",
    )
    args = parser.parse_args()
    date_id = args.date_id.strip()
    if not re.fullmatch(r"\d{8}", date_id):
        print(f"[ERROR] date_id must be 8 digits, got: {date_id!r}", file=sys.stderr)
        sys.exit(1)

    out_dir = _REPO / "movie-images" / date_id
    sidecar_path = out_dir / SIDECAR_NAME

    if args.clear_sidecar_only:
        if args.assignments:
            print("[ERROR] Do not pass SEG=PATH with --clear-sidecar-only", file=sys.stderr)
            sys.exit(1)
        if sidecar_path.is_file():
            sidecar_path.unlink()
            print(f"[OK] Removed {sidecar_path.relative_to(_REPO)}")
        else:
            print(f"[INFO] No sidecar at {sidecar_path.relative_to(_REPO)}")
        return

    if not args.assignments:
        print("[ERROR] Provide at least one SEG=PATH or use --clear-sidecar-only", file=sys.stderr)
        sys.exit(1)

    narr = load_narration(date_id, _REPO / "narrations")
    if not narr:
        print(f"[ERROR] Missing or invalid narrations/narration{date_id}.json", file=sys.stderr)
        sys.exit(1)
    n_seg = _narration_segment_count(narr)
    if n_seg < 1:
        print("[ERROR] Narration has no narration_script segments", file=sys.stderr)
        sys.exit(1)

    try:
        parsed = _parse_assignments(list(args.assignments))
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    seen: set[int] = set()
    rows: list[dict[str, Any]] = []
    for seg_i, rel in parsed:
        if seg_i < 1 or seg_i > n_seg:
            print(
                f"[ERROR] segment_index {seg_i} out of range 1..{n_seg} for narration",
                file=sys.stderr,
            )
            sys.exit(1)
        if seg_i in seen:
            print(f"[ERROR] Duplicate segment_index: {seg_i}", file=sys.stderr)
            sys.exit(1)
        seen.add(seg_i)
        src = _safe_repo_file(_REPO, rel)
        if src is None:
            print(f"[ERROR] Source missing or unsafe path: {rel!r}", file=sys.stderr)
            sys.exit(1)
        norm_rel = str(src.relative_to(_REPO)).replace("\\", "/")
        rows.append({"segment_index": seg_i, "source_repo_rel": norm_rel})

    out_dir.mkdir(parents=True, exist_ok=True)
    cover_keys = _fal_cover_segment_keys(out_dir)
    overlap = sorted(seen & cover_keys)
    if overlap:
        print(
            "[WARN] fal_segment_covers.json references segment(s) "
            f"{overlap}; edit or remove those entries if masks are wrong for B-roll clips.",
            file=sys.stderr,
        )

    for seg_i, rel in parsed:
        src = _safe_repo_file(_REPO, rel)
        assert src is not None
        dst = out_dir / f"{seg_i:02d}.mp4"
        if args.symlink:
            try:
                if dst.is_symlink() or dst.exists():
                    dst.unlink()
            except OSError as e:
                print(f"[ERROR] Could not replace {dst}: {e}", file=sys.stderr)
                sys.exit(1)
            try:
                os.symlink(src, dst, target_is_directory=False)
            except OSError as e:
                print(f"[ERROR] Symlink failed {dst} -> {src}: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            shutil.copy2(src, dst)
        print(f"[OK] {seg_i:02d}.mp4 <- {src.relative_to(_REPO)}")

    payload = {
        "version": 1,
        "date_id": date_id,
        "segments": sorted(rows, key=lambda r: r["segment_index"]),
    }
    sidecar_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] Wrote {sidecar_path.relative_to(_REPO)}")


if __name__ == "__main__":
    main()
