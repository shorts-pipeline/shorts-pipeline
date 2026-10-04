"""Phase 2 runtime hints for mid-episode maps based on prior narration outputs."""

from __future__ import annotations

import json
import os
import re
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from pipeline.narration_utils import get_mid_episode_map_insertion

# Narration filenames must match narration\d{8}\.json (see scripts/b_roll_mine_prompt_themes.py).
_CANONICAL_NARRATION_RE = re.compile(r"\Anarration(\d{8})\.json\Z")

MAP_DRY_SPELL_THRESHOLD_DEFAULT = 7

_TRAVEL_HINT_RE = re.compile(
    r"\b(?:"
    r"set\s+out|depart(?:ed|ure)?|embark(?:ed)?|marched|(?:break|broke)\s+camp|decamp(?:ed)?|"
    r"pushed\s+off|upstream|downstream|opposite\s+bank|crossed\s+the\s+river|"
    r"north\s+(?:bank|shore)|south\s+(?:bank|shore)|east\s+(?:bank|shore)|west\s+(?:bank|shore)|"
    r"\d+\s+miles?\b|navigat(?:e|ion|ing)|course\s+(?:was|to|along)|rowing\s+against"
    r")\b",
    re.I,
)


def runtime_map_policy_disabled() -> bool:
    return os.environ.get("LEWISCLARK_MAP_RUNTIME_POLICY", "").strip().lower() in (
        "0",
        "false",
        "off",
        "no",
    )


def map_dry_spell_threshold() -> int:
    raw = os.environ.get("LEWISCLARK_MAP_DRY_SPELL_THRESHOLD", "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return MAP_DRY_SPELL_THRESHOLD_DEFAULT


def _prior_canonical_date_ids_sorted_newest_first(
    narrations_dir: Path,
    before_date_id: str,
) -> list[str]:
    """Expedition-date IDs strictly before ``before_date_id``, newest first."""
    try:
        cutoff = int(before_date_id)
    except ValueError:
        return []
    out: list[str] = []
    if not narrations_dir.is_dir():
        return out
    for p in narrations_dir.iterdir():
        if not p.is_file():
            continue
        m = _CANONICAL_NARRATION_RE.fullmatch(p.name)
        if not m:
            continue
        did = m.group(1)
        try:
            if int(did) >= cutoff:
                continue
        except ValueError:
            continue
        out.append(did)
    out.sort(key=lambda x: int(x), reverse=True)
    return out


def narration_json_has_mid_episode_map(data: dict[str, Any]) -> bool:
    return get_mid_episode_map_insertion(data) is not None


def consecutive_prior_episodes_without_mid_map(
    narrations_dir: Path,
    before_date_id: str,
) -> int:
    """
    Walk prior canonical narration JSON files (by expedition date_id, newest first).

    Count consecutive episodes **without** a valid mid-episode parchment map insertion,
    stopping at the first older episode that has one or at end of known files.

    Missing or unreadable files **break** the streak (not counted toward consecutive
    absence) so corrupt gaps do not inflate dry spells forever.
    """
    n = 0
    for did in _prior_canonical_date_ids_sorted_newest_first(narrations_dir, before_date_id):
        path = narrations_dir / f"narration{did}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, JSONDecodeError, UnicodeDecodeError):
            break
        if not isinstance(data, dict):
            break
        if narration_json_has_mid_episode_map(data):
            break
        n += 1
    return n


def phase1_implies_travel_or_relocation(phase1: dict[str, Any]) -> bool:
    """Heuristic from Phase 1 JSON only (before merge)."""
    meta = phase1.get("episode_metadata") or {}
    lens = str(meta.get("primary_lens") or "").strip().lower()
    if lens == "navigation":
        return True
    try:
        if int(meta.get("narrative_energy")) == 0:
            return True
    except (TypeError, ValueError):
        pass
    spine = phase1.get("narrative_spine") or {}
    blob = " ".join(
        str(x)
        for x in (
            spine.get("core_situation"),
            spine.get("environment_context"),
            meta.get("location_summary"),
        )
        if x
    )
    segments = phase1.get("segments") or []
    narr = " ".join(str(s.get("narration") or "") for s in segments if isinstance(s, dict))
    combined = f"{blob}\n{narr}".lower()
    return bool(_TRAVEL_HINT_RE.search(combined))


def compute_phase2_runtime_map_policy_block(
    phase1: dict[str, Any],
    date_id: str,
    narrations_dir: Path,
) -> tuple[str, str]:
    """
    Return (prompt_suffix, stderr_log_line).

    ``prompt_suffix`` is appended into Phase 2 system text via ``__RUNTIME_MAP_POLICY_BLOCK__``.
    ``stderr_log_line`` is non-empty when the suffix is injected (for operator visibility).
    """
    if runtime_map_policy_disabled():
        return "", ""
    streak = consecutive_prior_episodes_without_mid_map(narrations_dir, date_id)
    thr = map_dry_spell_threshold()
    travel = phase1_implies_travel_or_relocation(phase1)
    if streak < thr or not travel:
        return "", ""
    block = (
        "\nRUNTIME MAP POLICY NUDGE (pipeline):\n"
        f"- Telemetry: **{streak}** consecutive prior narration episode(s) (by expedition date among saved "
        "`narrationYYYYMMDD.json` files) had **no** mid-episode parchment route overlay; threshold is **{thr}**.\n"
        "- Phase 1 signals **travel/relocation emphasis** for today.\n"
        "- **Strongly prefer** exactly one `map_insertions` entry (`visual_style`: `parchment_overlay`, "
        "total on-screen map time still **under 8 seconds**) on the segment that carries the clearest geographic "
        "progression along the route.\n"
        "- **Never** anchor overlay timing on segment 1 (hook).\n"
        "- **Final segment is allowed** when it still states concrete geography (bank, reach, landmark)—keep "
        "the overlay brief.\n"
        "- Still omit maps when Phase 1 is geographically static despite wording elsewhere.\n"
    )
    log = (
        f"[Phase 2] Runtime map policy nudge: prior_without_mid_map={streak} (>={thr}), "
        "travel_heuristic=true"
    )
    return block, log
