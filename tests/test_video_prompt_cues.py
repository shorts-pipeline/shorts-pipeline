"""Tests for config-driven conditional video_prompt / opening_frame append cues."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.narration_common import apply_prompt_replacements
from pipeline.video_prompt_cues import (
    VideoPromptCueConfig,
    apply_video_prompt_cues,
    apply_video_prompt_cues_to_segment,
    load_video_prompt_cues_config,
)


def _firewood_cue_config() -> VideoPromptCueConfig:
    cfg = load_video_prompt_cues_config(
        Path(__file__).resolve().parent.parent / "config" / "video_prompt_cues.json"
    )
    cues = [c for c in cfg.cues if c.id == "period_firewood"]
    assert len(cues) == 1
    return VideoPromptCueConfig(enabled=True, cues=tuple(cues))


def test_load_default_config_has_period_firewood():
    cfg = load_video_prompt_cues_config()
    assert cfg.enabled
    ids = [c.id for c in cfg.cues]
    assert "period_firewood" in ids
    assert "weapon_empty_hands_default" not in ids
    assert "animal_focal_guard" not in ids


def test_disabled_weapon_cues_not_in_default_config():
    cfg = load_video_prompt_cues_config()
    ids = {c.id for c in cfg.cues}
    assert "weapon_empty_hands_default" not in ids
    assert "weapon_no_firearms" not in ids
    assert "animal_focal_guard" not in ids


def test_appends_when_narration_mentions_campfire():
    cue = _firewood_cue_config()
    seg = {
        "segment_index": 3,
        "narration": "They gathered at the campfire after sundown.",
        "video_prompt": "Wide shot of men seated near glowing coals, canvas tents behind.",
    }
    applied = apply_video_prompt_cues_to_segment(seg, cue.cues)
    assert applied == ["period_firewood"]
    assert "rough-hewn" in seg["video_prompt"].lower()
    assert "axe-cut" in seg["video_prompt"].lower()


def test_skips_when_prompt_already_period_wood():
    cue = _firewood_cue_config()
    seg = {
        "narration": "Firewood was stacked by the fire ring.",
        "video_prompt": "Close view of rough-hewn, axe-cut fuelwood beside the fire ring.",
    }
    applied = apply_video_prompt_cues_to_segment(seg, cue.cues)
    assert applied == []


def test_no_append_without_trigger():
    cue = _firewood_cue_config()
    seg = {
        "narration": "The party rowed upstream against a stiff breeze.",
        "video_prompt": "Keelboat laboring in chop, cordelle line taut.",
    }
    applied = apply_video_prompt_cues_to_segment(seg, cue.cues)
    assert applied == []


def test_appends_to_opening_frame_when_present():
    cue = _firewood_cue_config()
    seg = {
        "narration": "Clark spoke by the firelight.",
        "opening_frame": "Head-and-shoulders portrait before speech; firelight on face.",
        "video_prompt": "Tight portrait, firelit face.",
    }
    applied = apply_video_prompt_cues_to_segment(seg, cue.cues)
    assert applied == ["period_firewood"]
    assert "rough-hewn" in seg["opening_frame"].lower()
    assert "rough-hewn" in seg["video_prompt"].lower()


def test_apply_prompt_replacements_runs_cues(tmp_path: Path):
    cfg_path = tmp_path / "cues.json"
    cfg_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "cues": [
                    {
                        "id": "test_cue",
                        "trigger_pattern": "(?i)campfire",
                        "append": "Period timber only.",
                        "append_fields": ["video_prompt"],
                        "scan_fields": ["narration"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    merged = {
        "narration_script": [
            {
                "segment_index": 1,
                "narration": "A campfire burned low.",
                "video_prompt": "Men at camp.",
            }
        ]
    }
    log = apply_prompt_replacements(merged, [], [], video_prompt_cues_path=cfg_path)
    assert log == {"1": ["test_cue"]}
    assert "Period timber only" in merged["narration_script"][0]["video_prompt"]


def test_env_disable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LEWISCLARK_VIDEO_PROMPT_CUES", "0")
    merged = {
        "narration_script": [
            {
                "segment_index": 1,
                "narration": "Campfire and firewood.",
                "video_prompt": "Fire scene.",
            }
        ]
    }
    log = apply_video_prompt_cues(merged, _firewood_cue_config())
    assert log == {}
    assert "rough-hewn" not in merged["narration_script"][0]["video_prompt"].lower()


def test_invalid_trigger_pattern_raises(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "cues": [
                    {
                        "id": "x",
                        "trigger_pattern": "[",
                        "append": "nope",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid trigger_pattern"):
        load_video_prompt_cues_config(bad)
