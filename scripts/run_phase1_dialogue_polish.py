#!/usr/bin/env python3
"""Re-run phase1-dialogue polish on an existing voice sidecar and sync merged narration."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from pipeline.narration_common import load_narration_config
from pipeline.narration_phase1_dialogue import run_phase1_dialogue
from pipeline.prompt_pack_metadata import build_prompt_pack_lineage


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run phase1-dialogue polish for a date_id")
    parser.add_argument("date_id", help="Episode date id, e.g. 18040616")
    parser.add_argument("--model", default="gpt-4o")
    parser.add_argument("--pack", default="lewis_clark_long_conversation")
    parser.add_argument("--repo-root", type=Path, default=_repo_root)
    args = parser.parse_args()

    repo = args.repo_root
    date_id = args.date_id.strip()
    pack = args.pack.strip()
    config = load_narration_config(repo / "config" / "narration_config.json")
    convo_mode = config.get("dialogue_conversation_mode") or {}

    voice_path = repo / "narrations" / f"narration{date_id}_voice.json"
    merged_path = repo / "narrations" / f"narration{date_id}.json"
    if not voice_path.is_file():
        print(f"[ERROR] Missing voice sidecar: {voice_path}", file=sys.stderr)
        sys.exit(1)
    if not merged_path.is_file():
        print(f"[ERROR] Missing merged narration: {merged_path}", file=sys.stderr)
        sys.exit(1)

    voice_doc = json.loads(voice_path.read_text(encoding="utf-8"))
    phase1 = {
        "title": voice_doc.get("title"),
        "episode_metadata": voice_doc.get("episode_metadata"),
        "narrative_spine": voice_doc.get("narrative_spine"),
        "conversation_micro_arc": voice_doc.get("conversation_micro_arc"),
        "closing_type": voice_doc.get("closing_type"),
        "tone_register": voice_doc.get("tone_register"),
        "segments": voice_doc.get("segments") or [],
    }

    print("[phase1-dialogue] Polishing spoken dialogue lines...", file=sys.stderr)
    polished = run_phase1_dialogue(
        phase1,
        date_id,
        model=args.model,
        repo_root=repo,
        prompt_pack=pack,
        conversation_mode=convo_mode,
    )
    print("[phase1-dialogue] OK", file=sys.stderr)

    voice_doc["segments"] = polished["segments"]
    voice_path.write_text(
        json.dumps(voice_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"[OK] Updated {voice_path}", file=sys.stderr)

    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    script = merged.get("narration_script") or []
    for i, seg in enumerate(polished.get("segments") or []):
        if i >= len(script):
            break
        pd = seg.get("dialogue") or []
        md = script[i].get("dialogue") or []
        if not pd and not md:
            continue
        if len(pd) != len(md):
            print(
                f"[WARN] segment {i + 1} dialogue row count mismatch; skipping sync",
                file=sys.stderr,
            )
            continue
        for j, line in enumerate(pd):
            if isinstance(md[j], dict) and isinstance(line, dict):
                md[j]["text"] = line.get("text", md[j].get("text"))

    lineage = build_prompt_pack_lineage(
        repo,
        phase1_pack=pack,
        phase2_pack=pack,
        phase1_dialogue_pack=pack,
    )
    lineage["generated_at_utc"] = datetime.now(UTC).isoformat()
    merged["prompt_pack_lineage"] = lineage
    merged_path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"[OK] Updated {merged_path}", file=sys.stderr)

    print("\n--- Polished dialogue (cast segments) ---")
    for i, seg in enumerate(polished.get("segments") or [], start=1):
        rows = seg.get("dialogue") or []
        if not rows:
            continue
        sp = rows[0].get("speaker_id")
        print(f"  seg {i} ({sp}): {rows[0].get('text')}")


if __name__ == "__main__":
    main()
