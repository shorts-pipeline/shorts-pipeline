#!/usr/bin/env python3
"""
Create or refresh a multi-day narrative arc plan (the LLM picks the arc length).

Plans are written to ``state/week_arcs/week_<start_date_id>.json``.
Anchor and length bounds: ``config/week_arc.json`` (default first day 18040705).

Usage:
  python scripts/plan_week_arc.py 18040705
  python scripts/plan_week_arc.py 18040709 --refresh
  python scripts/plan_week_arc.py 18040705 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pipeline.week_arc import (
    ensure_week_arc,
    load_week_arc_config,
    week_arc_path,
    week_journal_dates_for,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Plan a 7-journal-day week arc.")
    ap.add_argument("date_id", help="Any date_id in the bundle (YYYYMMDD), e.g. 18040705")
    ap.add_argument("--journal-dir", type=Path, default=_REPO / "journal-entries")
    ap.add_argument("--narrations-dir", type=Path, default=_REPO / "narrations")
    ap.add_argument(
        "--model", default="", help="OpenAI model (default: config/week_arc.json plan_model)"
    )
    ap.add_argument(
        "--refresh", action="store_true", help="Regenerate even when cached plan exists"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="Print placeholder plan JSON; no API call or write"
    )
    args = ap.parse_args()

    date_id = args.date_id.strip()
    if len(date_id) != 8 or not date_id.isdigit():
        print("date_id must be 8 digits (YYYYMMDD)", file=sys.stderr)
        return 2

    cfg = load_week_arc_config(_REPO)
    week_dates = week_journal_dates_for(date_id, args.journal_dir, repo_root=_REPO, config=cfg)
    if not week_dates:
        print(
            f"No arc window available for {date_id} (anchor {cfg.get('first_anchor_date_id')}). "
            "It may already belong to a different saved arc, or isn't the next date awaiting a plan.",
            file=sys.stderr,
        )
        return 1

    try:
        doc = ensure_week_arc(
            date_id,
            repo_root=_REPO,
            journal_dir=args.journal_dir,
            narrations_dir=args.narrations_dir,
            model=(args.model or "").strip() or None,
            refresh=bool(args.refresh),
            dry_run_plan=bool(args.dry_run),
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not doc:
        print("No week arc produced.", file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0

    out = week_arc_path(_REPO, doc["week_start_date_id"])
    print(f"Wrote {out}", file=sys.stderr)
    print(f"Week: {doc.get('week_start_date_id')}–{doc.get('week_end_date_id')}", file=sys.stderr)
    print(f"Through-line: {doc.get('through_line')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
