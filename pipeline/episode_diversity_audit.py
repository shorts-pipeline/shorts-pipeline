"""
Offline LLM audit of recent Lewis & Clark narrations → cached diversity hints.

Refresh via ``scripts/refresh_episode_diversity_audit.py`` or Pipeline UI
``POST /api/library/diversity/refresh``. Runtime reads cache only (no per-run LLM).
See ``docs/AGENTS-pipeline.md`` (episode diversity).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.narration_common import clean_json_reply
from pipeline.prompt_pack_paths import packs_root
from pipeline.recent_episode_diversity import (
    _collect_cast,
    _script_edge_segments,
    _segment_narration_video_blob,
    _snapshot_from_narration,
    _text_matches_any,
    is_lewis_clark_prompt_pack,
    resolve_episode_diversity_config,
)

_RE_NARRATION = re.compile(r"^narration(\d{8})\.json$")
_MAX_HINT_CHARS = 400
_MAX_PATTERNS = 4
_VALID_CATEGORIES = frozenset(
    {"open_bookend", "close_bookend", "character_cast", "visual_beat", "story_arc", "tone_lens"}
)
_VALID_CHECK_TYPES = frozenset({"first_segment_regex", "last_segment_regex", "meta_flag"})
_VALID_UNRENDERABLE_CATEGORIES = frozenset({"sound", "invisible_state"})
_MAX_UNRENDERABLE_FINDINGS = 8
_MAX_EXCERPT_CHARS = 200
_MAX_SUGGESTED_PATTERN_CHARS = 120
_VALID_META_FLAGS = frozenset({"open_wake_morning", "close_evening_camp", "hunt_haul_heavy"})
_CATEGORY_TO_STATIC_RULE = {
    "open_bookend": "open_wake_morning_bookend",
    "close_bookend": "close_evening_camp_bookend",
}
_STATIC_HINT_STOPWORDS = frozenset(
    {
        "when",
        "the",
        "today",
        "journal",
        "allows",
        "especially",
        "recent",
        "episodes",
        "prior",
        "supports",
        "support",
        "reflect",
        "unique",
        "different",
        "consider",
        "varying",
        "explore",
        "encourage",
        "inclusion",
        "entries",
        "circumstances",
        "activities",
        "scenarios",
        "moods",
        "perhaps",
        "range",
        "wider",
        "provide",
        "enrich",
        "narrative",
        "perspectives",
        "interactions",
        "indicates",
        "allowed",
        "soft",
        "obey",
        "facts",
        "pack",
        "rules",
        "that",
        "this",
        "with",
        "from",
        "into",
        "have",
        "been",
        "their",
        "they",
        "them",
        "only",
        "more",
        "than",
        "another",
        "other",
        "where",
        "while",
        "using",
        "used",
        "many",
        "most",
        "structure",
    }
)
_CONCRETE_ALTERNATIVE_MARKERS_RE = re.compile(
    r"\b(in medias res|talking head|b_roll|video_prompt|unresolved|river hazard|"
    r"wildlife encounter|trade dispute|council|shields|drouillard|gass|ordway|"
    r"track/sign|sign-reading|chopping wood|camp setup)\b",
    re.IGNORECASE,
)


def normalize_checker_pattern(pat: str) -> str:
    """Fix LLM double-escaped regex (literal ``\\b``) and plain substring patterns."""
    p = str(pat or "").strip()
    if not p:
        return p
    # JSON often yields literal backslash-backslash-b instead of regex word boundary.
    p = re.sub(r"\\{2,}b", r"\\b", p, flags=re.IGNORECASE)
    try:
        re.compile(p)
    except re.error:
        p = re.escape(p)
    return p


def _truncate_words(text: str, max_words: int = 25) -> str:
    words = (text or "").split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]) + "…"


def summarize_episode_for_diversity_audit(date_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Compact episode summary for LLM audit (~200–400 tokens each)."""
    meta = _snapshot_from_narration(date_id, data)
    script = data.get("narration_script")
    if not isinstance(script, list):
        script = []
    em = data.get("episode_metadata") if isinstance(data.get("episode_metadata"), dict) else {}
    segment_outline: list[dict[str, str]] = []
    for seg in script:
        if not isinstance(seg, dict):
            continue
        narr = _truncate_words(str(seg.get("narration") or ""), 25)
        segment_outline.append(
            {
                "segment_type": str(seg.get("segment_type") or ""),
                "visual_mode": str(seg.get("visual_mode") or "b_roll"),
                "reference_character_id": str(seg.get("reference_character_id") or ""),
                "narration_preview": narr,
                # Full (untruncated) text: this is the exact silent-clip prompt sent to the video
                # generator, so the unrenderable-language scan needs it verbatim, not a preview.
                "video_prompt": str(seg.get("video_prompt") or "").strip(),
            }
        )
    first, last = _script_edge_segments(script)
    open_blob = _segment_narration_video_blob(first) if first else ""
    close_blob = _segment_narration_video_blob(last) if last else ""
    from pipeline.conversation_dyad import extract_conversation_dyad, format_dyad_label

    dyad = extract_conversation_dyad(data)
    return {
        "date_id": date_id,
        "title": str(data.get("title") or "").strip(),
        "primary_lens": str(em.get("primary_lens") or meta.get("primary_lens") or ""),
        "tone_register": str(data.get("tone_register") or meta.get("tone_register") or ""),
        "dialogue_mode": bool(data.get("dialogue_mode")),
        "long_conversation_mode": bool(data.get("long_conversation_mode")),
        "conversation_dyad": format_dyad_label(dyad) if dyad else "",
        "cast": sorted(_collect_cast(script)),
        "flags": {
            "open_wake_morning": bool(meta.get("open_wake_morning")),
            "close_evening_camp": bool(meta.get("close_evening_camp")),
            "seaman_label": str(meta.get("seaman_label") or "no"),
            "hunt_haul_heavy": bool(meta.get("hunt_haul_heavy")),
            "talking_head_segments": int(meta.get("talking_head_segments") or 0),
        },
        "segment_outline": segment_outline,
        "open_snippet": _truncate_words(open_blob, 40),
        "close_snippet": _truncate_words(close_blob, 40),
    }


def load_merged_narrations_for_audit(
    narrations_dir: Path | str,
    *,
    before_date_id: str | None = None,
    limit: int = 15,
) -> list[tuple[str, dict[str, Any]]]:
    """Newest-first merged narration files, optionally strictly before ``before_date_id``."""
    narrations_dir = Path(narrations_dir)
    if not narrations_dir.is_dir() or limit <= 0:
        return []
    rows: list[tuple[str, Path]] = []
    for p in narrations_dir.iterdir():
        if not p.is_file():
            continue
        m = _RE_NARRATION.match(p.name)
        if not m:
            continue
        did = m.group(1)
        if before_date_id and did >= before_date_id:
            continue
        rows.append((did, p))
    rows.sort(key=lambda t: t[0], reverse=True)
    out: list[tuple[str, dict[str, Any]]] = []
    for did, path in rows[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            out.append((did, data))
    return out


def build_episode_summaries_for_audit(
    narrations_dir: Path | str,
    *,
    before_date_id: str | None = None,
    limit: int = 15,
) -> list[dict[str, Any]]:
    return [
        summarize_episode_for_diversity_audit(did, data)
        for did, data in load_merged_narrations_for_audit(
            narrations_dir, before_date_id=before_date_id, limit=limit
        )
    ]


def fingerprint_summaries(summaries: list[dict[str, Any]]) -> str:
    payload = json.dumps(summaries, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit_cache_basename(prompt_pack: str) -> str:
    """All ``lewis_clark*`` packs share one cache file."""
    pid = (prompt_pack or "").strip().lower()
    if pid == "lewis_clark" or pid.startswith("lewis_clark_"):
        return "episode_diversity_lewis_clark.json"
    return f"episode_diversity_{pid}.json"


def audit_cache_path(repo_root: Path | str, prompt_pack: str) -> Path:
    return Path(repo_root) / "state" / audit_cache_basename(prompt_pack)


def _normalize_cache_checkers(cache: dict[str, Any]) -> None:
    checkers = cache.get("recommended_checkers")
    if not isinstance(checkers, list):
        return
    for row in checkers:
        if not isinstance(row, dict):
            continue
        pats = row.get("patterns")
        if isinstance(pats, list):
            row["patterns"] = [normalize_checker_pattern(str(p)) for p in pats if str(p).strip()]


def load_audit_cache(repo_root: Path | str, prompt_pack: str) -> dict[str, Any] | None:
    path = audit_cache_path(repo_root, prompt_pack)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if isinstance(data, dict):
        _normalize_cache_checkers(data)
    return data if isinstance(data, dict) else None


def save_audit_cache(repo_root: Path | str, prompt_pack: str, payload: dict[str, Any]) -> Path:
    path = audit_cache_path(repo_root, prompt_pack)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def parse_llm_audit_config(episode_diversity: dict[str, Any] | None) -> dict[str, Any]:
    ediv = episode_diversity if isinstance(episode_diversity, dict) else {}
    la = ediv.get("llm_audit")
    if not isinstance(la, dict):
        la = {}
    return {
        "enabled": bool(la.get("enabled", False)),
        "sample_size": max(5, int(la.get("sample_size", 15) or 15)),
        "refresh_every_new_episodes": max(1, int(la.get("refresh_every_new_episodes", 5) or 5)),
        "max_dynamic_hints": max(1, int(la.get("max_dynamic_hints", 4) or 4)),
        "max_total_hints": max(1, int(la.get("max_total_hints", 6) or 6)),
        "model": str(la.get("model") or "gpt-4o-mini").strip() or "gpt-4o-mini",
    }


def _validate_unrenderable_findings(raw: Any) -> list[dict[str, Any]]:
    """Normalize the optional ``unrenderable_language_findings`` array (never raises)."""
    findings_in = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    for row in findings_in[:_MAX_UNRENDERABLE_FINDINGS]:
        if not isinstance(row, dict):
            continue
        excerpt = str(row.get("excerpt") or "").strip()
        if not excerpt:
            continue
        if len(excerpt) > _MAX_EXCERPT_CHARS:
            excerpt = excerpt[: _MAX_EXCERPT_CHARS - 1] + "…"
        cat = str(row.get("category") or "").strip().lower()
        if cat not in _VALID_UNRENDERABLE_CATEGORIES:
            cat = "sound"
        pattern = str(row.get("suggested_pattern") or "").strip()
        if len(pattern) > _MAX_SUGGESTED_PATTERN_CHARS:
            pattern = pattern[: _MAX_SUGGESTED_PATTERN_CHARS - 1] + "…"
        seg_idx_raw = row.get("segment_index")
        seg_idx = int(seg_idx_raw) if isinstance(seg_idx_raw, (int, float)) else None
        out.append(
            {
                "date_id": str(row.get("date_id") or "").strip(),
                "segment_index": seg_idx,
                "category": cat,
                "excerpt": excerpt,
                "suggested_pattern": pattern,
            }
        )
    return out


def annotate_unrenderable_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark each finding ``already_handled`` by running its excerpt through the current stripper.

    ``already_handled=True`` means ``strip_unrenderable_language``
    (``pipeline/video_prompt_language_stripper.py``) already changes this exact excerpt today —
    i.e. its curated pattern lists already catch this phrasing. ``False`` is the actionable
    signal: phrasing the audit found that the stripper does not yet touch, and a maintainer
    should consider adding ``suggested_pattern`` (or similar) to those lists. See
    ai-plans/fal-video-quality-followups-2026-09.md item 6.
    """
    from pipeline.video_prompt_language_stripper import strip_unrenderable_language

    out: list[dict[str, Any]] = []
    for row in findings:
        excerpt = str(row.get("excerpt") or "")
        cleaned = strip_unrenderable_language(excerpt) if excerpt else excerpt
        out.append({**row, "already_handled": bool(excerpt) and cleaned != excerpt})
    return out


def validate_audit_response(raw: Any) -> dict[str, Any]:
    """Normalize and validate LLM JSON; raises ValueError on bad shape."""
    if not isinstance(raw, dict):
        raise ValueError("audit response must be a JSON object")
    patterns_in = raw.get("patterns")
    if not isinstance(patterns_in, list):
        raise ValueError("patterns must be an array")
    patterns: list[dict[str, Any]] = []
    for i, row in enumerate(patterns_in[:_MAX_PATTERNS]):
        if not isinstance(row, dict):
            raise ValueError(f"patterns[{i}] must be an object")
        pid = str(row.get("id") or f"pattern_{i}").strip()
        cat = str(row.get("category") or "").strip().lower()
        if cat and cat not in _VALID_CATEGORIES:
            raise ValueError(f"patterns[{i}].category invalid: {cat!r}")
        hint = str(row.get("suggested_hint") or "").strip()
        if not hint:
            raise ValueError(f"patterns[{i}].suggested_hint is required")
        if len(hint) > _MAX_HINT_CHARS:
            hint = hint[: _MAX_HINT_CHARS - 1] + "…"
        evidence = row.get("evidence")
        if not isinstance(evidence, list):
            evidence = []
        patterns.append(
            {
                "id": pid,
                "category": cat or "story_arc",
                "prevalence": str(row.get("prevalence") or "").strip(),
                "evidence": [str(x).strip() for x in evidence[:5] if str(x).strip()],
                "suggested_hint": hint,
            }
        )
    checkers_in = raw.get("recommended_checkers")
    checkers: list[dict[str, Any]] = []
    if isinstance(checkers_in, list):
        for i, row in enumerate(checkers_in[:_MAX_PATTERNS]):
            if not isinstance(row, dict):
                continue
            ctype = str(row.get("check_type") or "").strip().lower()
            if ctype not in _VALID_CHECK_TYPES:
                continue
            meta_flag = str(row.get("meta_flag") or "").strip()
            if ctype == "meta_flag":
                if meta_flag not in _VALID_META_FLAGS:
                    continue
            elif meta_flag:
                meta_flag = ""
            pats = row.get("patterns")
            if ctype != "meta_flag" and (not isinstance(pats, list) or not pats):
                continue
            checkers.append(
                {
                    "id": str(
                        row.get("id") or patterns[i]["id"] if i < len(patterns) else f"checker_{i}"
                    ),
                    "check_type": ctype,
                    "patterns": [normalize_checker_pattern(str(p)) for p in pats if str(p).strip()]
                    if isinstance(pats, list)
                    else [],
                    "meta_flag": meta_flag,
                    "skew_threshold": float(row.get("skew_threshold", 0.5) or 0.5),
                    "status": "proposed",
                }
            )
    notes = str(raw.get("notes") or "").strip()
    unrenderable = _validate_unrenderable_findings(raw.get("unrenderable_language_findings"))
    return {
        "patterns": patterns,
        "recommended_checkers": checkers,
        "notes": notes,
        "unrenderable_language_findings": unrenderable,
    }


def load_diversity_audit_system_prompt(repo_root: Path | str, prompt_pack: str) -> str:
    root = Path(repo_root)
    pack = (prompt_pack or "lewis_clark").strip()
    for candidate in (
        packs_root(root) / pack / "diversity_audit_system.txt",
        packs_root(root) / "lewis_clark" / "diversity_audit_system.txt",
    ):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Missing diversity_audit_system.txt for pack {pack!r}")


def build_audit_user_prompt(summaries: list[dict[str, Any]]) -> str:
    return (
        "Analyze these recent expedition narration episodes (newest first). "
        "Return JSON only per the system schema.\n\n"
        + json.dumps({"episodes": summaries}, indent=2, ensure_ascii=False)
    )


def run_diversity_audit_llm(
    repo_root: Path | str,
    summaries: list[dict[str, Any]],
    prompt_pack: str,
    *,
    model: str = "gpt-4o-mini",
    client: Any = None,
) -> dict[str, Any]:
    if not summaries:
        raise ValueError("no episode summaries for audit")
    from openai import OpenAI

    from pipeline_logging import log_api_call_with_bodies

    system = load_diversity_audit_system_prompt(repo_root, prompt_pack)
    user = build_audit_user_prompt(summaries)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    oai = client or OpenAI()
    response = oai.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=2500,
    )
    raw = response.choices[0].message.content or ""
    log_api_call_with_bodies(
        "openai",
        "chat.completions.create",
        request_body=messages,
        response_body=raw,
        model=model,
        extra={"message_count": 2, "purpose": "episode_diversity_audit"},
    )
    parsed = json.loads(clean_json_reply(raw))
    return validate_audit_response(parsed)


_CHECKER_REGEN_SYSTEM = """You regenerate one episode-diversity checker for the Lewis & Clark narration pipeline.

Given a pattern (id, category, suggested_hint, evidence) and recent episode summaries, output a single recommended_checker that detects when the pattern still skews across episodes.

Rules:
- `id` must match the pattern id exactly.
- `check_type`: `first_segment_regex` | `last_segment_regex` | `meta_flag`
- For `meta_flag`, use only: `open_wake_morning` | `close_evening_camp` | `hunt_haul_heavy`
- `patterns`: regex strings matched against lowercased segment-1 or last-segment narration+video_prompt text
- `skew_threshold`: 0.0–1.0 (typically 0.5)
- `meta_flag` only when check_type is meta_flag; otherwise empty string

Return valid JSON only (no markdown fences):

{
  "id": "pattern_id",
  "check_type": "first_segment_regex",
  "patterns": ["\\\\bphrase\\\\b"],
  "meta_flag": "",
  "skew_threshold": 0.5
}
"""


def _validate_single_checker(raw: Any, pattern_id: str) -> dict[str, Any]:
    """Normalize and validate one checker object from LLM JSON."""
    if not isinstance(raw, dict):
        raise ValueError("checker must be a JSON object")
    pid = (pattern_id or "").strip()
    cid = str(raw.get("id") or pid).strip()
    if pid and cid != pid:
        raise ValueError(f"checker id must be {pid!r}, got {cid!r}")
    ctype = str(raw.get("check_type") or "").strip().lower()
    if ctype not in _VALID_CHECK_TYPES:
        raise ValueError(f"invalid check_type: {ctype!r}")
    meta_flag = str(raw.get("meta_flag") or "").strip()
    if ctype == "meta_flag":
        if meta_flag not in _VALID_META_FLAGS:
            raise ValueError(f"invalid meta_flag: {meta_flag!r}")
    elif meta_flag:
        meta_flag = ""
    pats = raw.get("patterns")
    if ctype != "meta_flag" and (not isinstance(pats, list) or not pats):
        raise ValueError("patterns required for regex check types")
    return {
        "id": cid or pid,
        "check_type": ctype,
        "patterns": [normalize_checker_pattern(str(p)) for p in (pats or []) if str(p).strip()]
        if isinstance(pats, list)
        else [],
        "meta_flag": meta_flag,
        "skew_threshold": float(raw.get("skew_threshold", 0.5) or 0.5),
        "status": "proposed",
    }


def build_checker_regeneration_user_prompt(
    pattern: dict[str, Any],
    summaries: list[dict[str, Any]],
    *,
    old_checker: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "pattern": pattern,
        "episodes": summaries,
    }
    if old_checker:
        payload["previous_checker"] = old_checker
        payload["instruction"] = (
            "The previous checker may have poor regex coverage or the wrong check_type. "
            "Return an improved checker for this pattern only."
        )
    else:
        payload["instruction"] = "Return a new checker for this pattern only."
    return json.dumps(payload, indent=2, ensure_ascii=False)


def run_checker_regeneration_llm(
    repo_root: Path | str,
    pattern: dict[str, Any],
    summaries: list[dict[str, Any]],
    prompt_pack: str,
    *,
    old_checker: dict[str, Any] | None = None,
    model: str = "gpt-4o-mini",
    client: Any = None,
) -> dict[str, Any]:
    """One OpenAI call to replace a single pattern's recommended_checker."""
    if not pattern or not summaries:
        raise ValueError("pattern and summaries required")
    from openai import OpenAI

    from pipeline_logging import log_api_call_with_bodies

    pattern_id = str(pattern.get("id") or "").strip()
    if not pattern_id:
        raise ValueError("pattern id required")
    user = build_checker_regeneration_user_prompt(pattern, summaries, old_checker=old_checker)
    messages = [
        {"role": "system", "content": _CHECKER_REGEN_SYSTEM},
        {"role": "user", "content": user},
    ]
    oai = client or OpenAI()
    response = oai.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
        max_tokens=800,
    )
    raw = response.choices[0].message.content or ""
    log_api_call_with_bodies(
        "openai",
        "chat.completions.create",
        request_body=messages,
        response_body=raw,
        model=model,
        extra={"message_count": 2, "purpose": "episode_diversity_checker_regen"},
    )
    parsed = json.loads(clean_json_reply(raw))
    return _validate_single_checker(parsed, pattern_id)


def refresh_diversity_audit(
    repo_root: Path | str,
    narrations_dir: Path | str,
    prompt_pack: str = "lewis_clark",
    *,
    last: int = 0,
    through: str | None = None,
    before: str | None = None,
    model: str | None = None,
    dry_run: bool = False,
    client: Any = None,
    narration_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run the full LLM episode-diversity audit and write ``state/episode_diversity_*.json``.

    Same work as ``scripts/refresh_episode_diversity_audit.py`` (used by Pipeline UI).
    """
    pack = (prompt_pack or "lewis_clark").strip()
    if not is_lewis_clark_prompt_pack(pack):
        raise ValueError(
            f"episode diversity audit applies only to lewis_clark* packs, not {pack!r}"
        )

    cfg = narration_config
    if cfg is None:
        from pipeline.narration_common import load_narration_config

        cfg = load_narration_config()
    ediv = resolve_episode_diversity_config(repo_root, pack, cfg if isinstance(cfg, dict) else {})
    if not ediv:
        raise ValueError("could not resolve episode_diversity config")
    llm_cfg = ediv.get("llm_audit") or {}
    sample = int(last or llm_cfg.get("sample_size", 15) or 15)
    use_model = (model or llm_cfg.get("model") or "gpt-4o-mini").strip()

    before_id = (before or "").strip() or None
    through_id = (through or "").strip() or None
    if through_id:
        rows = build_episode_summaries_for_audit(
            narrations_dir,
            before_date_id=before_id,
            limit=9999,
        )
        summaries = [s for s in rows if s["date_id"] <= through_id][:sample]
    else:
        summaries = build_episode_summaries_for_audit(
            narrations_dir,
            before_date_id=before_id,
            limit=sample,
        )
    if not summaries:
        raise ValueError("no merged narrations found for audit")

    prev = load_audit_cache(repo_root, pack)
    audit = run_diversity_audit_llm(repo_root, summaries, pack, model=use_model, client=client)
    payload = build_audit_cache_payload(
        prompt_pack=pack,
        summaries=summaries,
        audit=audit,
        model=use_model,
    )
    old_hints = list((prev or {}).get("hints") or [])
    new_hints = list(payload.get("hints") or [])
    added = [h for h in new_hints if h not in old_hints]
    removed = [h for h in old_hints if h not in new_hints]
    unrenderable_findings = list(payload.get("unrenderable_language_findings") or [])
    unrenderable_uncaught = [f for f in unrenderable_findings if not f.get("already_handled")]

    out_path = audit_cache_path(repo_root, pack)
    if not dry_run:
        save_audit_cache(repo_root, pack, payload)

    return {
        "ok": True,
        "prompt_pack": pack,
        "episode_count": len(summaries),
        "through_date_id": payload.get("through_date_id") or "",
        "cache_path": str(out_path),
        "model": use_model,
        "notes": payload.get("notes") or "",
        "hints": new_hints,
        "hints_added": added,
        "hints_removed": removed,
        "unrenderable_language_findings": unrenderable_findings,
        "unrenderable_language_uncaught": unrenderable_uncaught,
        "dry_run": bool(dry_run),
        "payload": payload,
    }


def regenerate_diversity_checker(
    repo_root: Path | str,
    narrations_dir: Path | str,
    prompt_pack: str,
    pattern_id: str,
    *,
    model: str | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """
    LLM-regenerate ``recommended_checkers`` entry for one cached pattern; writes cache file.
    """
    pid = (pattern_id or "").strip()
    if not pid:
        raise ValueError("pattern_id required")
    pack = (prompt_pack or "lewis_clark").strip()
    if not is_lewis_clark_prompt_pack(pack):
        raise ValueError(
            f"episode diversity audit applies only to lewis_clark* packs, not {pack!r}"
        )

    cache = load_audit_cache(repo_root, pack)
    if not cache:
        raise ValueError("no audit cache on disk")

    pattern: dict[str, Any] | None = None
    for row in cache.get("patterns") or []:
        if isinstance(row, dict) and str(row.get("id") or "") == pid:
            pattern = row
            break
    if not pattern:
        raise ValueError(f"pattern {pid!r} not found in cache")

    old_checker = _checker_for_pattern(pid, cache)
    limit = max(5, int(cache.get("episode_count") or 15))
    summaries = build_episode_summaries_for_audit(narrations_dir, limit=limit)
    if not summaries:
        raise ValueError("no merged narrations found for checker regeneration")

    use_model = (model or cache.get("model") or "gpt-4o-mini").strip()
    new_checker = run_checker_regeneration_llm(
        repo_root,
        pattern,
        summaries,
        pack,
        old_checker=old_checker,
        model=use_model,
        client=client,
    )

    checkers = list(cache.get("recommended_checkers") or [])
    replaced = False
    for i, row in enumerate(checkers):
        if isinstance(row, dict) and str(row.get("id") or "") == pid:
            checkers[i] = new_checker
            replaced = True
            break
    if not replaced:
        checkers.append(new_checker)
    cache["recommended_checkers"] = checkers
    save_audit_cache(repo_root, pack, cache)

    metas = [{"date_id": str(s.get("date_id") or "")} for s in summaries if s.get("date_id")]
    rate = checker_match_rate(new_checker, metas, narrations_dir) if metas else 0.0

    return {
        "ok": True,
        "pattern_id": pid,
        "prompt_pack": pack,
        "checker": new_checker,
        "previous_checker": old_checker,
        "match_rate": round(rate, 3),
        "model": use_model,
    }


def _checker_hits_episode(
    checker: dict[str, Any],
    date_id: str,
    data: dict[str, Any],
) -> bool:
    script = data.get("narration_script")
    if not isinstance(script, list):
        script = []
    meta = _snapshot_from_narration(date_id, data)
    ctype = str(checker.get("check_type") or "").strip().lower()
    if ctype == "meta_flag":
        flag = str(checker.get("meta_flag") or "").strip()
        return bool(meta.get(flag))
    first, last = _script_edge_segments(script)
    blob = ""
    if ctype == "first_segment_regex":
        blob = _segment_narration_video_blob(first) if first else ""
    elif ctype == "last_segment_regex":
        blob = _segment_narration_video_blob(last) if last else ""
    else:
        return False
    pats = tuple(
        normalize_checker_pattern(str(p)) for p in (checker.get("patterns") or []) if str(p).strip()
    )
    if not pats:
        return False
    return _text_matches_any(blob, pats)


def checker_match_rate(
    checker: dict[str, Any],
    metas: list[dict[str, Any]],
    narrations_dir: Path | str,
) -> float:
    if not metas:
        return 0.0
    narrations_dir = Path(narrations_dir)
    hits = 0
    for m in metas:
        did = str(m.get("date_id") or "")
        if not did:
            continue
        path = narrations_dir / f"narration{did}.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and _checker_hits_episode(checker, did, data):
            hits += 1
    return hits / len(metas)


def _checker_for_pattern(pattern_id: str, cache: dict[str, Any]) -> dict[str, Any] | None:
    for c in cache.get("recommended_checkers") or []:
        if isinstance(c, dict) and str(c.get("id") or "") == pattern_id:
            return c
    return None


def _static_rule_for_pattern(row: dict[str, Any]) -> str | None:
    """Map an LLM pattern to a deterministic rule id when they cover the same beat."""
    cat = str(row.get("category") or "").strip().lower()
    if cat in _CATEGORY_TO_STATIC_RULE:
        return _CATEGORY_TO_STATIC_RULE[cat]
    pid = str(row.get("id") or "").lower()
    if "hunt" in pid or "haul" in pid:
        return "hunt_haul_heavy"
    if "tone" in pid or "lens" in pid:
        return "primary_lens_skew"
    return None


def pattern_suppressed_by_static_rules(
    row: dict[str, Any],
    fired_static_rule_ids: set[str],
) -> bool:
    if not fired_static_rule_ids:
        return False
    cat = str(row.get("category") or "").strip().lower()
    mapped = _static_rule_for_pattern(row)
    if mapped and mapped in fired_static_rule_ids:
        return True
    if cat == "tone_lens" and ({"primary_lens_skew", "tone_register_skew"} & fired_static_rule_ids):
        return True
    return False


def _hint_keyword_set(text: str) -> set[str]:
    words = set(re.findall(r"[a-z]{4,}", (text or "").lower()))
    return words - _STATIC_HINT_STOPWORDS


def hint_has_concrete_alternative(hint: str) -> bool:
    """True when the hint names a specific alternative beat, not vague advice only."""
    text = (hint or "").strip()
    if not text:
        return False
    if _CONCRETE_ALTERNATIVE_MARKERS_RE.search(text):
        return True
    lower = text.lower()
    for sep in (";", "—", " - ", " instead of ", " rather than ", " explore closing on "):
        if sep in lower:
            idx = lower.index(sep)
            tail = text[idx + len(sep) :].strip()
            if len(_hint_keyword_set(tail)) >= 4:
                return True
    return False


_GENERIC_DYNAMIC_HINT_RE = re.compile(
    r"^\s*(consider|explore|encourage|diversify)\b",
    re.IGNORECASE,
)


def hint_redundant_with_static(dynamic_hint: str, static_hints: list[str]) -> bool:
    """Drop generic LLM hints that restate an already-active static bullet."""
    if not static_hints:
        return False
    if _GENERIC_DYNAMIC_HINT_RE.search(dynamic_hint) and not hint_has_concrete_alternative(
        dynamic_hint
    ):
        return True
    norm_dyn = re.sub(r"[*_]+", "", re.sub(r"\s+", " ", dynamic_hint.lower())).strip()
    for static in static_hints:
        norm_st = re.sub(r"[*_]+", "", re.sub(r"\s+", " ", static.lower())).strip()
        if len(norm_dyn) >= 40 and (norm_dyn in norm_st or norm_st in norm_dyn):
            return True
    dyn = _hint_keyword_set(dynamic_hint)
    if len(dyn) < 6:
        return False
    for static in static_hints:
        st = _hint_keyword_set(static)
        if not st:
            continue
        overlap = len(dyn & st)
        if overlap >= 9:
            return True
        if overlap / max(len(dyn), 1) >= 0.72:
            return True
    return False


def dynamic_hints_from_cache(
    cache: dict[str, Any] | None,
    metas: list[dict[str, Any]],
    narrations_dir: Path | str,
    *,
    max_dynamic_hints: int = 4,
    static_hints: list[str] | None = None,
    fired_static_rule_ids: set[str] | None = None,
) -> list[str]:
    """Return LLM suggested hints that still fire and do not duplicate static rules."""
    if not cache or not metas:
        return []
    patterns = cache.get("patterns")
    if not isinstance(patterns, list):
        return []
    static = static_hints or []
    fired = fired_static_rule_ids or set()
    out: list[str] = []
    for row in patterns:
        if not isinstance(row, dict):
            continue
        if pattern_suppressed_by_static_rules(row, fired):
            continue
        hint = str(row.get("suggested_hint") or "").strip()
        if not hint or hint_redundant_with_static(hint, static):
            continue
        pid = str(row.get("id") or "")
        checker = _checker_for_pattern(pid, cache) if pid else None
        if checker:
            threshold = float(checker.get("skew_threshold", 0.5) or 0.5)
            if checker_match_rate(checker, metas, narrations_dir) < threshold:
                continue
        out.append(hint)
        if len(out) >= max_dynamic_hints:
            break
    return out


def newest_merged_date_id(narrations_dir: Path | str) -> str | None:
    narrations_dir = Path(narrations_dir)
    best: str | None = None
    for p in narrations_dir.iterdir():
        m = _RE_NARRATION.match(p.name)
        if not m:
            continue
        did = m.group(1)
        if best is None or did > best:
            best = did
    return best


def is_audit_stale(
    cache: dict[str, Any] | None,
    narrations_dir: Path | str,
    refresh_every_new_episodes: int,
) -> bool:
    if not cache:
        return True
    through = str(cache.get("through_date_id") or "").strip()
    newest = newest_merged_date_id(narrations_dir)
    if not newest or not through:
        return True
    if newest <= through:
        return False
    # Count episodes between through (exclusive) and newest (inclusive)
    narrations_dir = Path(narrations_dir)
    gap = 0
    for p in narrations_dir.iterdir():
        m = _RE_NARRATION.match(p.name)
        if not m:
            continue
        did = m.group(1)
        if did > through and did <= newest:
            gap += 1
    return gap >= refresh_every_new_episodes


def dedupe_and_cap_hints(hints: list[str], max_total: int = 6) -> list[str]:
    seen_norm: set[str] = set()
    out: list[str] = []
    for h in hints:
        h = (h or "").strip()
        if not h:
            continue
        norm = re.sub(r"\s+", " ", h.lower())
        if norm in seen_norm:
            continue
        dominated = False
        for prev in seen_norm:
            if norm in prev or prev in norm:
                dominated = True
                break
        if dominated:
            continue
        seen_norm.add(norm)
        out.append(h)
        if len(out) >= max_total:
            break
    return out


def build_audit_cache_payload(
    *,
    prompt_pack: str,
    summaries: list[dict[str, Any]],
    audit: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    through = summaries[0]["date_id"] if summaries else ""
    hints = [p["suggested_hint"] for p in audit.get("patterns") or [] if isinstance(p, dict)]
    unrenderable = annotate_unrenderable_findings(audit.get("unrenderable_language_findings") or [])
    return {
        "prompt_pack": prompt_pack,
        "through_date_id": through,
        "episode_count": len(summaries),
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "model": model,
        "fingerprint": fingerprint_summaries(summaries),
        "patterns": audit.get("patterns") or [],
        "recommended_checkers": audit.get("recommended_checkers") or [],
        "notes": audit.get("notes") or "",
        "hints": hints,
        "unrenderable_language_findings": unrenderable,
    }


def llm_audit_status(
    repo_root: Path | str,
    prompt_pack: str,
    narrations_dir: Path | str,
    llm_config: dict[str, Any],
) -> dict[str, Any]:
    if not llm_config.get("enabled"):
        return {"enabled": False, "stale": False, "cache_present": False}
    cache = load_audit_cache(repo_root, prompt_pack)
    stale = is_audit_stale(
        cache,
        narrations_dir,
        int(llm_config.get("refresh_every_new_episodes", 5) or 5),
    )
    return {
        "enabled": True,
        "stale": stale,
        "cache_present": cache is not None,
        "generated_at": (cache or {}).get("generated_at"),
        "through_date_id": (cache or {}).get("through_date_id"),
        "notes": (cache or {}).get("notes") or "",
        "cached_hints": (cache or {}).get("hints") or [],
    }
