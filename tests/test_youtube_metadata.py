"""pipeline.youtube_metadata — shared episode title + description."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.youtube_metadata import (
    build_youtube_description,
    ensure_shorts_hashtag,
    episode_title_date,
    youtube_title_and_description,
)


def test_episode_title_date():
    assert episode_title_date("18040830") == "August 30, 1804"
    assert episode_title_date("not-a-date") == "not-a-date"
    assert episode_title_date("18049999") == "1804-99-99"  # invalid month/day -> dashed raw


def test_ensure_shorts_hashtag_idempotent():
    assert ensure_shorts_hashtag("Hello") == "Hello\n\n#Shorts"
    assert ensure_shorts_hashtag("done #SHORTS") == "done #SHORTS"
    assert ensure_shorts_hashtag("") == "#Shorts"


def test_build_description_mentions_date_and_shorts():
    d = build_youtube_description("August 30, 1804")
    assert "August 30, 1804" in d
    assert d.rstrip().endswith("#Shorts")


def test_title_uses_narration_title_when_present(tmp_path: Path):
    (tmp_path / "narrations").mkdir()
    (tmp_path / "narrations" / "narration18040830.json").write_text(
        json.dumps({"title": "The Yellowstone Fork"}), encoding="utf-8"
    )
    title, desc = youtube_title_and_description("18040830", repo_root=tmp_path)
    assert title == "The Yellowstone Fork - August 30, 1804"
    assert "August 30, 1804" in desc


def test_title_falls_back_without_narration(tmp_path: Path):
    title, desc = youtube_title_and_description("18040830", repo_root=tmp_path)
    assert title == "Lewis & Clark Expedition: August 30, 1804"
    assert desc


def test_blank_narration_title_falls_back(tmp_path: Path):
    (tmp_path / "narrations").mkdir()
    (tmp_path / "narrations" / "narration18040830.json").write_text(
        json.dumps({"title": "   "}), encoding="utf-8"
    )
    title, _ = youtube_title_and_description("18040830", repo_root=tmp_path)
    assert title == "Lewis & Clark Expedition: August 30, 1804"


def test_non_date_id_returns_generic(tmp_path: Path):
    assert youtube_title_and_description("bogus", repo_root=tmp_path) == (
        "Lewis & Clark Expedition",
        "",
    )
