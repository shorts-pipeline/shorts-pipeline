#!/usr/bin/env python3
"""
Prompt pack tooling: fingerprints for reproducibility and segment prompt export for A/B review.

Examples (from repo root):
  python scripts/prompt_ab_harness.py fingerprint
  python scripts/prompt_ab_harness.py fingerprint --pack lewis_clark_dialogue
  python scripts/prompt_ab_harness.py compare-packs lewis_clark lewis_clark_dialogue
  python scripts/prompt_ab_harness.py export-segments --date-id 18040507 --segments 3,4

For video A/B (same audio, different FAL talking-head model), run twice:
  set FAL_TALKING_HEAD_MODEL=fal-ai/sadtalker && python narration-to-video.py 18040507 --vendor fal --segments 3
  (move outputs aside) then set a different model / fallback in config/narration_config.json and repeat.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from pipeline.narration_utils import load_narration
from pipeline.prompt_pack_metadata import (
    fingerprint_prompt_pack,
    read_pack_manifest,
)
from pipeline.prompt_pack_paths import packs_root


def _cmd_fingerprint(args: argparse.Namespace) -> int:
    packs = (
        [args.pack]
        if args.pack
        else sorted(p.name for p in packs_root(_repo).iterdir() if p.is_dir())
    )
    rows = []
    for pid in packs:
        rows.append(
            {
                "pack_id": pid,
                "manifest": read_pack_manifest(_repo, pid),
                "fingerprint_sha256": fingerprint_prompt_pack(_repo, pid),
            }
        )
    print(json.dumps({"packs": rows}, indent=2, ensure_ascii=False))
    return 0


def _cmd_compare_packs(args: argparse.Namespace) -> int:
    a, b = args.pack_a, args.pack_b
    fa = fingerprint_prompt_pack(_repo, a)
    fb = fingerprint_prompt_pack(_repo, b)
    print(
        json.dumps(
            {
                "pack_a": a,
                "pack_b": b,
                "fingerprint_a": fa,
                "fingerprint_b": fb,
                "identical": fa == fb,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_export_segments(args: argparse.Namespace) -> int:
    date_id = args.date_id.strip()
    narr = load_narration(date_id, narrations_dir=_repo / "narrations")
    if not narr:
        print(f"[ERROR] No narration for date_id {date_id}", file=sys.stderr)
        return 1
    script = narr.get("narration_script") or []
    want = {int(x.strip()) for x in args.segments.split(",") if x.strip()}
    out_rows = []
    for row in script:
        if not isinstance(row, dict):
            continue
        idx = int(row.get("segment_index") or 0)
        if idx not in want:
            continue
        out_rows.append(
            {
                "segment_index": idx,
                "visual_mode": row.get("visual_mode"),
                "talking_head_subject": row.get("talking_head_subject"),
                "talking_head_prompt": row.get("talking_head_prompt"),
                "video_prompt": row.get("video_prompt"),
            }
        )
    print(json.dumps({"date_id": date_id, "segments": out_rows}, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Prompt pack fingerprint + segment export (A/B helpers)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("fingerprint", help="Print manifest + SHA-256 fingerprint for pack(s).")
    s1.add_argument(
        "--pack", default="", help="Single pack id; default = all directories under prompt_packs/"
    )
    s1.set_defaults(func=_cmd_fingerprint)

    s2 = sub.add_parser("compare-packs", help="Compare fingerprints of two packs.")
    s2.add_argument("pack_a")
    s2.add_argument("pack_b")
    s2.set_defaults(func=_cmd_compare_packs)

    s3 = sub.add_parser("export-segments", help="Dump merged prompts for 1-based segment indices.")
    s3.add_argument("--date-id", required=True, help="YYYYMMDD")
    s3.add_argument("--segments", required=True, help="Comma-separated 1-based indices, e.g. 3,7")
    s3.set_defaults(func=_cmd_export_segments)

    args = p.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
