#!/usr/bin/env python3
"""
Theme selection engine: reduce repetitiveness by comparing the current journal
entry to the last N narrations and, when similar, recommending a single key topic
to focus on in depth. Tracks used focus topics so we don't repeat.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# When run as `python theme_engine/theme_selector.py`, sys.path[0] is theme_engine/; add repo root so `pipeline.*` and other top-level imports resolve.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Default paths relative to repo root (parent of theme_engine)
DEFAULT_NARRATIONS_DIR = Path(__file__).resolve().parent.parent / "narrations"
DEFAULT_JOURNAL_DIR = Path(__file__).resolve().parent.parent / "journal-entries"
DEFAULT_STATE_PATH = Path(__file__).resolve().parent / "state.json"
DEFAULT_EMBEDDINGS_PATH = Path(__file__).resolve().parent / "embeddings.json"
MAX_ENTRIES = 14
SIMILARITY_THRESHOLD = 0.15  # Min word-overlap ratio (fallback when embeddings disabled)
EMBEDDING_SIMILARITY_THRESHOLD = (
    0.65  # Min cosine similarity to consider "similar" when using embeddings
)
EMBEDDING_MODEL = "text-embedding-3-small"

# ``journal_text_for_theme_embedding``: TEI text budget for embeddings (~8k-token ballpark for
# English; ``text-embedding-3-small`` max input 8191 tokens). Most entries stay short; long days
# use more of the cap. Changing these shifts cosine geometry — run ``backfill`` or
# ``refresh-embeddings`` for dates you care about.
THEME_JOURNAL_EMBED_MAX_ENTRY_CHARS = 24_000
THEME_JOURNAL_EMBED_MAX_NOTES_CHARS = 12_000
THEME_JOURNAL_EMBED_MAX_TOTAL_CHARS = 28_000

# ``embeddings.json`` may include this string key alongside date_id -> vector entries.
EMBEDDING_STORE_NOTE_KEY = "_embedding_store_note"
DEFAULT_EMBEDDING_STORE_NOTE = (
    "Values keyed by YYYYMMDD are embedding vectors. Rows not rebuilt after "
    "THEME_JOURNAL_EMBED_MAX_* was raised (April 2026) may still reflect the older policy "
    "(~500-character entry slice + notes). Compare apples-to-apples using "
    "`python theme_engine/theme_selector.py refresh-embeddings <date_id>…` or full "
    "`backfill` to re-embed from current journal XML."
)

_EMBEDDING_DATE_ID_RE = re.compile(r"^\d{8}$")

# Adjectives (and other non-noun descriptors) that make poor focus topics; filter these out when choosing.
FOCUS_TOPIC_EXCLUDED_WORDS = frozenset(
    {
        "thick",
        "thin",
        "cold",
        "warm",
        "heavy",
        "light",
        "dark",
        "long",
        "short",
        "great",
        "little",
        "high",
        "low",
        "hard",
        "soft",
        "strong",
        "weak",
        "deep",
        "shallow",
        "wide",
        "narrow",
        "fast",
        "slow",
        "early",
        "late",
        "dry",
        "wet",
        "full",
        "empty",
        "clear",
        "sharp",
        "rough",
        "smooth",
        "quiet",
        "loud",
        "bright",
        "calm",
        "tense",
        "simple",
        "complex",
        "easy",
        "difficult",
        "general",
        "specific",
        "normal",
        "strange",
        "common",
        "rare",
        "typical",
        "main",
        "major",
        "minor",
        "final",
        "initial",
        "original",
        "modern",
        "ancient",
        "current",
        "past",
        "real",
        "same",
        "different",
        "similar",
        "various",
    }
)


def load_state(state_path: Path) -> dict[str, Any]:
    """Load state JSON. Returns dict with entries, used_focus_topics, and focus_topic_queue."""
    if not state_path.exists():
        return {"entries": [], "used_focus_topics": [], "focus_topic_queue": []}
    data = json.loads(state_path.read_text(encoding="utf-8"))
    return {
        "entries": data.get("entries", []),
        "used_focus_topics": data.get("used_focus_topics", []),
        "focus_topic_queue": data.get("focus_topic_queue", []),
    }


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _use_embeddings() -> bool:
    """Use embeddings when OPENAI_API_KEY is set and THEME_USE_EMBEDDINGS is not '0'."""
    if os.environ.get("THEME_USE_EMBEDDINGS", "1") == "0":
        return False
    return bool(os.environ.get("OPENAI_API_KEY"))


def get_embedding(text: str) -> list[float] | None:
    """Return embedding vector for text using OpenAI text-embedding-3-small, or None on failure."""
    if not text.strip():
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=text.strip())
        return response.data[0].embedding
    except Exception:
        return None


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors (assumes L2-normalized; result in [-1, 1], typically [0, 1])."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / (norm_a * norm_b)


def load_embeddings_raw(embeddings_path: Path) -> dict[str, Any]:
    """Full JSON object from ``embeddings.json`` (vectors + optional string metadata keys)."""
    if not embeddings_path.exists():
        return {}
    try:
        data = json.loads(embeddings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def split_embeddings_store(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[float]]]:
    """Split store into metadata (non-date_id or non-list) vs ``YYYYMMDD`` embedding vectors."""
    meta: dict[str, Any] = {}
    vectors: dict[str, list[float]] = {}
    for k, v in data.items():
        if _EMBEDDING_DATE_ID_RE.match(str(k)) and isinstance(v, list):
            vectors[str(k)] = v
        else:
            meta[str(k)] = v
    return meta, vectors


def join_embeddings_store(meta: dict[str, Any], vectors: dict[str, list[float]]) -> dict[str, Any]:
    return {**meta, **vectors}


def load_embeddings(embeddings_path: Path = DEFAULT_EMBEDDINGS_PATH) -> dict[str, list[float]]:
    """Load embedding store: ``date_id`` -> vector (metadata keys excluded)."""
    _meta, vectors = split_embeddings_store(load_embeddings_raw(embeddings_path))
    return vectors


def save_embedding(
    embeddings_path: Path,
    date_id: str,
    vector: list[float],
    keep_date_ids: set[str] | None = None,
) -> None:
    """Save one embedding and optionally trim store to keep only keep_date_ids (e.g. last N entries)."""
    meta, vectors = split_embeddings_store(load_embeddings_raw(embeddings_path))
    vectors[date_id] = vector
    if keep_date_ids is not None:
        vectors = {k: v for k, v in vectors.items() if k in keep_date_ids}
    if EMBEDDING_STORE_NOTE_KEY not in meta:
        meta[EMBEDDING_STORE_NOTE_KEY] = DEFAULT_EMBEDDING_STORE_NOTE
    embeddings_path.parent.mkdir(parents=True, exist_ok=True)
    embeddings_path.write_text(
        json.dumps(join_embeddings_store(meta, vectors), indent=0),
        encoding="utf-8",
    )


def remove_date_from_state(
    date_id: str,
    state_path: Path = DEFAULT_STATE_PATH,
    embeddings_path: Path = DEFAULT_EMBEDDINGS_PATH,
) -> None:
    """
    Remove this date_id from theme state and embeddings (e.g. when re-narrating).
    Call before regenerating narration so recommend is not influenced by the old entry.
    """
    state = load_state(state_path)
    state["entries"] = [e for e in state["entries"] if e.get("date_id") != date_id]
    save_state(state_path, state)

    if embeddings_path.exists():
        meta, vectors = split_embeddings_store(load_embeddings_raw(embeddings_path))
        if date_id in vectors:
            del vectors[date_id]
            embeddings_path.parent.mkdir(parents=True, exist_ok=True)
            embeddings_path.write_text(
                json.dumps(join_embeddings_store(meta, vectors), indent=0),
                encoding="utf-8",
            )


def get_last_narration_dates(narrations_dir: Path, n: int = MAX_ENTRIES) -> list[str]:
    """Return the last n date_ids that have a narration file, newest last."""
    # Exclude .meta.json
    paths = [p for p in narrations_dir.glob("narration*.json") if ".meta." not in p.name]

    def date_id_from_path(p: Path) -> str | None:
        m = re.match(r"narration(\d{8})\.json", p.name)
        return m.group(1) if m else None

    date_ids = sorted({date_id_from_path(p) for p in paths if date_id_from_path(p)})
    return date_ids[-n:] if len(date_ids) >= n else date_ids


def prior_narration_date_ids_for_similarity(
    date_id: str,
    narrations_dir: Path,
    n: int = MAX_ENTRIES,
) -> list[str]:
    """
    Date IDs to compare the current journal against for theme similarity.
    Uses the same last-N narration window as build_focus_topic_queue (disk truth),
    excluding the episode being generated. Does not use state.json entries — those
    can lag behind narrations/ and embeddings.json if record failed or state was reverted.
    """
    return [d for d in get_last_narration_dates(narrations_dir, n=n) if d != date_id]


def extract_entry_text_from_xml(xml_path: Path) -> str:
    """Extract entry paragraph text from Lewis & Clark TEI XML (uses pipeline.narration_common for TEI choice/corr resolution)."""
    from pipeline.narration_common import extract_entry_text

    return extract_entry_text(xml_path)


def count_alphabetic_words(text: str) -> int:
    """Count alphabetic words (letter runs only). Single source of truth for 'word count' used by theme logic and run-daily skip."""
    return len(re.findall(r"[A-Za-z]+", text)) if text else 0


def entry_word_count(date_str: str, journal_dir: Path = DEFAULT_JOURNAL_DIR) -> int:
    """Return alphabetic word count of journal entry for date_str (YYYY-MM-DD). 0 if file missing or no text."""
    xml_path = journal_dir / f"{date_str}.xml"
    if not xml_path.exists():
        return 0
    text = extract_entry_text_from_xml(xml_path)
    return count_alphabetic_words(text)


def get_current_journal_summary(
    date_id: str,
    journal_dir: Path,
    max_chars: int = THEME_JOURNAL_EMBED_MAX_ENTRY_CHARS,
) -> str:
    """
    Get a short excerpt of the journal entry for the given date.
    Uses first ``max_chars`` of entry text (default matches the entry slice in
    ``journal_text_for_theme_embedding``). ``state.json`` stores this as ``journal_excerpt``.
    """
    # date_id 18030905 -> 1803-09-05.xml
    if len(date_id) != 8:
        return ""
    xml_name = f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:]}.xml"
    xml_path = journal_dir / xml_name
    if not xml_path.exists():
        return ""
    text = extract_entry_text_from_xml(xml_path)
    return text[:max_chars].strip() if text else ""


def journal_text_for_theme_embedding(
    date_id: str,
    journal_dir: Path,
    *,
    max_entry_chars: int = THEME_JOURNAL_EMBED_MAX_ENTRY_CHARS,
    max_notes_chars: int = THEME_JOURNAL_EMBED_MAX_NOTES_CHARS,
    max_total_chars: int = THEME_JOURNAL_EMBED_MAX_TOTAL_CHARS,
) -> str:
    """
    Text passed to OpenAI embeddings for theme similarity: journal entry excerpt plus linked TEI
    editorial notes (same footnotes as Phase 1). Short entries that share surface wording but differ
    in footnotes (e.g. rank, commissions) embed farther apart. Stays within a safe size for
    text-embedding-3-small context.

    Note: Changing this format shifts cosine geometry; re-run ``record`` or ``backfill`` to refresh
    ``embeddings.json`` for dates you care about.
    """
    if len(date_id) != 8:
        return ""
    xml_name = f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:]}.xml"
    xml_path = journal_dir / xml_name
    if not xml_path.exists():
        return ""

    from pipeline.narration_common import extract_entry_linked_notes

    entry_full = extract_entry_text_from_xml(xml_path)
    entry_part = entry_full[:max_entry_chars].strip() if entry_full else ""

    notes_raw = extract_entry_linked_notes(xml_path)
    notes_part = notes_raw[:max_notes_chars].strip() if notes_raw else ""

    if entry_part and notes_part:
        combined = (
            entry_part + "\n\n--- TEI editorial notes (linked footnotes) ---\n\n" + notes_part
        )
    elif entry_part:
        combined = entry_part
    elif notes_part:
        combined = "--- TEI editorial notes (linked footnotes) ---\n\n" + notes_part
    else:
        combined = ""

    if len(combined) > max_total_chars:
        combined = combined[:max_total_chars].rstrip()
    return combined


def extract_summary_and_topics_from_narration(narration_path: Path) -> tuple[str, list[str]]:
    """
    Extract a one-line summary and key topics from a narration JSON.
    Summary: title if present, else first narration line. Topics: simple word extraction
    from title + narration text only (not stage_direction or video_prompt). Replace with LLM for richer topics.
    """
    data = json.loads(narration_path.read_text(encoding="utf-8"))
    summary = (data.get("title") or "").strip()
    script = data.get("narration_script") or []
    if not summary and script:
        summary = (script[0].get("narration") or "")[:200]
    # Collect text for topic hints (title + narration text only)
    words: list[str] = []
    words.extend(re.findall(r"[A-Za-z]+", summary))
    for seg in script:
        words.extend(re.findall(r"[A-Za-z]+", (seg.get("narration") or "")))
    # Simple topic hints: lowercased, len > 4, exclude common
    stop = {
        "the",
        "this",
        "that",
        "with",
        "from",
        "they",
        "have",
        "were",
        "their",
        "there",
        "would",
        "could",
        "about",
        "which",
        "while",
        "after",
        "before",
        "scene",
        "camera",
        "shot",
        "wide",
        "close",
        "pan",
    }
    counted: dict[str, int] = {}
    for w in words:
        w = w.lower()
        if len(w) > 4 and w not in stop:
            counted[w] = counted.get(w, 0) + 1
    # Top 8 as "key topics" for now (phrases would need LLM)
    key_topics = [k for k, _ in sorted(counted.items(), key=lambda x: -x[1])[:8]]
    return summary, key_topics


def _word_set(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z]+", text.lower()))


def similarity_ratio(current_summary: str, previous_summary: str) -> float:
    """
    Word-overlap ratio. Uses symmetric measure: max of (|cur & prev|/|cur|, |cur & prev|/|prev|)
    so short-vs-long comparisons are fair (e.g. 3-word summary vs 500-char entry).
    """
    cur = _word_set(current_summary)
    prev = _word_set(previous_summary)
    if not cur or not prev:
        return 0.0
    overlap = len(cur & prev)
    return max(overlap / len(cur), overlap / len(prev))


def closest_similarity_score(summary: str, other_summaries: list[str]) -> float:
    """Max similarity (3 decimal places) of summary to any of other_summaries. 0 if no others."""
    if not other_summaries:
        return 0.0
    best = max(similarity_ratio(summary, s) for s in other_summaries)
    return round(best, 3)


def get_key_topics_for_date(
    date_id: str, narrations_dir: Path = DEFAULT_NARRATIONS_DIR
) -> list[str]:
    """Load key_topics for a date from its narration JSON. Returns [] if file missing."""
    path = narrations_dir / f"narration{date_id}.json"
    if not path.exists():
        return []
    _, topics = extract_summary_and_topics_from_narration(path)
    return topics or []


def build_focus_topic_queue(
    narrations_dir: Path = DEFAULT_NARRATIONS_DIR,
    used_focus_topics: list[str] | None = None,
    n: int = MAX_ENTRIES,
) -> list[str]:
    """
    Build a pre-filtered priority queue of focus topics from the last n narrations.
    Excludes adjectives (FOCUS_TOPIC_EXCLUDED_WORDS) and used_focus_topics.
    Ordered by frequency (most common first) so the next few topics are easy to review.
    """
    used = {t.lower() for t in (used_focus_topics or [])}
    last_dates = get_last_narration_dates(narrations_dir, n=n)
    counted: dict[str, int] = {}
    for d in last_dates:
        path = narrations_dir / f"narration{d}.json"
        if not path.exists():
            continue
        _, topics = extract_summary_and_topics_from_narration(path)
        for t in topics or []:
            tl = t.lower()
            if not tl or tl in FOCUS_TOPIC_EXCLUDED_WORDS or tl in used:
                continue
            counted[tl] = counted.get(tl, 0) + 1
    # Priority by frequency (desc), then alphabetically for stability
    ordered = sorted(counted.items(), key=lambda x: (-x[1], x[0]))
    return [t for t, _ in ordered]


def choose_focus_topic(
    available_topics: list[str],
    used_focus_topics: list[str],
    avoid_topics: set[str] | None = None,
) -> str | None:
    """Pick the first topic not in used_focus_topics, not in avoid_topics, and not an excluded adjective
    (e.g. topics from similar entries; exclude words like 'thick' that make poor focus themes)."""
    used = {t.lower() for t in used_focus_topics}
    avoid = {t.lower() for t in (avoid_topics or set())}
    for t in available_topics:
        if not t:
            continue
        tl = t.lower()
        if tl in used or tl in avoid or tl in FOCUS_TOPIC_EXCLUDED_WORDS:
            continue
        return t
    return None


def recommend(
    date_id: str,
    journal_dir: Path = DEFAULT_JOURNAL_DIR,
    narrations_dir: Path = DEFAULT_NARRATIONS_DIR,
    state_path: Path = DEFAULT_STATE_PATH,
    embeddings_path: Path = DEFAULT_EMBEDDINGS_PATH,
    n: int = MAX_ENTRIES,
) -> dict[str, Any]:
    """
    Decide whether to recommend a focus topic for the current date.
    Returns dict with focus_topic, reason, and (when similarity was computed) audit fields:
    closest_prior_similarity, closest_prior_date_id, similarity_threshold.

    Similarity is measured against the last N narration dates on disk (excluding date_id),
    not against state.json entries — so behavior stays aligned with narrations/ and
    embeddings.json even when state.entries is stale.

    When focus_topic is set, the engine updates state (adds topic to used_focus_topics).

    Similarity-based focus uses **embeddings only** (cosine ≥ EMBEDDING_SIMILARITY_THRESHOLD).
    There is no word-overlap fallback: if embeddings are disabled, or the current text cannot
    be embedded, or no prior narration in the window has a stored vector, the result is
    not_similar (no focus from journal similarity). Blank-page handling is unchanged.
    """
    state = load_state(state_path)
    used = state["used_focus_topics"]
    queue = state.get("focus_topic_queue") or []

    embedding_source = journal_text_for_theme_embedding(date_id, journal_dir)
    if not embedding_source:
        # Blank page: no entry text and no linked TEI notes. Pick from pre-filtered queue (or build once).
        if not queue:
            queue = build_focus_topic_queue(narrations_dir, used, n=n)
            state["focus_topic_queue"] = queue
        focus = choose_focus_topic(queue, used)
        if focus is not None:
            state["used_focus_topics"] = used + [focus]
            state["focus_topic_queue"] = [t for t in queue if t.lower() != focus.lower()]
            save_state(state_path, state)
            return {"focus_topic": focus, "reason": "blank_page"}
        return {"focus_topic": None, "reason": "no_journal"}

    # If the journal excerpt itself is extremely short, it's hard to build a whole
    # episode around it. In that case, always try to anchor the episode on a
    # reusable focus topic, even if this entry isn't similar to recent ones.
    word_count = count_alphabetic_words(embedding_source)
    is_short_entry = word_count < 20

    prior_dates = prior_narration_date_ids_for_similarity(date_id, narrations_dir, n=n)
    similar = False
    avoid_topics: set[str] = set()
    best_sim = 0.0
    best_date: str | None = None
    threshold_used: float | None = None

    # Similarity-based focus topics require embeddings only (no word-overlap fallback).
    if _use_embeddings():
        threshold_used = EMBEDDING_SIMILARITY_THRESHOLD
        current_emb = get_embedding(embedding_source)
        store = load_embeddings(embeddings_path) if current_emb else {}
        prior_with_emb = [d for d in prior_dates if store and d in store]
        if current_emb and prior_with_emb:
            for d in prior_with_emb:
                sim = cosine_sim(current_emb, store[d])
                if sim > best_sim:
                    best_sim = sim
                    best_date = d
                if sim >= EMBEDDING_SIMILARITY_THRESHOLD:
                    similar = True
                    for t in get_key_topics_for_date(d, narrations_dir):
                        if t:
                            avoid_topics.add(t.lower())
        # else: no embedding for current text, empty store, or no prior vectors — not similar
    else:
        # THEME_USE_EMBEDDINGS=0 or no OPENAI_API_KEY: never use journal similarity for focus
        threshold_used = None

    audit: dict[str, Any] = {
        "closest_prior_similarity": round(best_sim, 4) if best_sim > 0 else None,
        "closest_prior_date_id": best_date,
        "similarity_threshold": threshold_used,
    }

    if not similar:
        return {"focus_topic": None, "reason": "not_similar", **audit}

    # Use pre-filtered queue (no adjectives, no used); rebuild if empty
    if not queue:
        queue = build_focus_topic_queue(narrations_dir, used, n=n)
        state["focus_topic_queue"] = queue

    focus = choose_focus_topic(queue, used, avoid_topics=avoid_topics)
    if focus is None:
        return {"focus_topic": None, "reason": "no_unused_topic", **audit}

    # Persist: add to used and remove from queue so next run sees updated queue
    state["used_focus_topics"] = used + [focus]
    state["focus_topic_queue"] = [t for t in queue if t.lower() != focus.lower()]
    save_state(state_path, state)
    return {
        "focus_topic": focus,
        "reason": "short_entry" if is_short_entry else "similar_redirect",
        **audit,
    }


def record_narration(
    date_id: str,
    narrations_dir: Path = DEFAULT_NARRATIONS_DIR,
    state_path: Path = DEFAULT_STATE_PATH,
    journal_dir: Path = DEFAULT_JOURNAL_DIR,
    embeddings_path: Path = DEFAULT_EMBEDDINGS_PATH,
    max_entries: int = MAX_ENTRIES,
) -> None:
    """
    After narration is generated, record this episode in state: add entry with
    summary and journal_excerpt (no key_topics; those live in focus_topic_queue).
    Rebuilds focus_topic_queue from last n narrations (pre-filtered). Trim entries to last max_entries.
    When embeddings are enabled, embeds journal_text_for_theme_embedding (entry excerpt + linked TEI notes)
    and stores it; closest_similarity uses cosine. ``journal_excerpt`` in state remains entry-only for display.
    """
    narration_path = narrations_dir / f"narration{date_id}.json"
    if not narration_path.exists():
        print(
            f"[WARN] Theme record skipped: narration file not found at {narration_path}",
            file=sys.stderr,
        )
        return
    summary, _ = extract_summary_and_topics_from_narration(narration_path)
    if not summary:
        summary = get_current_journal_summary(date_id, journal_dir, max_chars=300)
    journal_excerpt = get_current_journal_summary(date_id, journal_dir)

    state = load_state(state_path)
    # Re-recording the same date_id must replace the old row, not append duplicates.
    entries = [e for e in state["entries"] if e.get("date_id") != date_id]
    emb: list[float] | None = None

    embed_text = journal_text_for_theme_embedding(date_id, journal_dir)
    if _use_embeddings() and embed_text:
        emb = get_embedding(embed_text)
        if emb:
            next_entries = entries + [{"date_id": date_id}]
            keep = {e.get("date_id") for e in next_entries[-max_entries:] if e.get("date_id")}
            save_embedding(embeddings_path, date_id, emb, keep_date_ids=keep)

    other_date_ids = [e.get("date_id") for e in entries if e.get("date_id")]
    other_date_ids = list(dict.fromkeys(other_date_ids))  # unique, preserve order

    closest_peer_date_id: str | None = None

    if _use_embeddings() and emb is not None:
        store = load_embeddings(embeddings_path)
        if store:
            best = 0.0
            for d in other_date_ids:
                if d not in store:
                    continue
                sim = cosine_sim(emb, store[d])
                if sim > best:
                    best = sim
                    closest_peer_date_id = d
            closest = round(best, 3)
        else:
            other_texts = [
                (e.get("journal_excerpt") or e.get("summary") or "")
                for e in entries
                if e.get("journal_excerpt") or e.get("summary")
            ]
            other_texts = [t for t in other_texts if t]
            closest = closest_similarity_score(summary, other_texts) if other_texts else 0.0
            # Word overlap: find which prior entry had max ratio vs this summary
            if other_texts:
                best_r = 0.0
                for e in entries:
                    od = e.get("date_id")
                    if not od:
                        continue
                    t = (e.get("journal_excerpt") or e.get("summary") or "").strip()
                    if not t:
                        continue
                    r = similarity_ratio(summary, t)
                    if r > best_r:
                        best_r = r
                        closest_peer_date_id = od
    else:
        other_texts = [
            (e.get("journal_excerpt") or e.get("summary") or "")
            for e in entries
            if e.get("journal_excerpt") or e.get("summary")
        ]
        other_texts = [t for t in other_texts if t]
        closest = closest_similarity_score(summary, other_texts) if other_texts else 0.0
        if other_texts:
            best_r = 0.0
            for e in entries:
                od = e.get("date_id")
                if not od:
                    continue
                t = (e.get("journal_excerpt") or e.get("summary") or "").strip()
                if not t:
                    continue
                r = similarity_ratio(summary, t)
                if r > best_r:
                    best_r = r
                    closest_peer_date_id = od

    row: dict[str, Any] = {
        "date_id": date_id,
        "summary": summary,
        "journal_excerpt": journal_excerpt or None,
        "closest_similarity": closest,
    }
    if closest_peer_date_id is not None:
        row["closest_peer_date_id"] = closest_peer_date_id
    entries.append(row)
    state["entries"] = entries[-max_entries:]
    # Rebuild pre-filtered queue so it includes topics from this narration and stays reviewable
    state["focus_topic_queue"] = build_focus_topic_queue(
        narrations_dir, state.get("used_focus_topics") or [], n=max_entries
    )
    save_state(state_path, state)


def refresh_embeddings_for_dates(
    date_ids: list[str],
    *,
    journal_dir: Path = DEFAULT_JOURNAL_DIR,
    embeddings_path: Path = DEFAULT_EMBEDDINGS_PATH,
) -> dict[str, list[float]]:
    """
    Re-embed listed ``date_ids`` from current journal XML (partial update).
    Preserves non-vector keys (e.g. ``_embedding_store_note``) in ``embeddings.json``.
    """
    meta, vectors = split_embeddings_store(load_embeddings_raw(embeddings_path))
    succeeded: list[str] = []
    for raw in date_ids:
        did = (raw or "").strip()
        if not _EMBEDDING_DATE_ID_RE.match(did):
            print(f"[WARN] skip invalid date_id {raw!r}", file=sys.stderr)
            continue
        text = journal_text_for_theme_embedding(did, journal_dir)
        if not text:
            print(f"[WARN] No journal text for {did}", file=sys.stderr)
            continue
        emb = get_embedding(text)
        if not emb:
            print(f"[WARN] Embedding API failed for {did}", file=sys.stderr)
            continue
        vectors[did] = emb
        succeeded.append(did)
        print(f"[OK] Re-embedded {did} (input {len(text)} chars)", file=sys.stderr)
    if EMBEDDING_STORE_NOTE_KEY not in meta:
        meta[EMBEDDING_STORE_NOTE_KEY] = DEFAULT_EMBEDDING_STORE_NOTE
    embeddings_path.parent.mkdir(parents=True, exist_ok=True)
    embeddings_path.write_text(
        json.dumps(join_embeddings_store(meta, vectors), indent=0),
        encoding="utf-8",
    )
    return {d: vectors[d] for d in succeeded}


def backfill_closest_similarity(
    state_path: Path = DEFAULT_STATE_PATH,
    embeddings_path: Path = DEFAULT_EMBEDDINGS_PATH,
    journal_dir: Path = DEFAULT_JOURNAL_DIR,
) -> None:
    """Add closest_similarity (3 decimal places) to every entry. When embeddings are enabled,
    backfill missing embeddings from journal XML (same dir as current entries) then compute
    similarity from cosine; else word overlap. 1 = most similar, 0 = least."""
    state = load_state(state_path)
    entries = state["entries"]
    meta: dict[str, Any] = {}
    vectors: dict[str, list[float]] = {}
    if _use_embeddings():
        meta, vectors = split_embeddings_store(load_embeddings_raw(embeddings_path))

    # Backfill missing embeddings from journal files
    if _use_embeddings():
        for e in entries:
            d = e.get("date_id")
            if not d or d in vectors:
                continue
            text = journal_text_for_theme_embedding(d, journal_dir)
            if not text:
                continue
            vec = get_embedding(text)
            if vec:
                vectors[d] = vec
        if vectors:
            keep = {e.get("date_id") for e in entries if e.get("date_id")}
            vectors = {k: v for k, v in vectors.items() if k in keep}
            if EMBEDDING_STORE_NOTE_KEY not in meta:
                meta[EMBEDDING_STORE_NOTE_KEY] = DEFAULT_EMBEDDING_STORE_NOTE
            embeddings_path.parent.mkdir(parents=True, exist_ok=True)
            embeddings_path.write_text(
                json.dumps(join_embeddings_store(meta, vectors), indent=0),
                encoding="utf-8",
            )

    store = vectors

    for i, e in enumerate(entries):
        summary = e.get("summary")
        if not summary:
            continue
        my_date = e.get("date_id") or ""

        if _use_embeddings() and my_date in store:
            my_emb = store[my_date]
            best = 0.0
            best_other: str | None = None
            for j, other in enumerate(entries):
                if j == i:
                    continue
                od = other.get("date_id")
                if not od or od not in store:
                    continue
                sim = cosine_sim(my_emb, store[od])
                if sim > best:
                    best = sim
                    best_other = od
            e["closest_similarity"] = round(best, 3)
            if best_other is not None:
                e["closest_peer_date_id"] = best_other
            elif "closest_peer_date_id" in e:
                del e["closest_peer_date_id"]
        else:
            best_r = 0.0
            best_other_w: str | None = None
            for j, other in enumerate(entries):
                if j == i:
                    continue
                od = other.get("date_id")
                if not od:
                    continue
                t = (other.get("summary") or "").strip()
                if not t:
                    continue
                r = similarity_ratio(summary, t)
                if r > best_r:
                    best_r = r
                    best_other_w = od
            e["closest_similarity"] = round(best_r, 3) if best_other_w is not None else 0.0
            if best_other_w is not None:
                e["closest_peer_date_id"] = best_other_w
            elif "closest_peer_date_id" in e:
                del e["closest_peer_date_id"]
    save_state(state_path, state)


def rebuild_focus_topic_queue(
    state_path: Path = DEFAULT_STATE_PATH,
    narrations_dir: Path = DEFAULT_NARRATIONS_DIR,
    n: int = MAX_ENTRIES,
    strip_key_topics_from_entries: bool = True,
) -> None:
    """Rebuild focus_topic_queue from last n narrations (pre-filtered). Optionally remove key_topics from entries."""
    state = load_state(state_path)
    used = state.get("used_focus_topics") or []
    state["focus_topic_queue"] = build_focus_topic_queue(narrations_dir, used, n=n)
    if strip_key_topics_from_entries:
        for e in state.get("entries") or []:
            if "key_topics" in e:
                del e["key_topics"]
    save_state(state_path, state)


def verify_similarity(
    date_ids: list[str],
    state_path: Path = DEFAULT_STATE_PATH,
    embeddings_path: Path = DEFAULT_EMBEDDINGS_PATH,
    journal_dir: Path = DEFAULT_JOURNAL_DIR,
) -> None:
    """For each date_id, print stored closest_similarity and which entry is closest (recomputed)."""
    state = load_state(state_path)
    entries = state["entries"]
    by_date = {e.get("date_id"): e for e in entries if e.get("date_id")}
    store = load_embeddings(embeddings_path) if _use_embeddings() else {}

    for date_id in date_ids:
        e = by_date.get(date_id)
        stored = e.get("closest_similarity") if e else None
        stored_peer = e.get("closest_peer_date_id") if e else None
        if not e:
            print(f"{date_id}: not in state")
            continue
        if _use_embeddings() and date_id in store:
            my_emb = store[date_id]
            best_sim = 0.0
            best_other = None
            for od, ov in store.items():
                if od == date_id:
                    continue
                sim = cosine_sim(my_emb, ov)
                if sim > best_sim:
                    best_sim = sim
                    best_other = od
            peer_note = f" stored_peer={stored_peer}" if stored_peer else ""
            print(
                f"{date_id}: stored={stored}{peer_note} recomputed={round(best_sim, 3)} closest_to={best_other}"
            )
        else:
            summary = e.get("journal_excerpt") or e.get("summary") or ""
            other_texts = [
                (by_date[d].get("journal_excerpt") or by_date[d].get("summary") or "")
                for d in by_date
                if d != date_id
            ]
            other_texts = [t for t in other_texts if t]
            if other_texts:
                best = 0.0
                best_other = None
                for other in entries:
                    if other.get("date_id") == date_id:
                        continue
                    t = other.get("journal_excerpt") or other.get("summary") or ""
                    if not t:
                        continue
                    r = similarity_ratio(summary, t)
                    if r > best:
                        best = r
                        best_other = other.get("date_id")
                peer_note = f" stored_peer={stored_peer}" if stored_peer else ""
                print(
                    f"{date_id}: stored={stored}{peer_note} recomputed={round(best, 3)} closest_to={best_other}"
                )
            else:
                print(f"{date_id}: stored={stored} (no others to compare)")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Theme selection engine: recommend focus topic or record a narration."
    )
    parser.add_argument(
        "action",
        choices=[
            "recommend",
            "record",
            "backfill",
            "backfill-topics",
            "verify",
            "refresh-embeddings",
        ],
        help="recommend: get focus_topic for this date. record: add this date to state. backfill: closest_similarity + closest_peer_date_id. backfill-topics: rebuild focus_topic_queue from narrations. verify: recomputed similarity and which date_id is closest (audit). refresh-embeddings: re-embed listed date_ids (partial update); prints pairwise cosine among listed ids.",
    )
    parser.add_argument(
        "date_id",
        nargs="*",
        help="Date identifier(s), e.g. 18030905 (required for recommend, record, verify, refresh-embeddings)",
    )
    parser.add_argument("--narrations-dir", type=Path, default=DEFAULT_NARRATIONS_DIR)
    parser.add_argument("--journal-dir", type=Path, default=DEFAULT_JOURNAL_DIR)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument(
        "--embeddings",
        type=Path,
        default=DEFAULT_EMBEDDINGS_PATH,
        help="Path to embeddings store (embeddings.json)",
    )
    parser.add_argument(
        "--n", type=int, default=MAX_ENTRIES, help="Number of recent narrations to consider"
    )
    args = parser.parse_args()

    if args.action == "backfill-topics":
        rebuild_focus_topic_queue(
            state_path=args.state, narrations_dir=args.narrations_dir, n=args.n
        )
        print(f"Rebuilt focus_topic_queue in {args.state}")
        return
    if args.action == "backfill":
        backfill_closest_similarity(
            state_path=args.state, embeddings_path=args.embeddings, journal_dir=args.journal_dir
        )
        print(f"Backfilled closest_similarity and closest_peer_date_id in {args.state}")
        return
    if args.action == "verify":
        if not args.date_id:
            parser.error(
                "at least one date_id required for verify (shows closest peer vs embeddings store)"
            )
        verify_similarity(
            args.date_id,
            state_path=args.state,
            embeddings_path=args.embeddings,
            journal_dir=args.journal_dir,
        )
        return
    if args.action == "refresh-embeddings":
        if not args.date_id:
            parser.error("at least one date_id required for refresh-embeddings")
        if not _use_embeddings():
            print(
                "[ERROR] Embeddings disabled (set OPENAI_API_KEY and THEME_USE_EMBEDDINGS=1).",
                file=sys.stderr,
            )
            return
        vecs = refresh_embeddings_for_dates(
            list(args.date_id),
            journal_dir=args.journal_dir,
            embeddings_path=args.embeddings,
        )
        ids = [d for d in args.date_id if (d or "").strip() in vecs]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a = (ids[i] or "").strip()
                b = (ids[j] or "").strip()
                if a in vecs and b in vecs:
                    sim = cosine_sim(vecs[a], vecs[b])
                    print(f"cosine_similarity({a}, {b}) = {round(sim, 4)}")
        return
    if not args.date_id or len(args.date_id) < 1:
        parser.error("date_id required for recommend and record")
    date_id = args.date_id[0]

    if args.action == "recommend":
        out = recommend(
            date_id,
            journal_dir=args.journal_dir,
            narrations_dir=args.narrations_dir,
            state_path=args.state,
            n=args.n,
            embeddings_path=args.embeddings,
        )
        print(json.dumps(out, indent=2))
    else:
        record_narration(
            date_id,
            narrations_dir=args.narrations_dir,
            state_path=args.state,
            journal_dir=args.journal_dir,
            max_entries=args.n,
            embeddings_path=args.embeddings,
        )
        print(f"Recorded narration for {date_id} in {args.state}")


if __name__ == "__main__":
    main()
