"""Shared YouTube episode metadata (title + description).

Used by ``run-daily.py`` (upload step) and ``pipeline_ui/server.py``
(upload-from-preview). Pure text helpers only — no google API imports — so both
entry points build identical metadata from one place.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

SHORTS_HASHTAG = "#Shorts"

DESCRIPTION_ENGAGEMENT_QUESTION = (
    "Which expedition member would you have wanted on your boat crew? Tell us in the comments."
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ensure_shorts_hashtag(text: str) -> str:
    """Append #Shorts when missing (case-insensitive)."""
    body = (text or "").rstrip()
    if SHORTS_HASHTAG.lower() in body.lower():
        return body
    if not body:
        return SHORTS_HASHTAG
    return f"{body}\n\n{SHORTS_HASHTAG}"


def build_youtube_description(title_date: str) -> str:
    """Standard YouTube description for a journal episode (run-daily, Pipeline UI)."""
    return ensure_shorts_hashtag(
        f"Journal entry from the Lewis and Clark Expedition, {title_date}.\n\n"
        "Scripted by AI and Generated from the Lewis & Clark journal archive.\n\n"
        f"{DESCRIPTION_ENGAGEMENT_QUESTION}\n"
    )


def episode_title_date(date_id: str) -> str:
    """``"August 30, 1804"`` from ``"18040830"``; falls back to the raw string on a bad date."""
    ds = (
        f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:8]}"
        if re.fullmatch(r"\d{8}", date_id)
        else date_id
    )
    try:
        return datetime.strptime(ds, "%Y-%m-%d").strftime("%B %d, %Y")
    except ValueError:
        return ds


def youtube_title_and_description(
    date_id: str, *, repo_root: Path | None = None
) -> tuple[str, str]:
    """``(title, description)`` for a journal ``date_id``.

    Title is ``"<episode title> - <Month DD, YYYY>"`` when
    ``narrations/narration<date_id>.json`` has a ``title``, else
    ``"Lewis & Clark Expedition: <Month DD, YYYY>"``.
    """
    if not re.fullmatch(r"\d{8}", date_id):
        return "Lewis & Clark Expedition", ""
    root = Path(repo_root) if repo_root is not None else _repo_root()
    title_date = episode_title_date(date_id)
    narration_path = root / "narrations" / f"narration{date_id}.json"
    title: str | None = None
    if narration_path.is_file():
        try:
            data = json.loads(narration_path.read_text(encoding="utf-8"))
            t = (data.get("title") or "").strip()
            if isinstance(t, str) and t:
                title = f"{t} - {title_date}"
        except (json.JSONDecodeError, OSError):
            pass
    if not title:
        title = f"Lewis & Clark Expedition: {title_date}"
    return title, build_youtube_description(title_date)
