"""Tests for pipeline.youtube_srt and youtube_upload metadata helpers."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.youtube_srt import (
    build_srt_cues,
    caption_text_for_segment,
    format_srt_timestamp,
    intro_caption_text,
    render_srt,
)
from youtube_upload import (
    DEFAULT_CATEGORY_ID,
    _missing_oauth_scopes,
    _oauth_scopes,
    _stored_token_scopes,
    build_video_insert_body,
    ensure_shorts_hashtag,
)


def test_format_srt_timestamp():
    assert format_srt_timestamp(0) == "00:00:00,000"
    assert format_srt_timestamp(7.464) == "00:00:07,464"
    assert format_srt_timestamp(65.5) == "00:01:05,500"


def test_intro_caption_text():
    assert intro_caption_text("18040702") == "July 02, 1804"


def test_caption_text_talking_head_uses_dialogue_only():
    seg = {
        "visual_mode": "talking_head",
        "narration": "Clark reflects on the current.",
        "dialogue": [{"speaker_id": "clark", "text": "This river is choked with drift."}],
    }
    assert caption_text_for_segment(seg) == "This river is choked with drift."


def test_caption_text_b_roll_with_dialogue_includes_narration():
    seg = {
        "visual_mode": "b_roll",
        "narration": "They pushed upstream.",
        "dialogue": [{"speaker_id": "lewis", "text": "Keep the line taut."}],
    }
    assert caption_text_for_segment(seg) == "They pushed upstream. Keep the line taut."


def test_build_srt_cues_with_intro(tmp_path: Path):
    date_id = "18040702"
    (tmp_path / "audio" / date_id).mkdir(parents=True)
    (tmp_path / "narrations").mkdir()
    durations = [
        {"file": "intro", "duration": 2.0},
        {"file": "segments/01.mp3", "duration": 3.0},
        {"file": "segments/02.mp3", "duration": 4.0},
    ]
    (tmp_path / "audio" / date_id / "durations.json").write_text(
        json.dumps(durations), encoding="utf-8"
    )
    narration = {
        "narration_script": [
            {
                "segment_index": 1,
                "visual_mode": "b_roll",
                "narration": "First beat.",
            },
            {
                "segment_index": 2,
                "visual_mode": "talking_head",
                "narration": "Ignored for captions.",
                "dialogue": [{"speaker_id": "clark", "text": "Second beat spoken."}],
            },
        ]
    }
    (tmp_path / "narrations" / f"narration{date_id}.json").write_text(
        json.dumps(narration), encoding="utf-8"
    )
    cues = build_srt_cues(date_id, repo_root=tmp_path)
    assert cues[0] == (0.0, 2.0, "July 02, 1804")
    assert cues[1] == (2.0, 5.0, "First beat.")
    assert cues[2] == (5.0, 9.0, "Second beat spoken.")
    srt = render_srt(cues)
    assert "1\n00:00:00,000 --> 00:00:02,000\nJuly 02, 1804" in srt
    assert "#Shorts" not in srt


def test_ensure_shorts_hashtag():
    assert ensure_shorts_hashtag("Hello") == "Hello\n\n#Shorts"
    assert ensure_shorts_hashtag("Hello #shorts already") == "Hello #shorts already"


def test_missing_oauth_scopes_detects_stale_token():
    required = _oauth_scopes()
    stored = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
    ]
    missing = _missing_oauth_scopes(stored, required)
    assert "https://www.googleapis.com/auth/youtube.force-ssl" in missing
    assert _missing_oauth_scopes(stored, required) == missing
    assert _missing_oauth_scopes(required, required) == []


def test_stored_token_scopes_reads_token_json(tmp_path: Path):
    token_path = tmp_path / "token.json"
    token_path.write_text(
        json.dumps(
            {
                "scopes": [
                    "https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube",
                ]
            }
        ),
        encoding="utf-8",
    )
    assert _stored_token_scopes(token_path) == [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
    ]
    assert _stored_token_scopes(tmp_path / "missing.json") is None


def test_build_video_insert_body_defaults():
    body = build_video_insert_body(
        title="Navigating Swift Currents - July 02, 1804",
        description="Journal entry.",
        tags=["history"],
        category_id=DEFAULT_CATEGORY_ID,
        privacy="public",
        contains_synthetic_media=True,
    )
    assert body["snippet"]["categoryId"] == "27"
    assert body["snippet"]["defaultLanguage"] == "en"
    assert body["snippet"]["defaultAudioLanguage"] == "en"
    assert "#Shorts" in body["snippet"]["description"]
    assert body["status"]["containsSyntheticMedia"] is True
    assert body["status"]["selfDeclaredMadeForKids"] is False
