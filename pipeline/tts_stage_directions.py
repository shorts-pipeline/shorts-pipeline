"""Strip non-spoken stage directions from dialogue and map them to silence gaps for TTS."""

from __future__ import annotations

import re

# Silence inserted after a removed stage-direction beat (seconds).
DEFAULT_STAGE_PAUSE_SEC = 0.75

_PAREN_BLOCK = re.compile(r"\s*\(([^)]*)\)\s*")

_STAGE_SENTENCE = re.compile(
    r"^(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+)?(?:"
    r"pauses?(?:\s+for\s+(?:a\s+)?(?:moment|beat|second|seconds|while))?"
    r"|pauses?\s+briefly"
    r"|stands?\s+quietly"
    r"|waits?(?:\s+(?:for\s+)?(?:a\s+)?moment|\s+briefly)?"
    r"|holds?\s+(?:silent|still)"
    r"|(?:remains?|is)\s+silent"
    r"|(?:a\s+)?brief\s+(?:pause|beat|silence)"
    r"|quiet\s+beat"
    r"|beat\s+of\s+silence"
    r")\s*[.!?]?\s*$",
    re.IGNORECASE,
)

_PAUSE_HINT_WORDS = frozenset(
    {"pause", "beat", "silence", "quiet", "wait", "moment", "still", "hold"}
)


def _paren_implies_pause(inner: str) -> bool:
    t = (inner or "").lower()
    return any(w in t for w in _PAUSE_HINT_WORDS)


def _is_stage_direction_sentence(sentence: str) -> bool:
    s = (sentence or "").strip()
    if not s:
        return False
    if _STAGE_SENTENCE.match(s):
        return True
    # Short clause with no verb other than stage verbs
    low = s.lower().rstrip(".!?")
    if low in (
        "pause",
        "a pause",
        "brief pause",
        "beat",
        "silence",
        "quiet",
    ):
        return True
    return False


def _strip_inline_parentheticals(text: str, *, pause_sec: float) -> tuple[str, float]:
    """Remove (pauses) / (beat) parentheticals; accumulate pause duration."""
    work = text
    extra_pause = 0.0
    while True:
        m = _PAREN_BLOCK.search(work)
        if not m:
            break
        inner = m.group(1)
        before = work[: m.start()].rstrip()
        after = work[m.end() :].lstrip()
        if _paren_implies_pause(inner):
            extra_pause = max(extra_pause, pause_sec)
        work = f"{before} {after}".strip() if before and after else (before or after)
    return work, extra_pause


def split_dialogue_for_tts(
    text: str,
    *,
    stage_pause_sec: float = DEFAULT_STAGE_PAUSE_SEC,
) -> list[tuple[str, float]]:
    """
    Split one dialogue line into TTS chunks: ``(spoken_text, pause_after_seconds)``.

    Stage-direction sentences/clauses are removed; ``pause_after`` on the preceding chunk
    (or a silence-only chunk) carries the beat so lip-sync audio matches quiet moments.
    """
    raw = (text or "").strip()
    if not raw:
        return []

    work, paren_pause = _strip_inline_parentheticals(raw, pause_sec=stage_pause_sec)
    if not work:
        if paren_pause > 0:
            return [("", paren_pause)]
        return []

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", work) if s.strip()]
    if not sentences:
        sentences = [work]

    out: list[tuple[str, float]] = []
    speech_parts: list[str] = []
    pending_pause = paren_pause

    def flush_speech() -> None:
        nonlocal speech_parts, pending_pause
        if not speech_parts:
            return
        spoken = " ".join(speech_parts).strip()
        speech_parts.clear()
        if spoken:
            out.append((spoken, pending_pause))
            pending_pause = 0.0

    for sent in sentences:
        if _is_stage_direction_sentence(sent):
            flush_speech()
            pending_pause = max(pending_pause, stage_pause_sec)
        else:
            speech_parts.append(sent)

    flush_speech()
    if pending_pause > 0:
        out.append(("", pending_pause))

    return [(t, p) for t, p in out if t.strip() or p > 0]


def spoken_text_only_for_tts(text: str) -> str:
    """Join spoken chunks with no pause metadata (for previews / single-shot TTS)."""
    parts = [t for t, _ in split_dialogue_for_tts(text) if t.strip()]
    return " ".join(parts).strip()
