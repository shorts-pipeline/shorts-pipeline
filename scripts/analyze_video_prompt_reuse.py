#!/usr/bin/env python3
"""
Analyze video_prompt repetition and visual_style distribution across narrations.

Reads all narration*.json (excludes *.meta.json), normalizes video_prompts,
reports exact and near-duplicate counts, and empirical theme distribution
plus P(same theme on next episode).

Usage:
  python scripts/analyze_video_prompt_reuse.py [--narrations-dir narrations] [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


def _normalize_prompt(text: str) -> str:
    """Strip optional 'Subject: Name—' prefix, collapse whitespace, trim."""
    if not text or not isinstance(text, str):
        return ""
    t = text.strip()
    # Strip leading "Subject: ...—" (em dash or hyphen)
    m = re.match(r"^Subject:\s*[^—\-]+[—\-]\s*", t, re.IGNORECASE)
    if m:
        t = t[m.end() :].strip()
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _token_set(text: str) -> set[str]:
    """Lowercase word tokens for Jaccard."""
    return set(re.findall(r"[a-zA-Z]+", text.lower()))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_narrations(narrations_dir: Path):
    """Yield (date_id, visual_style_name, segments) per narration file."""
    for p in sorted(narrations_dir.glob("narration*.json")):
        if ".meta." in p.name:
            continue
        m = re.match(r"narration(\d{8})\.json", p.name)
        date_id = m.group(1) if m else ""
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        style = data.get("visual_style") or {}
        style_name = (style.get("name") or "").strip() or "(none)"
        script = data.get("narration_script") or []
        segments = [seg.get("video_prompt") or "" for seg in script]
        yield date_id, style_name, segments


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze video_prompt repetition and visual_style distribution."
    )
    parser.add_argument(
        "--narrations-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "narrations",
        help="Directory containing narration*.json files",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional: write JSON report (duplicate groups, per-theme stats)",
    )
    parser.add_argument(
        "--near-threshold",
        type=float,
        default=0.8,
        help="Jaccard threshold for near-duplicate pairs (default 0.8)",
    )
    args = parser.parse_args()

    narrations_dir = args.narrations_dir
    if not narrations_dir.exists():
        print(f"[ERROR] Narrations dir not found: {narrations_dir}")
        return

    # Collect all prompts with metadata
    rows: list[tuple[str, str, int, str, str]] = []  # date_id, style_name, seg_idx, raw, normalized
    theme_counts: dict[str, int] = defaultdict(int)
    for date_id, style_name, segments in load_narrations(narrations_dir):
        theme_counts[style_name] += 1
        for i, raw in enumerate(segments):
            norm = _normalize_prompt(raw)
            rows.append((date_id, style_name, i, raw, norm))

    total_segments = len(rows)
    if total_segments == 0:
        print("No segments found in narrations.")
        return

    # Exact repetition (normalized)
    norm_to_occurrences: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for date_id, _style_name, seg_idx, _raw, norm in rows:
        if norm:
            norm_to_occurrences[norm].append((date_id, seg_idx))

    unique_prompts = len(norm_to_occurrences)
    duplicate_groups = [occ for occ in norm_to_occurrences.values() if len(occ) > 1]
    num_duplicate_groups = len(duplicate_groups)
    segments_with_duplicate = sum(len(occ) for occ in duplicate_groups)
    pct_with_duplicate = 100.0 * segments_with_duplicate / total_segments if total_segments else 0

    # Near-duplication: pairs with Jaccard >= threshold (among normalized prompts)
    norm_list = list(norm_to_occurrences.keys())
    near_pairs: list[tuple[str, str, float]] = []
    for i in range(len(norm_list)):
        for j in range(i + 1, len(norm_list)):
            a, b = norm_list[i], norm_list[j]
            if a == b:
                continue
            sim = jaccard(_token_set(a), _token_set(b))
            if sim >= args.near_threshold:
                near_pairs.append((a[:80], b[:80], round(sim, 3)))

    # Theme distribution and P(same theme as previous)
    total_episodes = sum(theme_counts.values())
    p_same_theme_empirical = 0.0
    if total_episodes > 0:
        for count in theme_counts.values():
            p_same_theme_empirical += (count / total_episodes) ** 2

    # --- Report ---
    print("=== Video prompt reusability report ===\n")
    print("Repetition (normalized prompts):")
    print(f"  Total segments:     {total_segments}")
    print(f"  Unique prompts:    {unique_prompts}")
    print(f"  Duplicate groups:  {num_duplicate_groups}")
    print(f"  Segments in a dup:  {segments_with_duplicate} ({pct_with_duplicate:.1f}%)")
    print(f"  Empirical reuse rate (exact): {pct_with_duplicate / 100:.3f}")
    print()
    print(f"Near-duplicates (Jaccard >= {args.near_threshold}): {len(near_pairs)} pairs")
    if near_pairs and len(near_pairs) <= 5:
        for a, b, sim in near_pairs[:5]:
            print(f"  sim={sim}: {a!r} ... vs ... {b!r}")
    elif near_pairs:
        print(f"  (First 3: {near_pairs[:3]})")
    print()
    print("Visual theme distribution (per episode):")
    for name in sorted(theme_counts.keys()):
        c = theme_counts[name]
        pct = 100.0 * c / total_episodes if total_episodes else 0
        print(f"  {c:3d}  {pct:5.1f}%  {name}")
    print()
    print("Probability same visual theme on next episode:")
    n_themes = len(theme_counts)
    p_theoretical = 1.0 / n_themes if n_themes else 0
    print(f"  Theoretical (random 1/N): 1/{n_themes} = {p_theoretical:.3f}")
    print(f"  Empirical P(same as prev): {p_same_theme_empirical:.3f}")
    print()
    print("Note: P(exact prompt reuse) for new segments is not predictable; use the")
    print("  empirical duplicate rate above as reuse potential. For higher reuse,")
    print("  consider prompt pools or a video cache keyed by normalized prompt hash.")

    # Optional JSON output
    if args.out is not None:
        out_data = {
            "total_segments": total_segments,
            "unique_prompts": unique_prompts,
            "duplicate_groups_count": num_duplicate_groups,
            "segments_with_duplicate": segments_with_duplicate,
            "pct_with_duplicate": round(pct_with_duplicate, 2),
            "near_duplicate_pairs_count": len(near_pairs),
            "near_threshold": args.near_threshold,
            "theme_distribution": dict(theme_counts),
            "p_same_theme_empirical": round(p_same_theme_empirical, 4),
            "duplicate_groups": [
                {
                    "normalized_preview": norm[:200],
                    "count": len(occ),
                    "occurrences": [{"date_id": d, "segment_index": i} for d, i in occ],
                }
                for norm, occ in [
                    (n, norm_to_occurrences[n])
                    for n in norm_to_occurrences
                    if len(norm_to_occurrences[n]) > 1
                ]
            ],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote report to {args.out}")


if __name__ == "__main__":
    main()
