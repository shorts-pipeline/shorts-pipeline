#!/usr/bin/env python3
"""
Narration generation: redirects to two-phase narration (v2).

The pipeline uses generate-narration-two-phase.py only. This script exists so that
invocations like `python generate-narration.py 18030830` still work and run the
two-phase generator. For prompt reference of the legacy single-phase (v1) logic,
see docs/generate-narration-v1.md.
"""

import subprocess
import sys
from pathlib import Path


def main():
    repo = Path(__file__).resolve().parent
    two_phase = repo / "generate-narration-two-phase.py"
    argv = list(sys.argv[1:])
    if not argv:
        print(
            "Usage: generate-narration.py <date_id> [--model MODEL] [--config CONFIG] [--no-focus-topic] ...",
            file=sys.stderr,
        )
        print(
            "Runs two-phase narration. See docs/generate-narration-v1.md for legacy v1 prompt reference.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Normalize date: YYYY-MM-DD -> YYYYMMDD for two-phase
    if len(argv[0]) == 10 and argv[0][4] == "-" and argv[0][7] == "-":
        argv[0] = argv[0].replace("-", "")
    # Drop --vendor openai (v1 CLI); two-phase does not use it
    filtered = []
    i = 0
    while i < len(argv):
        if argv[i] == "--vendor" and i + 1 < len(argv) and argv[i + 1] == "openai":
            i += 2
            continue
        filtered.append(argv[i])
        i += 1
    cmd = [sys.executable, str(two_phase)] + filtered
    sys.exit(subprocess.run(cmd, cwd=str(repo)).returncode)


if __name__ == "__main__":
    main()
