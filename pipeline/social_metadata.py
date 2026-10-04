"""Shared caption builder for cross-posting Shorts to Instagram Reels.

Pure text helpers only — no platform SDK imports — reusing the same episode
title/date lookup as YouTube uploads (pipeline/youtube_metadata.py) so both platforms describe the same episode consistently.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.youtube_metadata import (
    DESCRIPTION_ENGAGEMENT_QUESTION,
    youtube_title_and_description,
)

SOCIAL_HASHTAGS = "#LewisAndClark #History #shorts"

# Instagram's media caption is capped at 2200 UTF-16 code units; stay well under that so truncation is never needed.
MAX_CAPTION_LEN = 2000


def social_caption(date_id: str, *, repo_root: Path | None = None) -> str:
    """Short caption for Instagram Reels: episode title + engagement
    question + hashtags. Truncated to MAX_CAPTION_LEN as a safety net."""
    title, _ = youtube_title_and_description(date_id, repo_root=repo_root)
    caption = f"{title}\n\n{DESCRIPTION_ENGAGEMENT_QUESTION}\n\n{SOCIAL_HASHTAGS}"
    return caption[:MAX_CAPTION_LEN]
