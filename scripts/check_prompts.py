#!/usr/bin/env python3
"""
Quick QA checks for generated video prompts.

Run from the repo root:

  python scripts/check_prompts.py            # scan all narrations/narration*.json
  python scripts/check_prompts.py 18031223   # scan a single date_id

Checks for:
- Gear acting alone: phrases like "pair of boots ... trudging".
- Double periods after the Subject prefix (e.g. "Subject: X—... ..").
- Over-repeated "Newfoundland dog".
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from video_vendors import build_prompts

RE_BOOTS_TRUDGING = re.compile(r"\bpair of boots\b.*\btrudg\w*", re.IGNORECASE | re.DOTALL)
RE_DOUBLE_PERIOD_AFTER_SUBJECT = re.compile(r"Subject:[^.\n]+?\.\s*\.", re.IGNORECASE)
RE_NEWFOUNDLAND_REPEATED = re.compile(
    r"(Newfoundland dog).*?(Newfoundland dog)", re.IGNORECASE | re.DOTALL
)


def iter_dates_to_check(args: list[str]) -> Iterable[str]:
    """Yield date_ids (YYYYMMDD) to check based on CLI args."""
    narrations_dir = Path("narrations")
    if args:
        yield from args
        return
    # No args: scan all narration*.json
    if not narrations_dir.exists():
        return
    for p in sorted(narrations_dir.glob("narration*.json")):
        stem = p.stem  # e.g. narration18031223
        if stem.startswith("narration") and stem[len("narration") :].isdigit():
            yield stem[len("narration") :]


def check_prompt(date_id: str, prompt: str, idx: int) -> list[str]:
    """Return a list of issue strings for this prompt."""
    issues: list[str] = []
    # 1) Gear acting alone (boots trudging)
    if RE_BOOTS_TRUDGING.search(prompt):
        issues.append("gear_acting_alone: 'pair of boots' + 'trudging'")
    # 2) Double period after Subject prefix (e.g. Subject: X—... ..)
    #    We allow one period after the subject description; this catches ".." style glitches.
    if RE_DOUBLE_PERIOD_AFTER_SUBJECT.search(prompt):
        issues.append("double_period_after_subject")
    # 3) Over-repeated 'Newfoundland dog'
    if RE_NEWFOUNDLAND_REPEATED.search(prompt):
        issues.append("repeated_newfoundland_dog")
    return issues


def main(argv: list[str]) -> None:
    any_issues = False
    for date_id in iter_dates_to_check(argv):
        try:
            prompts = build_prompts(date_id)
        except FileNotFoundError:
            print(f"[WARN] narration file for {date_id} not found; skipping", file=sys.stderr)
            continue
        for i, prompt in enumerate(prompts, start=1):
            # Ignore vendor-specific markers (e.g. FAL image reference hints)
            prompt = re.sub(r"^\\s*FAL_IMAGE_CHAR=[a-zA-Z0-9_-]+\\s+", "", prompt)
            issues = check_prompt(date_id, prompt, i)
            if issues:
                any_issues = True
                print(f"[ISSUE] {date_id} segment {i}: {', '.join(issues)}")
    if not any_issues:
        print("[OK] No prompt issues found.")


if __name__ == "__main__":
    main(sys.argv[1:])
