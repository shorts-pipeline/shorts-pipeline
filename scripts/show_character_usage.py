#!/usr/bin/env python3
"""Print persistent character usage counts (from pipeline/character_usage.json). Run from repo root."""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.narration_characters import load_character_usage, load_characters


def main() -> None:
    data = load_character_usage()
    totals = data.get("totals") or {}
    last = data.get("last_updated_date_id") or "—"
    chars = {c.id: c.name for c in load_characters()}
    if not totals:
        print(
            "No character usage recorded yet. Run generate-narration on a date to populate pipeline/character_usage.json."
        )
        return
    print("Character usage (segment injections across all narrations)")
    print("Last updated:", last)
    print()
    for cid, count in sorted(totals.items(), key=lambda x: -x[1]):
        name = chars.get(cid, cid)
        print(f"  {name}: {count}")
    print()
    print("Total injections:", sum(totals.values()))


if __name__ == "__main__":
    main()
