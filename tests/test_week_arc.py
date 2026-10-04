"""Tests for pipeline.week_arc (variable-length narrative-arc bundles)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.week_arc import (
    _validate_plan_payload,
    assemble_week_arc_document,
    build_week_arc_prompt_block,
    cli_specifies_mode,
    expected_closing_type_for_role,
    find_saved_arc_for_date,
    get_day_plan,
    list_journal_date_ids,
    list_saved_week_arcs,
    modes_from_day_plan,
    normalize_recommended_mode,
    requires_talking_head,
    role_pacing_hint,
    save_week_arc,
    week_arc_path,
    week_journal_dates_for,
)


def _write_journal(tmp_path: Path, date_id: str, body: str = "River mile note.") -> None:
    je = tmp_path / "journal-entries"
    je.mkdir(parents=True, exist_ok=True)
    y, m, d = date_id[:4], date_id[4:6], date_id[6:8]
    xml = f"""<?xml version="1.0"?>
<TEI><text><body><div><p>{body}</p></div></body></text></TEI>"""
    (je / f"{y}-{m}-{d}.xml").write_text(xml, encoding="utf-8")


def _dates(start_id: str, n: int) -> list[str]:
    from datetime import datetime, timedelta

    from pipeline.week_arc import date_id_to_journal_date, journal_date_to_date_id

    start = datetime.strptime(date_id_to_journal_date(start_id), "%Y-%m-%d")
    return [
        journal_date_to_date_id((start + timedelta(days=i)).strftime("%Y-%m-%d")) for i in range(n)
    ]


def test_candidate_window_before_any_saved_arc(tmp_path: Path) -> None:
    for did in _dates("18040705", 12):
        _write_journal(tmp_path, did)
    jdir = tmp_path / "journal-entries"
    cfg = {
        "first_anchor_date_id": "18040705",
        "week_journal_days_min": 4,
        "week_journal_days_max": 7,
    }

    assert list_journal_date_ids(jdir, from_anchor="18040705") == _dates("18040705", 12)

    # No arcs saved yet: the anchor is the only date that can start a new arc, and the
    # candidate window is capped at max_days.
    candidate = week_journal_dates_for("18040705", jdir, repo_root=tmp_path, config=cfg)
    assert candidate == _dates("18040705", 7)

    # A date mid-window, with no saved arc of its own, isn't yet reachable.
    assert week_journal_dates_for("18040707", jdir, repo_root=tmp_path, config=cfg) == []
    assert week_journal_dates_for("18040701", jdir, repo_root=tmp_path, config=cfg) == []


def test_candidate_window_after_saved_arc(tmp_path: Path) -> None:
    for did in _dates("18040705", 12):
        _write_journal(tmp_path, did)
    jdir = tmp_path / "journal-entries"
    cfg = {
        "first_anchor_date_id": "18040705",
        "week_journal_days_min": 4,
        "week_journal_days_max": 7,
    }

    # Simulate a previously-planned 5-day arc.
    chosen = _dates("18040705", 5)
    doc = assemble_week_arc_document(
        {
            "through_line": "t",
            "avoid_this_week": [],
            "days": {
                did: {
                    "role": "setup",
                    "recommended_mode": "narration",
                    "speakers": None,
                    "day_focus": "f",
                    "reason": "r",
                }
                for did in chosen
            },
        },
        chosen,
        model="test",
    )
    save_week_arc(week_arc_path(tmp_path, chosen[0]), doc)

    # A date already covered by the saved arc: returns a fresh max-size window from that
    # arc's original start, so a refresh can re-choose a different length.
    mid_window = week_journal_dates_for("18040707", jdir, repo_root=tmp_path, config=cfg)
    assert mid_window == _dates("18040705", 7)
    assert find_saved_arc_for_date("18040707", tmp_path).get("journal_date_ids") == chosen

    # The next unplanned date (right after the saved arc's end) can start a new window.
    next_start = _dates("18040705", 6)[-1]
    assert next_start == "18040710"
    window2 = week_journal_dates_for(next_start, jdir, repo_root=tmp_path, config=cfg)
    assert window2 == _dates("18040710", 7)

    # A date beyond the saved arc that isn't the frontier is unreachable.
    assert week_journal_dates_for("18040711", jdir, repo_root=tmp_path, config=cfg) == []


def test_week_arc_example_file_is_not_treated_as_a_saved_arc(tmp_path: Path) -> None:
    """week_arc.example.json lives alongside real week_<date_id>.json files and must be ignored."""
    for did in _dates("18040705", 8):
        _write_journal(tmp_path, did)
    jdir = tmp_path / "journal-entries"
    cfg = {
        "first_anchor_date_id": "18040705",
        "week_journal_days_min": 4,
        "week_journal_days_max": 7,
    }
    example_doc = {
        "schema_version": 1,
        "week_id": "18040705",
        "week_start_date_id": "18040705",
        "week_end_date_id": "18040711",
        "journal_date_ids": _dates("18040705", 7),
        "through_line": "example",
        "avoid_this_week": [],
        "days": {},
    }
    arcs_dir = tmp_path / "state" / "week_arcs"
    arcs_dir.mkdir(parents=True)
    (arcs_dir / "week_arc.example.json").write_text(json.dumps(example_doc), encoding="utf-8")

    assert find_saved_arc_for_date("18040706", tmp_path) is None
    # The anchor is still the planning frontier -- the example file didn't get mistaken
    # for a real saved arc that would have pushed the frontier forward.
    candidate = week_journal_dates_for("18040705", jdir, repo_root=tmp_path, config=cfg)
    assert candidate == _dates("18040705", 7)


def test_list_saved_week_arcs_newest_first(tmp_path: Path) -> None:
    def _save(chosen: list[str]) -> None:
        doc = assemble_week_arc_document(
            {
                "through_line": "t",
                "avoid_this_week": [],
                "days": {
                    did: {
                        "role": "setup",
                        "recommended_mode": "narration",
                        "speakers": None,
                        "day_focus": "f",
                        "reason": "r",
                    }
                    for did in chosen
                },
            },
            chosen,
            model="test",
        )
        save_week_arc(week_arc_path(tmp_path, chosen[0]), doc)

    _save(_dates("18040705", 5))
    _save(_dates("18040712", 4))

    arcs = list_saved_week_arcs(tmp_path)
    assert [a["week_start_date_id"] for a in arcs] == ["18040712", "18040705"]


def test_modes_from_day_plan_and_focus_topic() -> None:
    day = {"recommended_mode": "long_conversation", "speakers": ["lewis", "clark"]}
    assert modes_from_day_plan(day) == (True, True)
    assert modes_from_day_plan(day, focus_topic="boats") == (False, False)
    assert modes_from_day_plan({"recommended_mode": "dialogue"}) == (True, False)
    assert modes_from_day_plan({"recommended_mode": "narration"}) == (False, False)
    assert normalize_recommended_mode("long-conversation") == "long_conversation"


def test_requires_talking_head() -> None:
    assert requires_talking_head({"recommended_mode": "dialogue"}) is True
    assert (
        requires_talking_head(
            {"recommended_mode": "long_conversation", "speakers": ["lewis", "clark"]}
        )
        is True
    )
    assert requires_talking_head({"recommended_mode": "narration"}) is False
    assert requires_talking_head(None) is False
    assert requires_talking_head({"recommended_mode": "dialogue"}, focus_topic="boats") is False


def test_build_week_arc_prompt_block() -> None:
    arc = {
        "through_line": "River push.",
        "avoid_this_week": ["dawn hooks"],
        "days": {
            "18040705": {
                "role": "setup",
                "recommended_mode": "narration",
                "day_focus": "Camp",
                "reason": "thin",
            }
        },
    }
    block = build_week_arc_prompt_block(arc, "18040705")
    assert "WEEK ARC" in block
    assert "River push." in block
    assert "setup" in block


def test_expected_closing_type_for_role() -> None:
    assert expected_closing_type_for_role("setup") == "forward_tension"
    assert expected_closing_type_for_role("build") == "forward_tension"
    assert expected_closing_type_for_role("hold") == "forward_tension"
    assert expected_closing_type_for_role("payoff") == "reflective_lift"
    assert expected_closing_type_for_role("release") == "reflective_lift"
    assert expected_closing_type_for_role("not_a_role") is None
    assert expected_closing_type_for_role(None) is None


def test_role_pacing_hint_known_and_unknown_roles() -> None:
    hint = role_pacing_hint("build")
    assert hint is not None
    assert "forward_tension" in hint
    assert role_pacing_hint("payoff") is not None
    assert "reflective_lift" in role_pacing_hint("payoff")
    assert role_pacing_hint("not_a_role") is None
    assert role_pacing_hint(None) is None


def test_build_week_arc_prompt_block_includes_role_pacing_guidance() -> None:
    arc = {
        "through_line": "River push.",
        "avoid_this_week": ["dawn hooks"],
        "days": {
            "18040707": {
                "role": "build",
                "recommended_mode": "narration",
                "day_focus": "Rising stakes",
                "reason": "momentum",
            }
        },
    }
    block = build_week_arc_prompt_block(arc, "18040707")
    assert "forward_tension" in block
    assert "narrative_energy" in block


def test_validate_plan_payload() -> None:
    candidate = ["18040705", "18040706", "18040707", "18040708"]
    good = {
        "through_line": "x",
        "avoid_this_week": [],
        "days": {
            "18040705": {
                "role": "setup",
                "recommended_mode": "narration",
                "speakers": None,
                "day_focus": "a",
                "reason": "b",
            },
            "18040706": {
                "role": "payoff",
                "recommended_mode": "long_conversation",
                "speakers": ["lewis", "clark"],
                "day_focus": "c",
                "reason": "d",
            },
        },
    }
    # A 2-day prefix is valid when it's within [min_days, max_days].
    _validate_plan_payload(good, candidate, min_days=2, max_days=4)
    with pytest.raises(ValueError, match="outside allowed range"):
        _validate_plan_payload(good, candidate, min_days=3, max_days=4)
    with pytest.raises(ValueError, match="unexpected days"):
        _validate_plan_payload({"days": {"20000101": {}}}, candidate, min_days=1, max_days=4)
    non_prefix = {"days": {"18040706": {}, "18040707": {}}}
    with pytest.raises(ValueError, match="contiguous prefix"):
        _validate_plan_payload(non_prefix, candidate, min_days=1, max_days=4)
    with pytest.raises(ValueError, match="at least one date"):
        _validate_plan_payload({"days": {}}, candidate, min_days=1, max_days=4)


def test_ensure_week_arc_uses_cache_without_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for did in ("18040705", "18040706"):
        _write_journal(tmp_path, did)
    cfg = {
        "first_anchor_date_id": "18040705",
        "week_journal_days_min": 4,
        "week_journal_days_max": 7,
    }
    week = week_journal_dates_for(
        "18040705", tmp_path / "journal-entries", repo_root=tmp_path, config=cfg
    )
    doc = assemble_week_arc_document(
        {
            "through_line": "cached",
            "avoid_this_week": [],
            "days": {
                did: {
                    "role": "setup",
                    "recommended_mode": "narration",
                    "speakers": None,
                    "day_focus": "f",
                    "reason": "r",
                }
                for did in week
            },
        },
        week,
        model="test",
    )
    path = week_arc_path(tmp_path, "18040705")
    save_week_arc(path, doc)

    def _boom(*_a, **_k):
        raise AssertionError("LLM should not run when cache exists")

    monkeypatch.setattr("pipeline.week_arc.run_week_plan_llm", _boom)
    from pipeline.week_arc import ensure_week_arc

    loaded = ensure_week_arc(
        "18040705",
        repo_root=tmp_path,
        journal_dir=tmp_path / "journal-entries",
        narrations_dir=tmp_path / "narrations",
    )
    assert loaded is not None
    assert loaded.get("through_line") == "cached"
    assert get_day_plan(loaded, "18040705") is not None


def test_cli_specifies_mode() -> None:
    assert cli_specifies_mode(dialogue=True)
    assert not cli_specifies_mode()
    assert cli_specifies_mode(no_dialogue=True)


def test_ensure_week_arc_lets_llm_pick_shorter_arc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM chooses 5 of a possible 10-day candidate window; the saved arc reflects that."""
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    for did in _dates("18040705", 10):
        _write_journal(tmp_path, did)

    chosen_days = _dates("18040705", 5)
    plan_json = json.dumps(
        {
            "through_line": "Five-day push to the payoff.",
            "avoid_this_week": [],
            "days": {
                did: {
                    "role": "payoff" if did == chosen_days[-1] else "setup",
                    "recommended_mode": "narration",
                    "speakers": None,
                    "day_focus": "f",
                    "reason": "r",
                }
                for did in chosen_days
            },
        }
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=plan_json))]
    )
    monkeypatch.setattr("pipeline_logging.log_api_call_with_bodies", lambda *a, **k: None)

    from pipeline.week_arc import ensure_week_arc

    cfg_path = tmp_path / "config"
    cfg_path.mkdir()
    (cfg_path / "week_arc.json").write_text(
        json.dumps(
            {
                "first_anchor_date_id": "18040705",
                "week_journal_days_min": 4,
                "week_journal_days_max": 10,
                "plan_model": "test-model",
            }
        ),
        encoding="utf-8",
    )

    doc = ensure_week_arc(
        "18040705",
        repo_root=tmp_path,
        journal_dir=tmp_path / "journal-entries",
        narrations_dir=tmp_path / "narrations",
        client=mock_client,
    )
    assert doc is not None
    assert doc["journal_date_ids"] == chosen_days
    assert doc["week_end_date_id"] == chosen_days[-1]
    assert set(doc["days"].keys()) == set(chosen_days)

    # Saved under the chosen arc's own boundaries, not the full 10-day candidate window.
    assert week_arc_path(tmp_path, chosen_days[0]).is_file()
    assert find_saved_arc_for_date(chosen_days[2], tmp_path)["journal_date_ids"] == chosen_days
