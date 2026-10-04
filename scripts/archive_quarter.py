#!/usr/bin/env python3
"""
Move completed pipeline artifacts for one expedition calendar quarter into archive/.

Archives (under repo root):
  archive/{YYYY}-Q{n}/output/
  archive/{YYYY}-Q{n}/movie-images/{date_id}/
  archive/{YYYY}-Q{n}/audio/{date_id}/

Does not move narrations/ or journal-entries/ (pipeline defaults expect those at repo root).

Usage (from repo root, venv optional):
  python scripts/archive_quarter.py 1803 3
  python scripts/archive_quarter.py 1803 4
  python scripts/archive_quarter.py 1803 3 --dry-run
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def quarter_month_range(year: int, quarter: int) -> tuple[int, int]:
    if quarter == 1:
        return 1, 3
    if quarter == 2:
        return 4, 6
    if quarter == 3:
        return 7, 9
    if quarter == 4:
        return 10, 12
    raise ValueError("quarter must be 1-4")


def date_id_in_quarter(date_id: str, year: int, q: int) -> bool:
    if len(date_id) != 8 or not date_id.isdigit():
        return False
    y, m = int(date_id[0:4]), int(date_id[4:6])
    lo, hi = quarter_month_range(year, q)
    return y == year and lo <= m <= hi


def date_ids_touching_quarter(year: int, q: int) -> set[str]:
    """Union of date_ids found under movie-images/, audio/, and output/ for this quarter."""
    lo, hi = quarter_month_range(year, q)
    ids: set[str] = set()

    for base, _is_dir in (
        (_REPO_ROOT / "movie-images", True),
        (_REPO_ROOT / "audio", True),
    ):
        if not base.is_dir():
            continue
        for p in base.iterdir():
            if not p.is_dir():
                continue
            name = p.name
            if len(name) == 8 and name.isdigit() and date_id_in_quarter(name, year, q):
                ids.add(name)

    out = _REPO_ROOT / "output"
    if out.is_dir():
        for p in out.iterdir():
            if not p.is_file():
                continue
            m = re.search(r"(\d{8})", p.name)
            if m and date_id_in_quarter(m.group(1), year, q):
                ids.add(m.group(1))
    return ids


def move_quarter(year: int, quarter: int, *, dry_run: bool) -> list[str]:
    lo, hi = quarter_month_range(year, quarter)
    label = f"{year}-Q{quarter}"
    dest_root = _REPO_ROOT / "archive" / label
    date_ids = sorted(date_ids_touching_quarter(year, quarter))
    if not date_ids:
        return [f"{label}: nothing found to archive (months {lo:02d}–{hi:02d})."]

    log: list[str] = [f"{label}: archiving {len(date_ids)} date_id(s) -> {dest_root}"]

    for sub in ("output", "movie-images", "audio"):
        (dest_root / sub).mkdir(parents=True, exist_ok=True)

    # output files: any file whose name contains a date_id in this quarter
    out_dir = _REPO_ROOT / "output"
    if out_dir.is_dir():
        for p in list(out_dir.iterdir()):
            if not p.is_file():
                continue
            m = re.search(r"(\d{8})", p.name)
            if not m or not date_id_in_quarter(m.group(1), year, quarter):
                continue
            dest = dest_root / "output" / p.name
            log.append(f"  output/{p.name} -> archive/{label}/output/")
            if not dry_run:
                shutil.move(str(p), str(dest))

    for did in date_ids:
        for kind in ("movie-images", "audio"):
            src = _REPO_ROOT / kind / did
            if not src.is_dir():
                continue
            dest = dest_root / kind / did
            log.append(f"  {kind}/{did}/ -> archive/{label}/{kind}/{did}/")
            if not dry_run:
                if dest.exists():
                    raise SystemExit(f"Refusing to overwrite existing {dest}")
                shutil.move(str(src), str(dest))

    return log


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Archive output, movie-images, and audio for one expedition quarter."
    )
    parser.add_argument("year", type=int, help="Expedition year, e.g. 1803")
    parser.add_argument(
        "quarter", type=int, choices=(1, 2, 3, 4), help="Calendar quarter 1–4 (Q1=Jan–Mar, …)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print moves only")
    args = parser.parse_args()
    for line in move_quarter(args.year, args.quarter, dry_run=args.dry_run):
        print(line)


if __name__ == "__main__":
    main()
