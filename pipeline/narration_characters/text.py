"""Text normalization and date parsing for character matching."""

from __future__ import annotations

import re
from datetime import date


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def tokenize_words(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z]+", text.lower())


def parse_iso_date(s: str) -> date:
    year, month, day = (int(s[0:4]), int(s[5:7]), int(s[8:10]))
    return date(year, month, day)


def parse_date_id(date_id: str) -> date | None:
    if len(date_id) != 8 or not date_id.isdigit():
        return None
    return date(int(date_id[0:4]), int(date_id[4:6]), int(date_id[6:8]))
