#!/usr/bin/env python3
"""
Run two-phase narration for a generic plain-text source using config/profiles/generic_plain.json.

Example:
  python run-episode.py 20260101 path/to/article.txt
  python run-episode.py 20260101 path/to/article.txt --model gpt-4o-mini

Then run narration-to-mp3, narration-to-video, and videos-mp3-to-movie with the same
eight-digit id. Use videos-mp3-to-movie.py --output-prefix episode (default for that profile).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate narration JSON from a UTF-8 text file (generic_plain profile).",
    )
    parser.add_argument(
        "episode_id",
        help="Eight-digit episode id (audio/movie-images/narrations paths use this id).",
    )
    parser.add_argument("source_text", type=Path, help="Path to UTF-8 source .txt (or similar)")
    parser.add_argument("--model", default="gpt-4o", help="OpenAI model for two-phase generation")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Forward to generate-narration-two-phase.py (Phase 1 only, no writes).",
    )
    args = parser.parse_args()
    eid = args.episode_id.strip()
    if len(eid) != 8 or not eid.isdigit():
        print("[ERROR] episode_id must be eight digits.", file=sys.stderr)
        raise SystemExit(2)
    root = Path(__file__).resolve().parent
    prof = root / "config" / "profiles" / "generic_plain.json"
    src = args.source_text.expanduser()
    if not src.is_file():
        print(f"[ERROR] Source file not found: {src}", file=sys.stderr)
        raise SystemExit(2)
    cmd = [
        sys.executable,
        str(root / "generate-narration-two-phase.py"),
        eid,
        "--profile",
        str(prof),
        "--source-text-file",
        str(src),
        "--model",
        args.model,
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    raise SystemExit(subprocess.call(cmd, cwd=str(root)))


if __name__ == "__main__":
    main()
