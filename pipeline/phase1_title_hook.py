"""Episode title uniqueness helpers and a short post-Phase-1 title rewrite prompt."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

_MONTH = (
    r"January|February|March|April|May|June|July|August|"
    r"September|October|November|December"
)
_DATE_SUFFIX = re.compile(
    rf"\s*[-–—:]\s*(?:{_MONTH})\s+\d{{1,2}},\s+\d{{4}}\s*$",
    re.IGNORECASE,
)
_GENERIC_TITLE = re.compile(
    r"^lewis\s*(?:and|&)\s*clark\s+expedition(?:\s*:.*)?$",
    re.IGNORECASE,
)
_FORMULAIC_HOOK = re.compile(
    r"^(?:"
    r"as dawn\b|"
    r"at dawn\b|"
    r"as daybreak\b|"
    r"at daybreak\b|"
    r"as the sun rises\b|"
    r"as the sun rose\b|"
    r"at sunrise\b|"
    r"as sunrise\b|"
    r"another day\b|"
    r"the day began\b|"
    r"the day begins\b|"
    r"the day started\b|"
    r"this morning began\b|"
    r"this morning begins\b|"
    r"as the world awakens\b|"
    r"on (?:january|february|march|april|may|june|july|august|september|october|november|december)\b"
    r")",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

TITLE_LOOKBACK = 20
TITLE_REWRITE_MAX_TOKENS = 120
TITLE_REWRITE_MAX_RETRIES = 2

TITLE_REWRITE_SYSTEM = """You write YouTube Shorts titles for Lewis & Clark journal episodes.

Return ONLY a JSON object: {"title": "..."}.

Rules:
- 2–5 words naming a specific event, place, or conflict from THIS episode.
- No dates, no colons, no "Lewis & Clark Expedition".
- Do not reuse any listed recent title (same wording or obvious synonym stem like repeating "Testing the Waters").
- Prefer a concrete noun from the hook or core situation (court martial, cave, riffle, Kickapoo, soaked provisions).
"""


def title_stem(title: str) -> str:
    """Normalize an episode title for uniqueness (strip calendar suffix, punctuation)."""
    raw = str(title or "").strip()
    raw = _DATE_SUFFIX.sub("", raw)
    raw = re.sub(r"[^\w\s&]", " ", raw, flags=re.UNICODE)
    return re.sub(r"\s+", " ", raw).strip().lower()


def is_generic_episode_title(title: str) -> bool:
    """True for date-only fallback titles like 'Lewis & Clark Expedition: September 09, 1803'."""
    stem = title_stem(title)
    if not stem:
        return True
    return bool(_GENERIC_TITLE.match(stem))


def first_spoken_sentence(segments: list[Any]) -> str:
    """First spoken sentence of segment 1 (cast dialogue if present, else narration)."""
    if not isinstance(segments, list) or not segments:
        return ""
    first = segments[0]
    if not isinstance(first, dict):
        return ""
    text = ""
    rows = first.get("dialogue")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            spoken = str(row.get("text") or "").strip()
            if spoken:
                text = spoken
                break
    if not text:
        text = str(first.get("narration") or "").strip()
    if not text:
        return ""
    parts = _SENTENCE_SPLIT.split(text, maxsplit=1)
    return (parts[0] if parts else text).strip()


def is_formulaic_hook_opener(sentence: str) -> bool:
    """True when the first spoken sentence starts with a weather/diary formula."""
    s = re.sub(r"^['\"“”]+", "", (sentence or "").strip())
    return bool(s) and bool(_FORMULAIC_HOOK.match(s))


def title_rewrite_reason(
    title: str,
    *,
    recent_title_stems: Iterable[str] | None = None,
) -> str | None:
    """Why this title should be rewritten, or None if it is fine."""
    if is_generic_episode_title(title):
        return "generic Lewis & Clark Expedition / empty title"
    stem = title_stem(title)
    prior = {title_stem(x) for x in (recent_title_stems or []) if str(x or "").strip()}
    prior.discard("")
    if stem and stem in prior:
        return f"reuses recent title stem {stem!r}"
    return None


def parse_rewritten_title(raw: str) -> str:
    """Extract a title string from a short model reply (JSON object or bare string)."""
    from pipeline.narration_common import clean_json_reply

    cleaned = clean_json_reply(raw or "")
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        t = str(parsed.get("title") or "").strip()
        if t:
            return t
    if isinstance(parsed, str) and parsed.strip():
        return parsed.strip()
    line = (raw or "").strip().strip('"').strip("'")
    if line.startswith("{") or not line:
        return ""
    return line.splitlines()[0].strip().strip('"').strip("'")


def build_title_rewrite_user_prompt(
    phase1: dict[str, Any],
    *,
    reason: str,
    recent_titles: list[str],
) -> str:
    """Compact user prompt: current title, avoid-list, hook + spine only."""
    title = str(phase1.get("title") or "").strip() or "(missing)"
    spine = phase1.get("narrative_spine") if isinstance(phase1.get("narrative_spine"), dict) else {}
    core = str(spine.get("core_situation") or "").strip()
    hook = first_spoken_sentence(phase1.get("segments") or [])
    avoid = "\n".join(f"- {t}" for t in recent_titles if str(t).strip()) or "- (none)"
    return (
        f"Current title: {title}\n"
        f"Rewrite because: {reason}\n\n"
        f"Do not reuse these recent titles:\n{avoid}\n\n"
        f"Core situation: {core or '(none)'}\n"
        f"First spoken sentence: {hook or '(none)'}\n"
    )


def accept_rewritten_title(
    candidate: str,
    *,
    recent_title_stems: Iterable[str] | None = None,
) -> str | None:
    """Return a cleaned title if it passes uniqueness rules, else None."""
    t = str(candidate or "").strip()
    t = _DATE_SUFFIX.sub("", t).strip()
    t = re.sub(r"\s+", " ", t)
    if not t or title_rewrite_reason(t, recent_title_stems=recent_title_stems):
        return None
    words = t.split()
    if not (2 <= len(words) <= 8):
        return None
    return t
