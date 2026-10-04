#!/usr/bin/env python3
"""
Repair mistaken ``movie-images/<date_id>/anchors/anchors/`` folders created when
``output_dir`` was already ``.../<date_id>/anchors`` (double ``anchors``).

Moves files into ``movie-images/<date_id>/anchors/`` and removes the empty inner folder.

Run from repo root:
  python scripts/fix_nested_movie_images_anchors.py
  python scripts/fix_nested_movie_images_anchors.py --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MOVIE = _REPO_ROOT / "movie-images"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Flatten nested anchors/anchors under movie-images."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions only; do not move or delete.",
    )
    args = parser.parse_args()

    if not _MOVIE.is_dir():
        print(f"No {_MOVIE} directory.", file=sys.stderr)
        sys.exit(0)

    nested = sorted(_MOVIE.glob("*/anchors/anchors"))
    if not nested:
        print("No nested movie-images/*/anchors/anchors directories found.")
        sys.exit(0)

    for inner in nested:
        if not inner.is_dir():
            continue
        segment_anchors = inner.parent
        if segment_anchors.name.lower() != "anchors":
            continue
        print(f"Found: {inner.relative_to(_REPO_ROOT)}")
        for f in sorted(inner.iterdir()):
            if not f.is_file():
                print(f"  skip non-file: {f.name}", file=sys.stderr)
                continue
            target = segment_anchors / f.name
            if args.dry_run:
                print(f"  would move: {f.name} -> {target.relative_to(_REPO_ROOT)}")
                continue
            if target.is_file() and target.resolve() != f.resolve():
                bak = target.with_suffix(target.suffix + ".bak_nested_fix")
                n = 0
                while bak.is_file():
                    n += 1
                    bak = target.with_suffix(target.suffix + f".bak_nested_fix{n}")
                shutil.move(str(target), str(bak))
                print(f"  backed up existing: {bak.name}")
            shutil.move(str(f), str(target))
            print(f"  moved: {f.name}")
        if args.dry_run:
            continue
        try:
            inner.rmdir()
            print(f"  removed empty: {inner.relative_to(_REPO_ROOT)}")
        except OSError as e:
            print(f"  [WARN] could not remove {inner}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
