"""Tests for removing narration_script rows via Pipeline UI update endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_ui.server import (
    narration_text_requests_segment_delete,
    narration_update_narration_http,
    narration_update_opening_frame_http,
    narration_update_talking_head_prompt_http,
    narration_update_video_prompt_http,
)


@pytest.fixture
def repo_three_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    (repo / "narrations").mkdir(parents=True)
    narr = {
        "title": "Three segments",
        "narration_script": [
            {"narration": "One", "video_prompt": "vp1"},
            {"narration": "Two", "video_prompt": "vp2"},
            {"narration": "Three", "video_prompt": "vp3"},
        ],
    }
    (repo / "narrations" / "narration18040531.json").write_text(json.dumps(narr), encoding="utf-8")
    import pipeline_ui.server as srv

    monkeypatch.setattr(srv, "_REPO_ROOT", repo)
    return repo


def test_narration_text_requests_segment_delete() -> None:
    assert narration_text_requests_segment_delete("") is False
    assert narration_text_requests_segment_delete("   ") is False
    assert narration_text_requests_segment_delete("DELETEME") is True
    assert narration_text_requests_segment_delete("deleteme") is True
    assert narration_text_requests_segment_delete("keep") is False


def test_delete_middle_segment_via_deleteme_narration(repo_three_segments: Path) -> None:
    del repo_three_segments
    code, out = narration_update_narration_http(
        {"journal_date": "1804-05-31", "segment_index": 2, "narration": "DELETEME"}
    )
    assert code == 200
    assert out.get("deleted") is True
    assert out.get("segment_count") == 2
    path = Path("narrations/narration18040531.json")
    import pipeline_ui.server as srv

    data = json.loads((srv._REPO_ROOT / path).read_text(encoding="utf-8"))
    script = data["narration_script"]
    assert len(script) == 2
    assert script[0]["narration"] == "One"
    assert script[1]["narration"] == "Three"
    assert script[0]["segment_index"] == 1
    assert script[1]["segment_index"] == 2


def test_delete_via_deleteme_in_video_prompt(repo_three_segments: Path) -> None:
    del repo_three_segments
    code, out = narration_update_video_prompt_http(
        {"journal_date": "1804-05-31", "segment_index": 3, "video_prompt": "DELETEME"}
    )
    assert code == 200
    assert out.get("deleted") is True
    import pipeline_ui.server as srv

    data = json.loads(
        (srv._REPO_ROOT / "narrations/narration18040531.json").read_text(encoding="utf-8")
    )
    assert len(data["narration_script"]) == 2


def test_update_talking_head_prompt(repo_three_segments: Path) -> None:
    del repo_three_segments
    import pipeline_ui.server as srv

    path = srv._REPO_ROOT / "narrations/narration18040531.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["narration_script"][1]["visual_mode"] = "talking_head"
    data["narration_script"][1]["talking_head_subject"] = "lewis"
    path.write_text(json.dumps(data), encoding="utf-8")

    code, out = narration_update_talking_head_prompt_http(
        {
            "journal_date": "1804-05-31",
            "segment_index": 2,
            "talking_head_prompt": "Speaks calmly toward off-camera partner.",
        }
    )
    assert code == 200, out
    assert out.get("ok") is True
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["narration_script"][1]["talking_head_prompt"].startswith("Speaks calmly")


def test_update_talking_head_prompt_rejects_b_roll(repo_three_segments: Path) -> None:
    del repo_three_segments
    code, out = narration_update_talking_head_prompt_http(
        {
            "journal_date": "1804-05-31",
            "segment_index": 1,
            "talking_head_prompt": "Nope.",
        }
    )
    assert code == 400
    assert out.get("error") == "not_talking_head_segment"


def test_update_opening_frame(repo_three_segments: Path) -> None:
    del repo_three_segments
    import pipeline_ui.server as srv

    path = srv._REPO_ROOT / "narrations/narration18040531.json"
    code, out = narration_update_opening_frame_http(
        {
            "journal_date": "1804-05-31",
            "segment_index": 2,
            "opening_frame": "Clark on a log at a makeshift camp table outdoors.",
        }
    )
    assert code == 200, out
    assert out.get("ok") is True
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "makeshift camp" in saved["narration_script"][1]["opening_frame"]

    code, out = narration_update_opening_frame_http(
        {
            "journal_date": "1804-05-31",
            "segment_index": 2,
            "opening_frame": "   ",
        }
    )
    assert code == 200, out
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "opening_frame" not in saved["narration_script"][1]


def test_cannot_delete_last_segment(repo_three_segments: Path) -> None:
    del repo_three_segments
    for seg_i in (3, 2):
        code, out = narration_update_narration_http(
            {"journal_date": "1804-05-31", "segment_index": seg_i, "narration": "DELETEME"}
        )
        assert code == 200, (seg_i, out)
    code, out = narration_update_narration_http(
        {"journal_date": "1804-05-31", "segment_index": 1, "narration": "DELETEME"}
    )
    assert code == 400
    assert out.get("error") == "cannot_delete_last_segment"
