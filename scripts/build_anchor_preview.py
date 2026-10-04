#!/usr/bin/env python3
"""Build scene anchors and/or anchor+TTS preview video for one episode date_id."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pipeline.anchor_preview import (  # noqa: E402
    assemble_anchor_preview_video,
    assemble_anchor_preview_with_missing_anchors,
    build_scene_anchors_batch,
    check_tts_prerequisites,
    render_anchor_preview_clips,
)


def _parse_segment_indices(raw: str | None) -> set[int] | None:
    if not raw or not str(raw).strip():
        return None
    out: set[int] = set()
    for part in str(raw).split(","):
        part = part.strip()
        if part:
            out.add(int(part))
    return out or None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Batch scene-anchor i2i and/or assemble cheap preview MP4 "
            "(stills + TTS) before OmniHuman."
        )
    )
    parser.add_argument("date_id", help="Eight-digit date id, e.g. 18030830")
    parser.add_argument(
        "--anchors-only",
        action="store_true",
        help="Only run FAL scene-anchor i2i for eligible segments.",
    )
    parser.add_argument(
        "--assemble-only",
        action="store_true",
        help=(
            "Render anchor_preview clips and mux; also builds missing scene anchors "
            "(use --force-anchors to regen all)."
        ),
    )
    parser.add_argument(
        "--force-anchors",
        action="store_true",
        help="Regenerate anchors even when PNG already exists.",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Alias for --force-anchors on anchor build.",
    )
    parser.add_argument(
        "--segments",
        type=str,
        default=None,
        metavar="N,M",
        help="1-based segment indices for anchor build only.",
    )
    parser.add_argument(
        "--aspect-ratio",
        choices=("9:16", "16:9"),
        default="9:16",
        help="Scene-anchor i2i aspect (default 9:16 Shorts).",
    )
    parser.add_argument(
        "--wide-screen",
        action="store_true",
        help="Preview clips and assembly in 16:9 instead of Shorts.",
    )
    parser.add_argument(
        "--ambient",
        action="store_true",
        help="Pass --ambient to videos-mp3-to-movie for preview assembly.",
    )
    args = parser.parse_args()
    repo = _REPO
    did = args.date_id.strip()
    seg_set = _parse_segment_indices(args.segments)
    force = bool(args.force_anchors or args.no_skip_existing)
    shorts = not args.wide_screen

    do_anchors = not args.assemble_only
    do_assemble = not args.anchors_only

    if do_assemble:
        ok, err = check_tts_prerequisites(repo, did)
        if not ok:
            print(f"[ERROR] {err}", file=sys.stderr)
            sys.exit(1)

    if do_anchors:
        print(f"Building scene anchors for {did}…")
        report = build_scene_anchors_batch(
            did,
            repo_root=repo,
            skip_existing=not force,
            force=force,
            segment_indices=seg_set,
            aspect_ratio=args.aspect_ratio,
        )
        for row in report.anchor_build:
            print(f"  seg {row.segment_index}: {row.action} {row.message or row.relative_path}")
        print(f"Eligible: {report.eligible_count}")

    if do_assemble:
        if do_anchors:
            print(f"Rendering preview clips for {did}…")
            render_anchor_preview_clips(did, repo_root=repo, shorts=shorts)
            print("Assembling preview video…")
            out = assemble_anchor_preview_video(
                did, repo_root=repo, shorts=shorts, ambient=args.ambient
            )
        else:
            print(f"Building missing anchors (if any), preview clips, and assembly for {did}…")
            _, out = assemble_anchor_preview_with_missing_anchors(
                did,
                repo_root=repo,
                skip_existing=not force,
                force=force,
                shorts=shorts,
                ambient=args.ambient,
                aspect_ratio=args.aspect_ratio,
            )
        print(f"[OK] {out}")


if __name__ == "__main__":
    main()
