"""Tests for pipeline.social_metadata.social_caption."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.social_metadata import SOCIAL_HASHTAGS, social_caption


def test_social_caption_includes_title_question_and_hashtags(tmp_path: Path):
    date_id = "18040702"
    (tmp_path / "narrations").mkdir()
    (tmp_path / "narrations" / f"narration{date_id}.json").write_text(
        json.dumps({"title": "Navigating Swift Currents"}), encoding="utf-8"
    )
    caption = social_caption(date_id, repo_root=tmp_path)
    assert "Navigating Swift Currents - July 02, 1804" in caption
    assert "Which expedition member" in caption
    assert SOCIAL_HASHTAGS in caption


def test_social_caption_falls_back_without_narration(tmp_path: Path):
    caption = social_caption("18040702", repo_root=tmp_path)
    assert "Lewis & Clark Expedition: July 02, 1804" in caption
