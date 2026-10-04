"""Title uniqueness helpers and post-Phase-1 title rewrite."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.narration_phase1 import refine_phase1_title
from pipeline.phase1_title_hook import (
    accept_rewritten_title,
    build_title_rewrite_user_prompt,
    first_spoken_sentence,
    is_formulaic_hook_opener,
    is_generic_episode_title,
    parse_rewritten_title,
    title_rewrite_reason,
    title_stem,
)


def test_title_stem_strips_date_suffix():
    assert title_stem("Cave of the Tavern - May 23, 1804") == "cave of the tavern"
    assert title_stem("Testing the Waters") == "testing the waters"


def test_generic_episode_title():
    assert is_generic_episode_title("Lewis & Clark Expedition: September 09, 1803")
    assert is_generic_episode_title("Lewis and Clark Expedition")
    assert not is_generic_episode_title("Court Martial Consequences")


def test_title_rewrite_reason_reuse():
    assert title_rewrite_reason(
        "Testing the Waters",
        recent_title_stems=["testing the waters", "riffles and rain"],
    )
    assert (
        title_rewrite_reason("Cave of the Tavern", recent_title_stems=["testing the waters"])
        is None
    )


def test_formulaic_hook_opener():
    assert is_formulaic_hook_opener("As dawn breaks the river is quiet.")
    assert is_formulaic_hook_opener("Another day begins shrouded in fog.")
    assert not is_formulaic_hook_opener("The Missouri River surged with force.")


def test_first_spoken_sentence_prefers_dialogue():
    segs = [
        {
            "narration": "Director notes only.",
            "dialogue": [
                {"speaker_id": "clark", "text": "This river is choked with drift. Keep pulling."}
            ],
        }
    ]
    assert first_spoken_sentence(segs) == "This river is choked with drift."


def test_parse_rewritten_title_json_and_plain():
    assert (
        parse_rewritten_title('{"title": "Court Martial Consequences"}')
        == "Court Martial Consequences"
    )
    assert parse_rewritten_title("Riffles and Rain") == "Riffles and Rain"


def test_accept_rewritten_title_rejects_reuse():
    assert (
        accept_rewritten_title("Testing the Waters", recent_title_stems=["testing the waters"])
        is None
    )
    assert (
        accept_rewritten_title("Cave of the Tavern", recent_title_stems=["testing the waters"])
        == "Cave of the Tavern"
    )


def test_build_title_rewrite_user_prompt_includes_avoid_list():
    phase1 = {
        "title": "Testing the Waters",
        "narrative_spine": {"core_situation": "Court martial at camp."},
        "segments": [{"narration": "Three men face court martial for misconduct in camp today."}],
    }
    prompt = build_title_rewrite_user_prompt(
        phase1,
        reason="reuses recent title stem 'testing the waters'",
        recent_titles=["Testing the Waters", "Fog and Fatigue"],
    )
    assert "Testing the Waters" in prompt
    assert "Fog and Fatigue" in prompt
    assert "court martial" in prompt.lower()


def test_refine_phase1_title_skips_when_unique(tmp_path: Path, monkeypatch):
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("should not call the title rewrite API")

    monkeypatch.setattr("pipeline.narration_phase1._call_api", boom)
    (tmp_path / "narrations").mkdir()
    out = refine_phase1_title(
        {
            "title": "Cave of the Tavern",
            "segments": [{"narration": "Kickapoo traders hail the keelboat."}],
        },
        date_id="18040524",
        repo_root=tmp_path,
        prompt_pack="lewis_clark",
        model="gpt-4o",
    )
    assert out["title"] == "Cave of the Tavern"
    assert called["n"] == 0


def test_refine_phase1_title_rewrites_generic(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "pipeline.narration_phase1._call_api",
        lambda *_a, **_k: json.dumps({"title": "Court Martial Consequences"}),
    )
    (tmp_path / "narrations").mkdir()
    (tmp_path / "narrations" / "narration18040516.json").write_text(
        json.dumps({"title": "Testing the Waters", "narration_script": []}),
        encoding="utf-8",
    )
    out = refine_phase1_title(
        {
            "title": "Lewis & Clark Expedition: May 17, 1804",
            "narrative_spine": {"core_situation": "Three men tried for misconduct."},
            "segments": [
                {"narration": "Three men face court martial for misconduct at the Missouri camp."}
            ],
        },
        date_id="18040517",
        repo_root=tmp_path,
        prompt_pack="lewis_clark",
        model="gpt-4o",
    )
    assert out["title"] == "Court Martial Consequences"
