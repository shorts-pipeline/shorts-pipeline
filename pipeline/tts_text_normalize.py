"""Spoken-text tweaks before TTS (OpenAI, FAL MiniMax, pyttsx3)."""

from __future__ import annotations

import re

# "St." is often read as "Street" by TTS; expand to "Saint" for place-style names.
# Match: St. optional space + word starting with a letter (St. Charles, St.Louis).
_ST_DOT_PLACE = re.compile(r"\bSt\.\s*([A-Za-z][\w'-]*)\b", re.IGNORECASE)
# St Charles / St Louis (no period after St)
_ST_SPACE_PLACE = re.compile(r"\bSt\s+([A-Za-z][\w'-]*)\b", re.IGNORECASE)

# If "St." is really end-of-line / end-of-street and the next token starts a new sentence, skip.
_SKIP_NAME_LOWER = frozenset(
    """
    a an and are as at be been but by can could did do does for from go had has have
    he her him his how i if in into is it its just let like may me might more most much
    my new next no nor not now of off on once only or our out said say she should so
    some such than that the their them then there these they this those though through
    thus to too two up upon us very was we were what when where which who whom why will
    with within without would you your
    """.split()
)


def normalize_text_for_tts(text: str) -> str:
    if not text:
        return text

    def _repl_st_dot(m: re.Match[str]) -> str:
        name = m.group(1)
        if name.lower() in _SKIP_NAME_LOWER:
            return m.group(0)
        return f"Saint {name}"

    def _repl_st_space(m: re.Match[str]) -> str:
        name = m.group(1)
        if name.lower() in _SKIP_NAME_LOWER:
            return m.group(0)
        return f"Saint {name}"

    out = _ST_DOT_PLACE.sub(_repl_st_dot, text)
    out = _ST_SPACE_PLACE.sub(_repl_st_space, out)
    return out
