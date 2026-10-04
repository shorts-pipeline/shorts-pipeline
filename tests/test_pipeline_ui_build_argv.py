"""Tests for pipeline.ui_argv.build_argv dialogue / long-conversation flags."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pipeline.ui_argv import (
    _user_explicit_dialogue_opt_out,
    _user_explicit_long_conversation_opt_out,
    _week_arc_defers_mode_flags,
    boolish,
    build_argv,
)
from pipeline_ui.server import load_options

PY = sys.executable


def _argv(vals: dict, spec: dict, workflow: str, repo_root: Path) -> list[str]:
    return build_argv(vals, spec, workflow, repo_root=repo_root, python_executable=PY)


def _dialogue_flags(cmd: list[str]) -> tuple[bool, bool]:
    return "--dialogue" in cmd, "--no-dialogue" in cmd


def _seed_repo(tmp_path: Path, narration_by_id: dict[str, dict] | None = None) -> Path:
    (tmp_path / "run-daily.py").write_text("", encoding="utf-8")
    narr_dir = tmp_path / "narrations"
    narr_dir.mkdir(exist_ok=True)
    for did, doc in (narration_by_id or {}).items():
        (narr_dir / f"narration{did}.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path


@pytest.fixture
def spec():
    s = load_options()
    if s.get("error"):
        pytest.skip(s["error"])
    return s


def test_explicit_no_dialogue_never_pairs_with_dialogue(spec, tmp_path):
    """Regenerating with dialogue off must not pass both CLI flags."""
    _seed_repo(
        tmp_path,
        {
            "18050615": {
                "dialogue_mode": True,
                "narration_script": [
                    {"text": "n", "dialogue": [{"speaker_id": "lewis", "text": "hi"}]}
                ],
            }
        },
    )
    vals = {
        "date": "1805-06-15",
        "dialogue_mode": False,
        "long_conversation_mode": False,
        "skip_existing": False,
    }
    cmd = _argv(vals, spec, "narration", tmp_path)
    has_d, has_nd = _dialogue_flags(cmd)
    assert has_nd
    assert not has_d


@pytest.mark.parametrize("dialogue_off", [False, 0, "false", "0"])
def test_falsy_dialogue_opt_out_treated_like_false(spec, tmp_path, dialogue_off):
    _seed_repo(tmp_path, {"18050615": {"dialogue_mode": True, "narration_script": []}})
    cmd = _argv(
        {
            "date": "1805-06-15",
            "dialogue_mode": dialogue_off,
            "long_conversation_mode": False,
            "skip_existing": False,
        },
        spec,
        "narration",
        tmp_path,
    )
    has_d, has_nd = _dialogue_flags(cmd)
    assert has_nd
    assert not has_d


def test_journal_week_arc_flags_on_narration_workflow(spec, tmp_path):
    _seed_repo(tmp_path)
    cmd = _argv(
        {
            "date": "1804-07-05",
            "use_week_arc": True,
            "refresh_week_arc": True,
            "skip_existing": True,
        },
        spec,
        "narration",
        tmp_path,
    )
    assert "--use-week-arc" in cmd
    assert "--refresh-week-arc" in cmd
    assert "--narration-only" in cmd


def test_video_promotes_dialogue_from_narration_json(spec, tmp_path):
    """Video tab must pass --dialogue when narration JSON is dialogue (e.g. week-arc day)."""
    _seed_repo(
        tmp_path,
        {
            "18040706": {
                "dialogue_mode": True,
                "long_conversation_mode": False,
                "narration_script": [
                    {"text": "n", "dialogue": [{"speaker_id": "lewis", "text": "hi"}]}
                ],
            }
        },
    )
    # Mimic Video form: no dialogue_mode key (unchecked checkbox may be omitted or false default).
    cmd = _argv(
        {
            "date": "1804-07-06",
            "vendor": "fal",
            "tts": "openai",
            "skip_existing": True,
            "long_conversation_mode": False,
            "ambient": True,
        },
        spec,
        "video",
        tmp_path,
    )
    has_d, has_nd = _dialogue_flags(cmd)
    assert has_d
    assert not has_nd
    assert "--no-long-conversation" not in cmd


def test_week_arc_defers_no_dialogue_when_checkboxes_unchecked(spec, tmp_path):
    """Journal + week arc + unchecked dialogue must not force --no-dialogue."""
    _seed_repo(tmp_path)
    cmd = _argv(
        {
            "date": "1804-07-06",
            "use_week_arc": True,
            "dialogue_mode": False,
            "long_conversation_mode": False,
            "skip_existing": True,
        },
        spec,
        "narration",
        tmp_path,
    )
    has_d, has_nd = _dialogue_flags(cmd)
    assert "--use-week-arc" in cmd
    assert not has_nd
    assert not has_d
    assert "--no-long-conversation" not in cmd
    assert "--narration-only" in cmd


def test_script_not_found_raises(spec, tmp_path):
    with pytest.raises(FileNotFoundError):
        _argv({"date": "1804-07-06"}, spec, "narration", tmp_path)


# --- inference helpers (direct, no spec) --------------------------------------


def test_boolish_variants():
    assert boolish(True) is True
    assert boolish("yes") is True
    assert boolish("0") is False
    assert boolish(None, default=True) is True


def test_week_arc_defers_only_when_both_checkboxes_unchecked():
    assert _week_arc_defers_mode_flags({"use_week_arc": True})
    assert not _week_arc_defers_mode_flags({"use_week_arc": False})
    assert not _week_arc_defers_mode_flags({"use_week_arc": True, "dialogue_mode": True})
    assert not _week_arc_defers_mode_flags({"use_week_arc": True, "long_conversation_mode": True})


def test_explicit_opt_out_helpers_respect_week_arc_defer():
    # key present + off => opt-out, unless week arc defers
    assert _user_explicit_dialogue_opt_out({"dialogue_mode": False})
    assert not _user_explicit_dialogue_opt_out({})
    assert not _user_explicit_dialogue_opt_out({"dialogue_mode": False, "use_week_arc": True})
    assert _user_explicit_long_conversation_opt_out({"long_conversation_mode": False})
    assert not _user_explicit_long_conversation_opt_out(
        {"long_conversation_mode": False, "use_week_arc": True}
    )
