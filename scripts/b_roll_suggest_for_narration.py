#!/usr/bin/env python3
"""
For a narration date_id, rank b_roll_library manifest clips against each segment's
video_prompt (and optional bucket / environment heuristics). Prints top N candidates
per segment.

Enrichment: clips with source.date_id + source.segment_index load that segment's
video_prompt from narrations for similarity; tags always contribute to the score.

Usage:
  python scripts/b_roll_suggest_for_narration.py 18040507
  python scripts/b_roll_suggest_for_narration.py 18040507 --top 5 --json out.json
  python scripts/b_roll_suggest_for_narration.py 18040507 --min-base-similarity 0.12

Clips with score_breakdown base_similarity below --min-base-similarity (default 0.08)
are dropped before taking top N; use 0 to disable the floor.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Repo root = parent of scripts/
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pipeline.narration_utils import load_narration  # noqa: E402

# Minimum Jaccard-heavy base_similarity (see _score_pair) before a clip can appear in top-N.
DEFAULT_MIN_BASE_SIMILARITY = 0.08


def _clamp_unit_interval(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


_STOPWORDS = frozenset(
    """
    a an the and or but if in on at to for of as is are was were be been being
    with from by it its this that these those into over under upon through
    about than then so not no yes very more most some any all each both few
    such same other another one two first second their they them his her she
    he him we you our your my me i
    """.split()
)


def _normalize_for_tokens(text: str) -> str:
    if not text or not isinstance(text, str):
        return ""
    t = text.lower()
    t = re.sub(r"[^a-z0-9\s]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _token_set(text: str) -> set[str]:
    return {
        w for w in _normalize_for_tokens(text).split() if w and w not in _STOPWORDS and len(w) > 1
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _load_buckets(path: Path) -> dict[str, list[str]]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        b = raw.get("buckets") if isinstance(raw, dict) else None
        if not isinstance(b, dict):
            return {}
        return {str(k): list(v) if isinstance(v, list) else [] for k, v in b.items()}
    except (json.JSONDecodeError, OSError):
        return {}


def _match_bucket(norm_prompt: str, buckets: dict[str, list[str]]) -> str:
    low = norm_prompt.lower()
    for name, keys in buckets.items():
        for k in keys:
            if k.lower() in low:
                return name
    return ""


def _get_segment_fields(data: dict, seg_idx_1based: int) -> tuple[str, bool, dict | None]:
    """Return (video_prompt, has_reference_character, raw segment dict)."""
    script = data.get("narration_script") or []
    if not isinstance(script, list):
        return "", False, None
    for j, seg in enumerate(script):
        if not isinstance(seg, dict):
            continue
        raw_idx = seg.get("segment_index")
        if raw_idx is None:
            i = j + 1
        else:
            try:
                i = int(raw_idx)
            except (TypeError, ValueError):
                i = j + 1
        if i != seg_idx_1based:
            continue
        vp = seg.get("video_prompt") or ""
        if not isinstance(vp, str):
            vp = str(vp)
        ref = bool(seg.get("reference_character_id"))
        return vp, ref, seg
    return "", False, None


def _manifest_path(library_root: Path) -> Path:
    return library_root / "manifest.json"


@dataclass
class ClipCandidate:
    clip_id: str
    file_rel: str
    tags: list[str]
    source: dict[str, Any]
    source_prompt: str
    source_has_ref: bool | None
    file_exists: bool
    # Optional repo-relative MP4 for thumbnails when canonical source path is missing.
    thumbnail_movie_rel: str | None = None


def _build_candidates(
    library_root: Path,
    narrations_dir: Path,
    buckets: dict[str, list[str]],
) -> list[ClipCandidate]:
    mp = _manifest_path(library_root)
    if not mp.is_file():
        return []
    try:
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    clips = manifest.get("clips") or []
    if not isinstance(clips, list):
        return []
    out: list[ClipCandidate] = []
    for c in clips:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip() or "(no id)"
        frel = str(c.get("file") or "").strip()
        tags = c.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        tags = [str(t).strip().lower() for t in tags if str(t).strip()]
        src = c.get("source") if isinstance(c.get("source"), dict) else {}
        prompt = ""
        has_ref: bool | None = None
        s_date = src.get("date_id")
        s_seg = src.get("segment_index")
        if s_date and s_seg is not None:
            try:
                sdi = str(s_date).strip()
                ssi = int(s_seg)
            except (TypeError, ValueError):
                sdi, ssi = "", 0
            else:
                narr = load_narration(sdi, narrations_dir)
                if narr:
                    vp, ref, _ = _get_segment_fields(narr, ssi)
                    prompt = vp
                    has_ref = ref
        abs_file = library_root / frel if frel else None
        exists = bool(abs_file and abs_file.is_file())
        thumb_override = c.get("thumbnail_movie_rel") or c.get("preview_movie_rel")
        thumb_override_s = (
            str(thumb_override).strip().replace("\\", "/").lstrip("/") if thumb_override else None
        )
        if thumb_override_s == "":
            thumb_override_s = None
        out.append(
            ClipCandidate(
                clip_id=cid,
                file_rel=frel,
                tags=tags,
                source=dict(src),
                source_prompt=prompt,
                source_has_ref=has_ref,
                file_exists=exists,
                thumbnail_movie_rel=thumb_override_s,
            )
        )
    return out


def _score_pair(
    seg_tokens: set[str],
    seg_norm: str,
    seg_bucket: str,
    seg_has_ref: bool,
    cand: ClipCandidate,
    buckets: dict[str, list[str]],
    prefer_environment: bool,
) -> tuple[float, dict[str, Any]]:
    """Return (score, debug breakdown)."""
    tag_tokens = _token_set(" ".join(cand.tags))
    cand_text = cand.source_prompt if cand.source_prompt.strip() else " ".join(cand.tags)
    cand_tokens = _token_set(cand_text)
    if cand.source_prompt.strip():
        j_prompt = _jaccard(seg_tokens, cand_tokens)
        j_tags = _jaccard(seg_tokens, tag_tokens)
        base = 0.65 * j_prompt + 0.35 * j_tags
    else:
        base = _jaccard(seg_tokens, tag_tokens.union(cand_tokens))

    cand_bucket = _match_bucket(_normalize_for_tokens(cand_text), buckets)
    bucket_bonus = 0.12 if seg_bucket and cand_bucket and seg_bucket == cand_bucket else 0.0

    env_bonus = 0.0
    if cand.source_has_ref is False:
        env_bonus = 0.08
    elif cand.source_has_ref is True:
        env_bonus = -0.03

    breakdown: dict[str, Any] = {
        "base_similarity": round(base, 4),
        "bucket_bonus": bucket_bonus,
        "env_bonus": env_bonus,
        "seg_bucket": seg_bucket,
        "cand_bucket": cand_bucket,
    }

    score = base + bucket_bonus + env_bonus

    if prefer_environment and seg_has_ref is False:
        if cand.source_has_ref is True:
            score -= 0.15
        breakdown["prefer_environment_penalty"] = -0.15 if cand.source_has_ref is True else 0.0

    return score, breakdown


def _repo_rel_if_existing_file(repo_root: Path, rel: str) -> str | None:
    """If rel is a path under repo_root and the file exists, return normalized repo-relative path."""
    if not rel:
        return None
    norm = rel.replace("\\", "/").strip().lstrip("/")
    if ".." in norm or "//" in norm:
        return None
    root = repo_root.resolve()
    p = (root / norm).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    if p.is_file():
        return str(p.relative_to(root)).replace("\\", "/")
    return None


def resolve_candidate_video_rel(
    cand: ClipCandidate, library_root: Path, repo_root: Path
) -> str | None:
    """
    Canonical B-roll source: library clip file, else movie-images/<source.date_id>/<NN>.mp4.
    Used for copy-from links (no episode fallback).
    """
    root = repo_root.resolve()
    if cand.file_rel:
        lib = (library_root / cand.file_rel).resolve()
        try:
            lib.relative_to(root)
        except ValueError:
            pass
        else:
            if lib.is_file():
                return str(lib.relative_to(root)).replace("\\", "/")
    s_date = str(cand.source.get("date_id") or "").strip()
    s_seg = cand.source.get("segment_index")
    if not s_date or s_seg is None:
        return None
    try:
        ssi = int(s_seg)
    except (TypeError, ValueError):
        return None
    return _repo_rel_if_existing_file(repo_root, f"movie-images/{s_date}/{ssi:02d}.mp4")


def resolve_thumbnail_video_rel(
    cand: ClipCandidate,
    library_root: Path,
    repo_root: Path,
    episode_date_id: str,
) -> tuple[str | None, bool]:
    """
    Path for UI thumbnail ffmpeg. Tries: manifest thumbnail_movie_rel, canonical source,
    then movie-images/<episode_date_id>/<source_segment>.mp4 (same NN as library source).

    Returns (relative_path_or_none, used_episode_fallback).
    """
    if cand.thumbnail_movie_rel:
        hit = _repo_rel_if_existing_file(repo_root, cand.thumbnail_movie_rel)
        if hit:
            return hit, False
    canonical = resolve_candidate_video_rel(cand, library_root, repo_root)
    if canonical:
        return canonical, False
    s_seg = cand.source.get("segment_index")
    if s_seg is None or not re.fullmatch(r"\d{8}", episode_date_id.strip()):
        return None, False
    try:
        ssi = int(s_seg)
    except (TypeError, ValueError):
        return None, False
    fb = _repo_rel_if_existing_file(
        repo_root, f"movie-images/{episode_date_id.strip()}/{ssi:02d}.mp4"
    )
    if fb:
        return fb, True
    return None, False


def _compute_segments_payload(
    date_id: str,
    data: dict[str, Any],
    candidates: list[ClipCandidate],
    buckets: dict[str, list[str]],
    lib_root: Path,
    root: Path,
    top: int,
    prefer_environment: bool,
    min_base_similarity: float,
) -> list[dict[str, Any]]:
    segments_out: list[dict[str, Any]] = []
    script = data.get("narration_script") or []
    if not isinstance(script, list):
        return segments_out

    for j, seg in enumerate(script):
        if not isinstance(seg, dict):
            continue
        raw_idx = seg.get("segment_index")
        if raw_idx is None:
            seg_i = j + 1
        else:
            try:
                seg_i = int(raw_idx)
            except (TypeError, ValueError):
                seg_i = j + 1

        vp_raw = seg.get("video_prompt") or ""
        if not isinstance(vp_raw, str):
            vp_raw = str(vp_raw)
        nar_raw = seg.get("narration") or ""
        if not isinstance(nar_raw, str):
            nar_raw = str(nar_raw)
        # Similarity scoring: use video_prompt when present, else narration (legacy behavior).
        vp_for_score = vp_raw.strip() if vp_raw.strip() else nar_raw.strip()
        seg_has_ref = bool(seg.get("reference_character_id"))

        seg_tokens = _token_set(vp_for_score)
        seg_norm = _normalize_for_tokens(vp_for_score)
        seg_bucket = _match_bucket(seg_norm, buckets)

        ranked: list[tuple[float, ClipCandidate, dict[str, Any]]] = []
        for cand in candidates:
            s_date = str(cand.source.get("date_id") or "").strip()
            try:
                s_seg = int(cand.source["segment_index"])
            except (KeyError, TypeError, ValueError):
                s_seg = None
            if s_date == date_id and s_seg == seg_i:
                continue

            score, breakdown = _score_pair(
                seg_tokens,
                seg_norm,
                seg_bucket,
                seg_has_ref,
                cand,
                buckets,
                prefer_environment,
            )
            ranked.append((score, cand, breakdown))

        ranked.sort(key=lambda x: (-x[0], x[1].clip_id))
        if min_base_similarity > 0.0:
            floor = float(min_base_similarity)
            ranked = [t for t in ranked if float(t[2].get("base_similarity") or 0.0) >= floor]
        top_n = ranked[: max(0, top)]

        nar_full = nar_raw.strip()
        nar_preview = (nar_full[:200] + "…") if len(nar_full) > 200 else nar_full
        vp_display = vp_raw.strip()
        vp_preview = (vp_display[:240] + "…") if len(vp_display) > 240 else vp_display

        target_rel = f"movie-images/{date_id}/{seg_i:02d}.mp4"
        from pipeline.narration_visual_mode import normalize_visual_mode

        row: dict[str, Any] = {
            "segment_index": seg_i,
            "visual_mode": normalize_visual_mode(seg),
            "segment_has_reference_character": seg_has_ref,
            "segment_bucket": seg_bucket,
            "narration_full": nar_full,
            "narration_preview": nar_preview,
            "video_prompt_preview": vp_preview,
            "video_prompt_full": vp_display,
            "target_movie_rel": target_rel,
            "candidates": [],
        }
        for rank, (score, cand, breakdown) in enumerate(top_n, start=1):
            source_rel = resolve_candidate_video_rel(cand, lib_root, root)
            thumb_rel, thumb_ep_fallback = resolve_thumbnail_video_rel(
                cand, lib_root, root, date_id
            )
            row["candidates"].append(
                {
                    "rank": rank,
                    "score": round(score, 4),
                    "clip_id": cand.clip_id,
                    "file": cand.file_rel,
                    "file_exists": cand.file_exists,
                    "tags": cand.tags,
                    "source": cand.source,
                    "source_has_reference_character": cand.source_has_ref,
                    "score_breakdown": breakdown,
                    "thumb_video_rel": thumb_rel,
                    "source_movie_rel": source_rel,
                    "thumbnail_episode_fallback": thumb_ep_fallback,
                }
            )
        segments_out.append(row)
    return segments_out


def build_b_roll_suggest_payload(
    date_id: str,
    *,
    repo_root: Path | None = None,
    library_root: Path | None = None,
    narrations_dir: Path | None = None,
    buckets_path: Path | None = None,
    top: int = 5,
    prefer_environment: bool = False,
    min_base_similarity: float = DEFAULT_MIN_BASE_SIMILARITY,
) -> dict[str, Any]:
    """
    Build JSON-serializable suggest result for UI/API. On failure returns {"ok": False, ...}.

    min_base_similarity: drop clips whose score_breakdown base_similarity is below this
    (0.0 disables). Clamped to [0, 1].
    """
    root = (repo_root or _REPO).resolve()
    lib_root = (library_root or root / "b_roll_library").resolve()
    narr_dir = (narrations_dir or root / "narrations").resolve()
    bpath = buckets_path or (lib_root / "bucket_keywords.json")
    buckets = _load_buckets(bpath)

    if not re.fullmatch(r"\d{8}", date_id.strip()):
        return {"ok": False, "error": "invalid_date_id", "message": "date_id must be 8 digits"}
    date_id = date_id.strip()
    min_base = _clamp_unit_interval(min_base_similarity)

    data = load_narration(date_id, narr_dir)
    if not data:
        return {
            "ok": False,
            "error": "no_narration",
            "message": f"Missing {narr_dir / f'narration{date_id}.json'}",
        }

    candidates = _build_candidates(lib_root, narr_dir, buckets)
    if not candidates:
        return {
            "ok": False,
            "error": "no_manifest_clips",
            "message": f"No clips in {lib_root / 'manifest.json'} (or manifest missing).",
        }

    segments = _compute_segments_payload(
        date_id,
        data,
        candidates,
        buckets,
        lib_root,
        root,
        top,
        prefer_environment,
        min_base,
    )
    if not segments:
        return {
            "ok": False,
            "error": "no_segments",
            "message": "narration_script is missing or empty.",
        }

    y, m, d = date_id[:4], date_id[4:6], date_id[6:8]
    journal_date = f"{y}-{m}-{d}"
    return {
        "ok": True,
        "date_id": date_id,
        "journal_date": journal_date,
        "library_root": str(lib_root),
        "buckets_file": str(bpath),
        "prefer_environment": prefer_environment,
        "min_base_similarity": min_base,
        "segments": segments,
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description="Suggest top b_roll_library clips per narration segment."
    )
    p.add_argument("date_id", help="Eight-digit narration id, e.g. 18040507")
    p.add_argument(
        "--library-root",
        type=Path,
        default=_REPO / "b_roll_library",
        help="Folder containing manifest.json",
    )
    p.add_argument(
        "--narrations-dir",
        type=Path,
        default=_REPO / "narrations",
        help="Merged narration JSON directory",
    )
    p.add_argument(
        "--buckets",
        type=Path,
        default=None,
        help="bucket_keywords.json (default: <library-root>/bucket_keywords.json)",
    )
    p.add_argument("--top", type=int, default=5, help="Candidates per segment")
    p.add_argument(
        "--min-base-similarity",
        type=float,
        default=DEFAULT_MIN_BASE_SIMILARITY,
        metavar="X",
        help=(
            "Minimum score_breakdown base_similarity (Jaccard-heavy) to include a clip "
            f"(default {DEFAULT_MIN_BASE_SIMILARITY}; clamped to 0..1). Use 0 to disable."
        ),
    )
    p.add_argument(
        "--prefer-environment",
        action="store_true",
        help="When segment has no reference_character_id, down-rank clips sourced from portrait segments",
    )
    p.add_argument("--json", type=Path, default=None, help="Write full results JSON")
    args = p.parse_args()

    date_id = args.date_id.strip()
    if not re.fullmatch(r"\d{8}", date_id):
        print(f"[ERROR] date_id must be 8 digits, got {date_id!r}", file=sys.stderr)
        sys.exit(1)

    library_root = args.library_root.resolve()
    narrations_dir = args.narrations_dir.resolve()
    buckets_path = args.buckets or (library_root / "bucket_keywords.json")
    min_base = _clamp_unit_interval(args.min_base_similarity)

    payload = build_b_roll_suggest_payload(
        date_id,
        repo_root=_REPO,
        library_root=library_root,
        narrations_dir=narrations_dir,
        buckets_path=buckets_path,
        top=args.top,
        prefer_environment=args.prefer_environment,
        min_base_similarity=min_base,
    )
    if not payload.get("ok"):
        print(f"[ERROR] {payload.get('message') or payload.get('error')}", file=sys.stderr)
        sys.exit(1)

    segments_out = payload["segments"]
    for row in segments_out:
        seg_i = row["segment_index"]
        seg_bucket = row.get("segment_bucket") or ""
        seg_has_ref = row.get("segment_has_reference_character")
        prev = row["video_prompt_preview"]
        prev_short = prev if len(prev) <= 120 else prev[:120] + "…"
        print(f"\n=== Segment {seg_i} (bucket={seg_bucket or '—'}) ===")
        print(f"ref_char: {seg_has_ref}  |  {prev_short}")
        for item in row["candidates"]:
            ex = "ok" if item["file_exists"] else "missing"
            print(
                f"  {item['rank']:2}. score={item['score']:.3f} [{ex}] {item['clip_id']}"
                f"  tags={item['tags']}"
            )

    if args.json:
        out_payload = {
            "date_id": payload["date_id"],
            "library_root": payload["library_root"],
            "buckets_file": payload["buckets_file"],
            "prefer_environment": payload["prefer_environment"],
            "segments": segments_out,
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(out_payload, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
