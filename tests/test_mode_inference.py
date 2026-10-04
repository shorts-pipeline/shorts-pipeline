"""pipeline.mode_inference.resolve_run_daily_modes — the run-daily mode decision."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.mode_inference import resolve_run_daily_modes

BASE = dict(
    dialogue_arg=False,
    no_dialogue_arg=False,
    long_conversation_arg=False,
    no_long_conversation_arg=False,
)


def _narr(tmp_path: Path, doc: dict | None) -> Path:
    p = tmp_path / "narration18040528.json"
    if doc is not None:
        p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# --- branch 3: no flags, no week arc -> infer from file ----------------------


def test_no_flags_no_file_is_narrator_only(tmp_path):
    d = resolve_run_daily_modes(**BASE, narration_path=_narr(tmp_path, None))
    assert (d.dialogue_effective, d.long_conversation_effective) == (False, False)


def test_no_flags_file_requests_dialogue(tmp_path):
    d = resolve_run_daily_modes(
        **BASE, narration_path=_narr(tmp_path, {"dialogue_mode": True, "narration_script": []})
    )
    assert d.dialogue_effective is True
    assert d.long_conversation_effective is False


def test_no_flags_file_requests_long_promotes_dialogue(tmp_path):
    d = resolve_run_daily_modes(
        **BASE, narration_path=_narr(tmp_path, {"long_conversation_mode": True})
    )
    assert d.long_conversation_effective is True
    assert d.dialogue_effective is True  # long implies dialogue


# --- branch 2: manual flags -------------------------------------------------


def test_explicit_no_dialogue_wins_over_file(tmp_path):
    d = resolve_run_daily_modes(
        **{**BASE, "no_dialogue_arg": True},
        narration_path=_narr(tmp_path, {"dialogue_mode": True}),
    )
    assert d.dialogue_effective is False
    assert d.long_conversation_effective is False


def test_explicit_long_conversation_forces_both(tmp_path):
    d = resolve_run_daily_modes(
        **{**BASE, "long_conversation_arg": True},
        narration_path=_narr(tmp_path, None),
    )
    assert d.long_conversation_effective is True
    assert d.dialogue_effective is True


def test_manual_dialogue_only_infers_long_from_file(tmp_path):
    # --dialogue set (manual), long unset -> long inferred from file (true here)
    d = resolve_run_daily_modes(
        **{**BASE, "dialogue_arg": True},
        narration_path=_narr(tmp_path, {"long_conversation_mode": True}),
    )
    assert d.long_conversation_effective is True
    assert d.dialogue_effective is True


# --- branch 1: week arc ----------------------------------------------------


def test_week_arc_recommended_mode_wins_and_sets_label(tmp_path):
    d = resolve_run_daily_modes(
        **BASE,
        narration_path=_narr(tmp_path, None),
        week_arc_active=True,
        week_arc_day_plan={"recommended_mode": "long_conversation"},
    )
    assert d.long_conversation_effective is True
    assert d.dialogue_effective is True
    assert d.week_arc_recommended_label == "long_conversation"


def test_week_arc_dialogue_label(tmp_path):
    d = resolve_run_daily_modes(
        **BASE,
        narration_path=_narr(tmp_path, None),
        week_arc_active=True,
        week_arc_day_plan={"recommended_mode": "dialogue"},
    )
    assert (d.dialogue_effective, d.long_conversation_effective) == (True, False)
    assert d.week_arc_recommended_label == "dialogue"


def test_manual_flag_overrides_week_arc(tmp_path):
    # A CLI mode flag => manual_mode => week arc does not apply.
    d = resolve_run_daily_modes(
        **{**BASE, "no_dialogue_arg": True},
        narration_path=_narr(tmp_path, None),
        week_arc_active=True,
        week_arc_day_plan={"recommended_mode": "long_conversation"},
    )
    assert d.dialogue_effective is False
    assert d.week_arc_recommended_label is None


def test_week_arc_narration_recommendation_no_label(tmp_path):
    d = resolve_run_daily_modes(
        **BASE,
        narration_path=_narr(tmp_path, None),
        week_arc_active=True,
        week_arc_day_plan={"recommended_mode": "narration"},
    )
    assert (d.dialogue_effective, d.long_conversation_effective) == (False, False)
    assert d.week_arc_recommended_label is None
