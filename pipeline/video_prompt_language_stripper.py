"""
Deterministic post-Phase-2 cleanup of unrenderable language in FAL/Wan ``video_prompt`` text:
sound/abstraction language, and cross-segment "the same X" continuity callbacks.

Phase 2 rule 2b/2c (SILENT video_prompt) already forbids sound-as-heard language ("echoes",
"crackles") and invisible internal states ("thick with anticipation", "heightening their
alertness"), but the model emits it anyway often enough that it's worth a mechanical net on
top of the prompt rule. See ai-plans/fal-video-quality-followups-2026-09.md item 6.

Separately, the CHARACTER CONSISTENCY rule tells the model to keep wardrobe/build/props
"consistent with prior segments" — the model complies by writing referential text like "in the
same dark blue field coat". But each segment is synthesized as an independent FAL prompt with no
memory of any other call, so "same" has no referent the vendor can resolve; it just burns budget.
Actual cross-segment consistency comes from the reference-image mechanism (character portraits,
scene anchors), not the text.

Design constraints (see the same plan item for the reasoning):

- **Best-effort, never a hard gate.** A pattern that doesn't match a given prompt is a silent
  no-op — identical to today's behavior, never worse. This module never raises and never blocks
  Phase 2 / the pipeline; it is not wired into ``_validate_phase2``.
- **Never produce a grammatical fragment.** Every transformation is either (a) a same-part-of-
  speech word/phrase swap (safe by construction), or (b) a removal bounded by existing sentence
  punctuation (a trailing comma-clause up to the sentence's own terminal punctuation, or a whole
  standalone sentence from one sentence boundary to the next) — never a mid-clause deletion that
  could leave a dangling fragment like "The air is  as they discuss threats."
- **Narrow scope on purpose.** This does not attempt to rewrite a banned phrase embedded
  mid-sentence alongside real visible content (e.g. "The crew huddles low, tense with dread,
  watching the ridge line."). Safely repairing that needs real rewriting, not regex; it's left to
  rule 2b at generation time. Expect this to catch the common, recurring phrasings from the
  original audit, not every possible violation.
"""

from __future__ import annotations

import re

# Sound-as-heard words -> a grounded, visible-adjacent near-synonym. Same tense/part of speech as
# the banned word, so the substitution always stays grammatical.
_SOUND_WORD_SWAPS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), repl)
    for pat, repl in (
        (r"\bechoing\b", "carrying"),
        (r"\bechoes\b", "carries"),
        (r"\brumbling\b", "rolling"),
        (r"\brumbles\b", "rolls"),
        (r"\bcrackling\b", "flickering"),
        (r"\bcrackles\b", "flickers"),
        (r"\bbuzzing\b", "flitting"),
        (r"\bbuzzes\b", "flits"),
        (r"\bdroning\b", "hovering"),
        (r"\bdrones\b", "hovers"),
        (r"\baudible\b", "visible"),
    )
)

# "The same X" cross-segment continuity callbacks (Phase 2 rule: keep wardrobe/build/props
# "consistent with prior segments"). Each segment is a standalone FAL prompt with no memory of any
# other call, so "same" has no referent the vendor can resolve -- actual continuity comes from the
# reference-image mechanism (character portraits, scene anchors), not this text. Dropping just
# "same" and keeping the determiner is always grammatical and never leaves a fragment.
_SAME_REFERENT_SWAPS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(the) same\b", re.IGNORECASE), r"\1"),
)

# Invisible-state adjectives -> a physical/visual near-equivalent. Same word class, drop-in safe.
_INVISIBLE_STATE_WORD_SWAPS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), repl)
    for pat, repl in (
        (r"\btense\b", "taut"),
        (r"\banxiously\b", "watchfully"),
        (r"\banxious\b", "watchful"),
        (r"\buneasy\b", "wary"),
        (r"\bapprehensively\b", "watchfully"),
        (r"\bapprehensive\b", "watchful"),
    )
)

# "Tell not show" gerund clauses that only narrate meaning/mood rather than describe anything a
# camera can film. Always introduced by a comma and always runs to the sentence's own terminal
# punctuation, so removing the whole span never leaves a fragment.
_TELEGRAPH_GERUNDS = (
    "demanding",
    "symbolizing",
    "symbolising",
    "signaling",
    "signalling",
    "conveying",
    "suggesting",
    "evoking",
    "heightening",
    "deepening",
    "underscoring",
    "embodying",
    "representing",
    "foreshadowing",
    "emphasizing",
    "emphasising",
    "illustrating",
    "telegraphing",
)
_TRAILING_TELEGRAPH_CLAUSE_RE = re.compile(
    r",\s*(?:" + "|".join(_TELEGRAPH_GERUNDS) + r")\b[^.!?]*(?=[.!?]|$)",
    re.IGNORECASE,
)

# Whole standalone sentences that are entirely atmosphere/internal-state, with no separable
# visible clause worth saving. Matched sentence-boundary to sentence-boundary so dropping them
# never leaves a fragment (a phrase embedded inside a longer sentence with other content is
# deliberately left alone — see module docstring).
_STANDALONE_INVISIBLE_STATE_SENTENCE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in (
        r"(?:(?<=[.!?]\s)|^)[^.!?]{0,90}\b(?:is|are|feels?|seems?)\s+thick with\s+"
        r"\w+(?:\s+\w+){0,3}[^.!?]*(?:[.!?]|$)",
        r"(?:(?<=[.!?]\s)|^)[^.!?]{0,90}\b(?:a\s+)?(?:sense|feeling) of\s+"
        r"\w+(?:\s+\w+){0,3}[^.!?]*(?:[.!?]|$)",
    )
)


def _cleanup_whitespace(text: str) -> str:
    out = re.sub(r"[ \t]{2,}", " ", text)
    out = re.sub(r"\s+([.,!?])", r"\1", out)
    out = re.sub(r"(?:\s*\.){2,}", ".", out)
    return out.strip()


def strip_unrenderable_language(text: str) -> str:
    """Best-effort cleanup of sound/abstraction language rule 2b/2c already forbids.

    Never raises. If the cleaned result is empty but the input wasn't (e.g. a short field that was
    entirely one banned standalone sentence), the original text is returned unchanged rather than
    shipping an empty prompt fragment — losing rule-2b compliance on that one field is preferable
    to losing the field's content outright.
    """
    original = (text or "").strip()
    if not original:
        return original
    out = original
    for pat in _STANDALONE_INVISIBLE_STATE_SENTENCE_PATTERNS:
        out = pat.sub("", out)
    out = _TRAILING_TELEGRAPH_CLAUSE_RE.sub("", out)
    for pat, repl in _SOUND_WORD_SWAPS:
        out = pat.sub(repl, out)
    for pat, repl in _INVISIBLE_STATE_WORD_SWAPS:
        out = pat.sub(repl, out)
    for pat, repl in _SAME_REFERENT_SWAPS:
        out = pat.sub(repl, out)
    out = _cleanup_whitespace(out)
    return out if out else original
