#!/usr/bin/env python3
"""
Build side-by-side composite portraits (same height, gap, RGBA). Replaces per-pair scripts.

  .venv\\Scripts\\python.exe scripts/build_portrait.py --preset lewis_clark

Presets: lewis_clark, lewis_drouillard, lewis_reuben_fields, lewis_seaman, clark_ordway, clark_york, clark_colter

clark_colter: uses character-portraits/colter.pair_nogun.png when present (see portrait_prompts.json),
else falls back to colter.png|jpg|jpeg.

Custom pair (no preset):

  scripts/build_portrait.py --left character-portraits/a.png --right character-portraits/b.png -o character-portraits/out.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pipeline.portrait_composite import (  # noqa: E402
    PORTRAITS_DIR,
    build_side_by_side_composite,
    resolve_portrait_stem,
)

_COLTER_NOGUN = PORTRAITS_DIR / "colter.pair_nogun.png"

# left/right = stem names under character-portraits/ (no extension); out = output filename in that folder
_PRESETS: dict[str, dict[str, str | Path]] = {
    "lewis_clark": {"left": "lewis", "right": "clark", "out": "lewis_clark.png"},
    "lewis_drouillard": {"left": "lewis", "right": "drouillard", "out": "lewis_drouillard.png"},
    "lewis_reuben_fields": {
        "left": "lewis",
        "right": "reuben_fields",
        "out": "lewis_reuben_fields.png",
    },
    "lewis_seaman": {"left": "lewis", "right": "seaman", "out": "lewis_seaman.png"},
    "clark_ordway": {"left": "clark", "right": "ordway", "out": "clark_ordway.png"},
    "clark_york": {"left": "clark", "right": "york", "out": "clark_york.png"},
    "clark_colter": {"left": "clark", "right": "colter", "out": "clark_colter.png"},
}


def _resolve_right_for_preset(preset: str, right_override: Path | None) -> Path:
    if right_override is not None:
        return right_override
    if preset == "clark_colter" and _COLTER_NOGUN.is_file():
        return _COLTER_NOGUN
    spec = _PRESETS[preset]
    stem = str(spec["right"])
    return resolve_portrait_stem(PORTRAITS_DIR / stem)


def main() -> None:
    ap = argparse.ArgumentParser(description="Combine two portrait images side by side")
    ap.add_argument(
        "--preset",
        choices=sorted(_PRESETS.keys()),
        help="Named left/right/output under character-portraits/",
    )
    ap.add_argument("--left", type=Path, default=None, help="Left image (required if no --preset)")
    ap.add_argument(
        "--right", type=Path, default=None, help="Right image (required if no --preset)"
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output PNG (default: character-portraits/<preset>.png)",
    )
    ap.add_argument("--gap", type=int, default=8)
    args = ap.parse_args()

    if args.preset:
        spec = _PRESETS[args.preset]
        left_p = args.left or resolve_portrait_stem(PORTRAITS_DIR / str(spec["left"]))
        right_p = _resolve_right_for_preset(args.preset, args.right)
        out_p = args.output or (PORTRAITS_DIR / str(spec["out"]))
    else:
        if not args.left or not args.right:
            ap.error("Without --preset, both --left and --right are required")
        left_p = args.left
        right_p = args.right
        out_p = args.output
        if out_p is None:
            ap.error("Without --preset, -o/--output is required")

    if not left_p.is_file():
        raise SystemExit(f"Missing {left_p}")
    if not right_p.is_file():
        msg = f"Missing {right_p}"
        if args.preset == "clark_colter":
            msg += (
                f". For combo without Colter's rifle, generate {_COLTER_NOGUN.name} via "
                "fal_portrait_refine.py (see portrait_prompts.json)."
            )
        raise SystemExit(msg)

    build_side_by_side_composite(left_p, right_p, out_p, gap_px=args.gap)
    if args.preset:
        print(f"Wrote {out_p} (right: {right_p.name})")
    else:
        print(f"Wrote {out_p}")


if __name__ == "__main__":
    main()
