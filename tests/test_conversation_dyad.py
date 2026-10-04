"""Tests for long-conversation dyad helpers and diversity hints."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.conversation_dyad import (
    LEWIS_CLARK_DYAD,
    build_journal_dyad_phase1_block,
    detect_journal_explicit_dyads,
    extract_conversation_dyad,
    format_dyad_label,
    normalize_conversation_dyad,
    stale_conversation_dyads,
)
from pipeline.narration_phase1 import _PACK_DIALOGUE_LONG, build_phase1_user_prompt
from pipeline.recent_episode_diversity import build_diversity_hints, load_prior_episode_metas


def test_normalize_conversation_dyad_sorts() -> None:
    assert normalize_conversation_dyad(["lewis", "clark"]) == LEWIS_CLARK_DYAD
    assert format_dyad_label(LEWIS_CLARK_DYAD) == "clark+lewis"


def test_extract_conversation_dyad_requires_long_mode() -> None:
    data = {
        "long_conversation_mode": True,
        "conversation_micro_arc": {"speakers": ["clark", "ordway"]},
    }
    assert extract_conversation_dyad(data) == ("clark", "ordway")
    data["long_conversation_mode"] = False
    assert extract_conversation_dyad(data) is None


def test_detect_journal_lewis_clark_hill_walk() -> None:
    text = (
        "Capt Lewis and my Self walked to the hill from the top of which "
        "we had a butifull prospect of Serounding Countrey"
    )
    dyads = detect_journal_explicit_dyads(text)
    assert LEWIS_CLARK_DYAD in dyads


def test_build_journal_dyad_block_nonempty() -> None:
    block = build_journal_dyad_phase1_block([LEWIS_CLARK_DYAD])
    assert "JOURNAL-EXPLICIT SPEAKER PAIRS" in block
    assert "clark+lewis" in block


def test_phase1_user_prompt_includes_journal_dyads() -> None:
    text = "Capt Lewis and my Self walked to the hill."
    prompt = build_phase1_user_prompt(
        text,
        "18040613",
        entry_author="William Clark",
        dialogue_mode=True,
        prompt_pack=_PACK_DIALOGUE_LONG,
    )
    assert "JOURNAL-EXPLICIT SPEAKER PAIRS" in prompt
    assert "clark+lewis" in prompt


def _write_narration(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body), encoding="utf-8")


def test_long_conversation_clark_ordway_skew_hint(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    lc_body = {
        "long_conversation_mode": True,
        "conversation_micro_arc": {"speakers": ["clark", "ordway"]},
        "narration_script": [],
    }
    for did in ("18040610", "18040611", "18040612"):
        _write_narration(narr / f"narration{did}.json", lc_body)
    _write_narration(
        narr / "narration18040609.json",
        {
            "long_conversation_mode": True,
            "conversation_micro_arc": {"speakers": ["clark", "lewis"]},
            "narration_script": [],
        },
    )
    hints = build_diversity_hints(
        load_prior_episode_metas(narr, "18040613", limit=5),
        repo_root=tmp_path,
        prompt_pack="lewis_clark_long_conversation",
        narrations_dir=narr,
    )
    assert any("clark+ordway" in h for h in hints)


def test_stale_conversation_dyads_excludes_recent() -> None:
    lc_metas = [
        {"conversation_dyad": "clark+ordway"},
        {"conversation_dyad": "clark+lewis"},
    ]
    known = [LEWIS_CLARK_DYAD, ("clark", "ordway"), ("clark", "york")]
    stale = stale_conversation_dyads(lc_metas, known, exclude=LEWIS_CLARK_DYAD)
    assert "clark+york" in stale
    assert "clark+ordway" not in stale
