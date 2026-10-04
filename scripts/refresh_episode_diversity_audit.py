#!/usr/bin/env python3
"""
Refresh cached LLM episode-diversity audit for Lewis & Clark prompt packs.

Summarizes the last N merged narrations, calls OpenAI once, writes
``state/episode_diversity_lewis_clark.json`` (shared by all lewis_clark* packs).

Usage:
  python scripts/refresh_episode_diversity_audit.py --last 15 --prompt-pack lewis_clark
  python scripts/refresh_episode_diversity_audit.py --through 18040531 --dry-run
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

from pipeline.episode_diversity_audit import refresh_diversity_audit


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh LLM episode diversity audit cache.")
    ap.add_argument("--prompt-pack", default="lewis_clark", help="Lewis & Clark prompt pack id")
    ap.add_argument("--narrations-dir", type=Path, default=_REPO / "narrations")
    ap.add_argument(
        "--last", type=int, default=0, help="Sample size (overrides pack config when > 0)"
    )
    ap.add_argument(
        "--through",
        metavar="DATE_ID",
        help="Include episodes up to this date_id (default: newest on disk)",
    )
    ap.add_argument(
        "--before", metavar="DATE_ID", help="Exclude this date_id and later (preview for a run)"
    )
    ap.add_argument("--model", default="", help="OpenAI model (default: pack llm_audit.model)")
    ap.add_argument("--dry-run", action="store_true", help="Print audit JSON; do not write cache")
    args = ap.parse_args()

    try:
        result = refresh_diversity_audit(
            _REPO,
            args.narrations_dir,
            args.prompt_pack,
            last=int(args.last or 0),
            through=args.through,
            before=args.before,
            model=(args.model or "").strip() or None,
            dry_run=bool(args.dry_run),
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return (
            2 if "only to lewis_clark" in str(exc) or "resolve episode_diversity" in str(exc) else 1
        )

    print(
        f"Auditing {result['episode_count']} episode(s); "
        f"newest={result.get('through_date_id') or '?'} model={result.get('model')}",
        file=sys.stderr,
    )
    added = result.get("hints_added") or []
    if added:
        print("\nAdded hints:", file=sys.stderr)
        for h in added:
            print(f"  + {h}", file=sys.stderr)
    removed = result.get("hints_removed") or []
    if removed:
        print("Removed hints:", file=sys.stderr)
        for h in removed:
            print(f"  - {h}", file=sys.stderr)

    uncaught = result.get("unrenderable_language_uncaught") or []
    if uncaught:
        print(
            "\nUnrenderable-language findings NOT yet handled by "
            "pipeline/video_prompt_language_stripper.py:",
            file=sys.stderr,
        )
        for f in uncaught:
            loc = f"{f.get('date_id') or '?'}#{f.get('segment_index')}"
            print(
                f'  [{f.get("category") or "?"}] {loc}: "{f.get("excerpt") or ""}" '
                f"-> try: {f.get('suggested_pattern') or '(none suggested)'}",
                file=sys.stderr,
            )

    if args.dry_run:
        print(json.dumps(result.get("payload") or {}, indent=2, ensure_ascii=False))
        return 0

    print(f"Wrote {result.get('cache_path')}", file=sys.stderr)
    if result.get("notes"):
        print(f"Notes: {result['notes']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
