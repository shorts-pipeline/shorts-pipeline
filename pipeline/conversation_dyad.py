"""
Long-conversation dyad helpers: normalize pairs, read merged JSON, detect journal co-presence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

LEWIS_CLARK_DYAD: tuple[str, str] = ("clark", "lewis")

# Clark first-person joint action with Lewis (journal spelling variants).
_JOURNAL_LEWIS_CLARK_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, str]], ...] = (
    (re.compile(r"\bcapt\.?\s*lewis\s+and\s+(?:my\s+)?self\b", re.I), LEWIS_CLARK_DYAD),
    (re.compile(r"\blewis\s+and\s+(?:my\s+)?self\b", re.I), LEWIS_CLARK_DYAD),
    (re.compile(r"\b(?:meriwether\s+)?lewis\s+and\s+(?:i|clark)\b", re.I), LEWIS_CLARK_DYAD),
    (re.compile(r"\bclark\s+and\s+(?:meriwether\s+)?lewis\b", re.I), LEWIS_CLARK_DYAD),
    (re.compile(r"\b(?:meriwether\s+)?lewis\s+and\s+clark\b", re.I), LEWIS_CLARK_DYAD),
    (
        re.compile(
            r"\bwe\s+(?:walked|went|climbed|rode|set\s+out)\b.{0,80}\b(?:together|both)\b", re.I
        ),
        LEWIS_CLARK_DYAD,
    ),
)

# Named roster pairs explicitly together (conservative; both names near a joint verb).
_NAMED_PAIR_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, str]], ...] = (
    (re.compile(r"\bclark\s+and\s+ordway\b", re.I), ("clark", "ordway")),
    (re.compile(r"\bordway\s+and\s+clark\b", re.I), ("clark", "ordway")),
    (re.compile(r"\bclark\s+and\s+york\b", re.I), ("clark", "york")),
    (re.compile(r"\byork\s+and\s+clark\b", re.I), ("clark", "york")),
    (re.compile(r"\blewis\s+and\s+drouillard\b", re.I), ("drouillard", "lewis")),
    (re.compile(r"\bdrouillard\s+and\s+lewis\b", re.I), ("drouillard", "lewis")),
    (re.compile(r"\bclark\s+and\s+colter\b", re.I), ("clark", "colter")),
    (re.compile(r"\bcolter\s+and\s+clark\b", re.I), ("clark", "colter")),
    (re.compile(r"\bclark\s+and\s+gass\b", re.I), ("clark", "gass")),
    (re.compile(r"\bgass\s+and\s+clark\b", re.I), ("clark", "gass")),
)


def normalize_conversation_dyad(speakers: Any) -> tuple[str, str] | None:
    """Return sorted (a, b) roster ids or None."""
    if not isinstance(speakers, (list, tuple)) or len(speakers) != 2:
        return None
    a = str(speakers[0] or "").strip().lower()
    b = str(speakers[1] or "").strip().lower()
    if not a or not b or a == b or a == "narrator" or b == "narrator":
        return None
    return tuple(sorted((a, b)))


def format_dyad_label(dyad: tuple[str, str]) -> str:
    return "+".join(dyad)


def extract_conversation_dyad(data: dict[str, Any]) -> tuple[str, str] | None:
    """Read ``conversation_micro_arc.speakers`` from merged narration when long-conversation."""
    if not bool(data.get("long_conversation_mode")):
        return None
    arc = data.get("conversation_micro_arc")
    if not isinstance(arc, dict):
        return None
    return normalize_conversation_dyad(arc.get("speakers"))


def load_known_conversation_dyads(repo_root: Path | str) -> list[tuple[str, str]]:
    """Pair-portrait member pairs from ``config/narration_characters.json``."""
    path = Path(repo_root) / "config" / "narration_characters.json"
    dyads: list[tuple[str, str]] = [LEWIS_CLARK_DYAD]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return dyads
    rows = raw.get("pair_portraits")
    if not isinstance(rows, list):
        return dyads
    seen = {LEWIS_CLARK_DYAD}
    for row in rows:
        if not isinstance(row, dict):
            continue
        mids = row.get("member_ids")
        nd = normalize_conversation_dyad(mids)
        if nd and nd not in seen:
            seen.add(nd)
            dyads.append(nd)
    return dyads


def detect_journal_explicit_dyads(entry_text: str) -> list[tuple[str, str]]:
    """
    Roster dyads the journal explicitly puts in a shared beat (joint action or named pair).
    Lewis+Clark patterns are checked first; deduped, captains preferred first in output order.
    """
    text = (entry_text or "").strip()
    if not text:
        return []
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for patterns in (_JOURNAL_LEWIS_CLARK_PATTERNS, _NAMED_PAIR_PATTERNS):
        for pat, dyad in patterns:
            if pat.search(text) and dyad not in seen:
                seen.add(dyad)
                found.append(dyad)
    return found


def build_journal_dyad_phase1_block(dyads: list[tuple[str, str]]) -> str:
    if not dyads:
        return ""
    labels = [format_dyad_label(d) for d in dyads]
    lines = [
        "JOURNAL-EXPLICIT SPEAKER PAIRS (today's source names these two together—prefer one for "
        "`conversation_micro_arc.speakers` when that beat is centerpiece-worthy):",
    ]
    for label in labels:
        lines.append(f"- {label}")
    lines.append(
        "If several pairs appear, pick the pair whose **shared scene** best anchors the micro-arc "
        "(joint walk, council, hunt, repair, etc.)—not a pair inferred only from separate diary blocks."
    )
    return "\n".join(lines)


def parse_dyad_label(label: str) -> tuple[str, str] | None:
    if not label or "+" not in label:
        return None
    return normalize_conversation_dyad(label.split("+", 1))


def stale_conversation_dyads(
    lc_metas: list[dict[str, Any]],
    known_dyads: list[tuple[str, str]],
    *,
    exclude: tuple[str, str] | None = None,
    limit: int = 4,
) -> list[str]:
    """Dyad labels absent from recent long-conversation runs (for rotation hints)."""
    seen_labels: set[tuple[str, str]] = set()
    for m in lc_metas:
        raw = m.get("conversation_dyad")
        if not raw:
            continue
        nd = (
            parse_dyad_label(str(raw)) if isinstance(raw, str) else normalize_conversation_dyad(raw)
        )
        if nd:
            seen_labels.add(nd)
    ex = exclude or LEWIS_CLARK_DYAD
    stale: list[str] = []
    for dyad in known_dyads:
        if dyad == ex:
            continue
        if dyad not in seen_labels:
            stale.append(format_dyad_label(dyad))
        if len(stale) >= limit:
            break
    return stale
