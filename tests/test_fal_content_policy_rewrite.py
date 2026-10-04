"""OpenAI rewrite of FAL-flagged opening_frame / video_prompt."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.fal_content_policy_rewrite import (
    apply_visual_rewrite_to_narration,
    parse_rewrite_reply,
    persist_segment_visual_rewrite,
    rewrite_and_persist_after_fal_policy,
    rewrite_segment_visuals_via_openai,
)


def test_parse_rewrite_reply_extracts_fields() -> None:
    raw = """```json
    {"opening_frame": "Reuben Fields stands by camp with an opaque provisions sack.",
     "video_prompt": "He gestures toward the tents while another hunter sets the sack down."}
    ```"""
    out = parse_rewrite_reply(raw)
    assert "opaque" in out["opening_frame"]
    assert "gestures" in out["video_prompt"]


def test_parse_rewrite_reply_rejects_antler_grip() -> None:
    raw = json.dumps(
        {
            "opening_frame": "Drouillard gripping a deer by its antlers.",
            "video_prompt": "He walks to camp.",
        }
    )
    with pytest.raises(ValueError, match="antler"):
        parse_rewrite_reply(raw)


def test_persist_updates_merged_and_visual_sidecar(tmp_path: Path) -> None:
    date_id = "18040630"
    merged = {
        "narration_script": [
            {
                "segment_index": 1,
                "narration": "keep this spoken line",
                "video_prompt": "old video",
                "opening_frame": "old still",
            }
        ]
    }
    vis = {
        "scene_plan": [
            {
                "segment_index": 1,
                "visual_strategy": {
                    "opening_frame": "old still",
                    "primary_visual": "old video",
                },
            }
        ]
    }
    (tmp_path / f"narration{date_id}.json").write_text(json.dumps(merged), encoding="utf-8")
    (tmp_path / f"narration{date_id}_visual.json").write_text(json.dumps(vis), encoding="utf-8")
    persist_segment_visual_rewrite(
        date_id,
        1,
        opening_frame="Hunter returns with an opaque sack.",
        video_prompt="He sets the sack beside the tent.",
        narrations_dir=tmp_path,
    )
    saved = json.loads((tmp_path / f"narration{date_id}.json").read_text(encoding="utf-8"))
    row = saved["narration_script"][0]
    assert row["narration"] == "keep this spoken line"
    assert row["opening_frame"].startswith("Hunter returns")
    assert "sack" in row["video_prompt"]
    assert row["fal_policy_rewrite"]["reason"] == "content_policy_violation"
    vis_saved = json.loads(
        (tmp_path / f"narration{date_id}_visual.json").read_text(encoding="utf-8")
    )
    vs = vis_saved["scene_plan"][0]["visual_strategy"]
    assert vs["opening_frame"].startswith("Hunter returns")
    assert "sack" in vs["primary_visual"]


def test_rewrite_and_persist_uses_openai_client(tmp_path: Path, monkeypatch) -> None:
    date_id = "18040101"
    (tmp_path / f"narration{date_id}.json").write_text(
        json.dumps(
            {
                "narration_script": [
                    {
                        "segment_index": 5,
                        "narration": "Hunters took nine deer.",
                        "opening_frame": "Kneels beside a freshly hunted deer.",
                        "video_prompt": "Transport of the deer back to camp.",
                        "reference_character_id": "reuben_fields",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    payload = json.dumps(
        {
            "opening_frame": "Reuben Fields stands at camp with an opaque provisions sack.",
            "video_prompt": "He gestures toward the tents as another man sets the sack down.",
        }
    )
    fake = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
                )
            )
        )
    )
    monkeypatch.setattr(
        "pipeline_logging.log_api_call_with_bodies",
        lambda *a, **k: None,
    )
    out = rewrite_and_persist_after_fal_policy(
        date_id,
        5,
        flagged_prompt="Reuben Fields kneels beside a freshly hunted deer",
        error_text="content_policy_violation",
        character_id="reuben_fields",
        narrations_dir=tmp_path,
        client=fake,
    )
    assert out is not None
    assert "opaque" in out["opening_frame"]
    saved = json.loads((tmp_path / f"narration{date_id}.json").read_text(encoding="utf-8"))
    assert saved["narration_script"][0]["narration"] == "Hunters took nine deer."
    assert "opaque" in saved["narration_script"][0]["opening_frame"]


def test_apply_visual_rewrite_sets_metadata() -> None:
    data = {
        "narration_script": [
            {"segment_index": 2, "opening_frame": "a", "video_prompt": "b", "narration": "c"}
        ]
    }
    apply_visual_rewrite_to_narration(data, 2, opening_frame="new still", video_prompt="new motion")
    assert data["narration_script"][0]["opening_frame"] == "new still"


def test_rewrite_segment_visuals_via_openai_parses_client() -> None:
    payload = json.dumps(
        {
            "opening_frame": "Clark stands on the bank with empty hands.",
            "video_prompt": "He scans the far shore.",
        }
    )
    fake = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
                )
            )
        )
    )
    out = rewrite_segment_visuals_via_openai(
        spoken_narration="x",
        opening_frame="old",
        video_prompt="old",
        flagged_prompt="old",
        error_text="content_policy_violation",
        client=fake,
    )
    assert out["opening_frame"].startswith("Clark stands")
