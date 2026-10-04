from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pipeline.episode_diversity_audit import (
    _validate_single_checker,
    annotate_unrenderable_findings,
    build_audit_cache_payload,
    build_episode_summaries_for_audit,
    dedupe_and_cap_hints,
    dynamic_hints_from_cache,
    hint_has_concrete_alternative,
    hint_redundant_with_static,
    is_audit_stale,
    normalize_checker_pattern,
    refresh_diversity_audit,
    regenerate_diversity_checker,
    run_diversity_audit_llm,
    summarize_episode_for_diversity_audit,
    validate_audit_response,
)
from pipeline.recent_episode_diversity import (
    OPEN_BOOKEND_DIVERSITY_HINT,
    build_diversity_hints,
    build_episode_diversity_bundle,
    load_prior_episode_metas,
)


def _write_narration(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body), encoding="utf-8")


def test_summarize_episode_for_diversity_audit() -> None:
    data = {
        "title": "River day",
        "tone_register": "warm",
        "episode_metadata": {"primary_lens": "navigation"},
        "narration_script": [
            {
                "segment_type": "hook",
                "visual_mode": "b_roll",
                "narration": "At dawn the party set out early along the Missouri.",
                "video_prompt": "Morning mist on the river.",
            },
            {
                "segment_type": "close",
                "visual_mode": "b_roll",
                "narration": "Evening camp under cottonwoods.",
                "video_prompt": "Tents at dusk.",
            },
        ],
    }
    summary = summarize_episode_for_diversity_audit("18040528", data)
    assert summary["date_id"] == "18040528"
    assert summary["primary_lens"] == "navigation"
    assert len(summary["segment_outline"]) == 2
    assert summary["flags"]["open_wake_morning"] is True
    assert summary["open_snippet"]
    # Full (untruncated) video_prompt per segment—needed for the unrenderable-language scan.
    assert summary["segment_outline"][0]["video_prompt"] == "Morning mist on the river."
    assert summary["segment_outline"][1]["video_prompt"] == "Tents at dusk."


def test_validate_audit_response_rejects_bad_shape() -> None:
    with pytest.raises(ValueError, match="patterns must be an array"):
        validate_audit_response({"patterns": "nope"})
    with pytest.raises(ValueError, match="suggested_hint is required"):
        validate_audit_response({"patterns": [{"id": "x", "category": "open_bookend"}]})


def test_validate_audit_response_normalizes() -> None:
    raw = {
        "patterns": [
            {
                "id": "morning_opener",
                "category": "open_bookend",
                "prevalence": "3/5",
                "evidence": ["18040528: dawn"],
                "suggested_hint": "Vary morning openers when the journal allows.",
            }
        ],
        "recommended_checkers": [
            {
                "id": "morning_opener",
                "check_type": "first_segment_regex",
                "patterns": [r"\bat dawn\b"],
                "skew_threshold": 0.5,
            }
        ],
        "notes": "ok",
    }
    out = validate_audit_response(raw)
    assert out["patterns"][0]["suggested_hint"].startswith("Vary morning")
    assert out["recommended_checkers"][0]["status"] == "proposed"


def test_validate_audit_response_defaults_unrenderable_findings_when_absent() -> None:
    out = validate_audit_response({"patterns": [], "recommended_checkers": []})
    assert out["unrenderable_language_findings"] == []


def test_validate_audit_response_normalizes_unrenderable_findings() -> None:
    raw = {
        "patterns": [],
        "recommended_checkers": [],
        "unrenderable_language_findings": [
            {
                "date_id": "18040531",
                "segment_index": 2,
                "category": "sound",
                "excerpt": "the report echoes across the landscape",
                "suggested_pattern": r"\bechoes\b",
            },
            {
                "date_id": "18040531",
                "segment_index": 4,
                "category": "not_a_real_category",
                "excerpt": "a strange hum fills the clearing",
                "suggested_pattern": "hum",
            },
            {"excerpt": ""},  # dropped: empty excerpt
            "not a dict",  # dropped: wrong shape
        ],
    }
    out = validate_audit_response(raw)
    findings = out["unrenderable_language_findings"]
    assert len(findings) == 2
    assert findings[0]["category"] == "sound"
    assert findings[0]["segment_index"] == 2
    # Invalid category falls back to "sound" rather than raising.
    assert findings[1]["category"] == "sound"


def test_validate_audit_response_caps_and_truncates_unrenderable_findings() -> None:
    raw = {
        "patterns": [],
        "recommended_checkers": [],
        "unrenderable_language_findings": [
            {
                "date_id": "18040531",
                "segment_index": i,
                "category": "invisible_state",
                "excerpt": "x" * 300,
                "suggested_pattern": "y" * 200,
            }
            for i in range(12)
        ],
    }
    out = validate_audit_response(raw)
    findings = out["unrenderable_language_findings"]
    assert len(findings) == 8  # capped
    assert len(findings[0]["excerpt"]) <= 200
    assert len(findings[0]["suggested_pattern"]) <= 120


def test_annotate_unrenderable_findings_marks_already_handled() -> None:
    findings = [
        {
            "date_id": "18040531",
            "segment_index": 1,
            "category": "sound",
            "excerpt": "the fire crackles nearby",
            "suggested_pattern": r"\bcrackles\b",
        },
        {
            "date_id": "18040531",
            "segment_index": 3,
            "category": "invisible_state",
            "excerpt": "a strange hum fills the clearing",
            "suggested_pattern": "hum",
        },
    ]
    out = annotate_unrenderable_findings(findings)
    # The stripper already swaps "crackles" -> "flickers".
    assert out[0]["already_handled"] is True
    # "hum" is not in any curated pattern list today.
    assert out[1]["already_handled"] is False


def test_dedupe_and_cap_hints() -> None:
    hints = [
        "Vary tone when the source allows.",
        "vary tone when the source allows.",
        "Short hint.",
        "Another unique hint.",
        "Extra one.",
        "Sixth.",
        "Seventh should drop.",
    ]
    capped = dedupe_and_cap_hints(hints, max_total=6)
    assert len(capped) == 6
    assert capped[0] == "Vary tone when the source allows."


def test_dynamic_hints_from_cache_gated_by_checker(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did in ("18040527", "18040528", "18040529"):
        _write_narration(
            narr / f"narration{did}.json",
            {
                "narration_script": [
                    {
                        "narration": "At dawn the party embarked.",
                        "video_prompt": "Morning departure on the river.",
                    }
                ]
            },
        )
    cache = {
        "patterns": [
            {
                "id": "morning_opener",
                "suggested_hint": "LLM: reduce dawn openers.",
            }
        ],
        "recommended_checkers": [
            {
                "id": "morning_opener",
                "check_type": "first_segment_regex",
                "patterns": [r"\bat dawn\b"],
                "skew_threshold": 0.5,
            }
        ],
    }
    metas = load_prior_episode_metas(narr, "18040530", limit=3)
    active = dynamic_hints_from_cache(cache, metas, narr, max_dynamic_hints=4)
    assert active == ["LLM: reduce dawn openers."]

    # Below threshold when only one of three matches
    _write_narration(
        narr / "narration18040528.json",
        {"narration_script": [{"narration": "Midday portage.", "video_prompt": "Canoes carried."}]},
    )
    _write_narration(
        narr / "narration18040529.json",
        {
            "narration_script": [
                {"narration": "Afternoon hunt.", "video_prompt": "Men in the woods."}
            ]
        },
    )
    metas2 = load_prior_episode_metas(narr, "18040530", limit=3)
    assert dynamic_hints_from_cache(cache, metas2, narr) == []


def test_is_audit_stale(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did in ("18040528", "18040529", "18040530", "18040531", "18040601", "18040602"):
        _write_narration(narr / f"narration{did}.json", {"narration_script": []})
    cache = {"through_date_id": "18040530"}
    assert is_audit_stale(cache, narr, refresh_every_new_episodes=5) is False
    assert is_audit_stale(cache, narr, refresh_every_new_episodes=2) is True
    assert is_audit_stale(None, narr, refresh_every_new_episodes=5) is True


def test_build_diversity_hints_merges_cache(tmp_path: Path) -> None:
    repo = tmp_path
    pack_dir = repo / "prompt_packs" / "lewis_clark"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.json").write_text(
        json.dumps(
            {
                "id": "lewis_clark",
                "episode_diversity": {
                    "enabled": True,
                    "recent_window": 3,
                    "llm_audit": {"enabled": True, "max_dynamic_hints": 2, "max_total_hints": 6},
                },
            }
        ),
        encoding="utf-8",
    )
    state = repo / "state"
    state.mkdir()
    (state / "episode_diversity_lewis_clark.json").write_text(
        json.dumps(
            {
                "patterns": [
                    {
                        "id": "shields_beat",
                        "category": "character_cast",
                        "suggested_hint": "Give Shields a dialogue beat when the journal names him.",
                    }
                ],
                "recommended_checkers": [],
                "hints": ["Give Shields a dialogue beat when the journal names him."],
            }
        ),
        encoding="utf-8",
    )
    narr = repo / "narrations"
    narr.mkdir()
    for did in ("18040528", "18040529", "18040530"):
        _write_narration(
            narr / f"narration{did}.json",
            {"narration_script": [{"narration": "Travel.", "video_prompt": "River."}]},
        )
    metas = load_prior_episode_metas(narr, "18040531", limit=3)
    hints = build_diversity_hints(
        metas,
        repo_root=repo,
        prompt_pack="lewis_clark",
        narrations_dir=narr,
    )
    assert any("Shields" in h for h in hints)


def test_build_episode_diversity_bundle_includes_llm_audit(tmp_path: Path) -> None:
    repo = tmp_path
    pack_dir = repo / "prompt_packs" / "lewis_clark"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.json").write_text(
        json.dumps(
            {
                "id": "lewis_clark",
                "episode_diversity": {
                    "enabled": True,
                    "recent_window": 2,
                    "llm_audit": {"enabled": True, "refresh_every_new_episodes": 99},
                },
            }
        ),
        encoding="utf-8",
    )
    state = repo / "state"
    state.mkdir()
    (state / "episode_diversity_lewis_clark.json").write_text(
        json.dumps(
            {
                "through_date_id": "18040529",
                "generated_at": "2026-05-18T12:00:00Z",
                "hints": ["Cached LLM hint."],
                "patterns": [],
                "recommended_checkers": [],
            }
        ),
        encoding="utf-8",
    )
    narr = repo / "narrations"
    narr.mkdir()
    _write_narration(
        narr / "narration18040529.json",
        {"narration_script": [{"narration": "Camp.", "video_prompt": "Tents."}]},
    )
    bundle = build_episode_diversity_bundle(repo, narr, "18040531", "lewis_clark", {})
    assert bundle["llm_audit"]["enabled"] is True
    assert bundle["llm_audit"]["cache_present"] is True
    assert bundle["llm_audit"]["cached_hints"] == ["Cached LLM hint."]


def test_run_diversity_audit_llm_mock(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    (repo / "prompt_packs" / "lewis_clark").mkdir(parents=True)
    (repo / "prompt_packs" / "lewis_clark" / "diversity_audit_system.txt").write_text(
        "Return JSON.", encoding="utf-8"
    )
    audit_json = json.dumps(
        {
            "patterns": [
                {
                    "id": "x",
                    "category": "story_arc",
                    "suggested_hint": "Try a different arc.",
                    "evidence": [],
                }
            ],
            "recommended_checkers": [],
            "notes": "mock",
        }
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=audit_json))]
    )
    monkeypatch.setattr(
        "pipeline_logging.log_api_call_with_bodies",
        lambda *a, **k: None,
    )
    summaries = [{"date_id": "18040528", "title": "t"}]
    out = run_diversity_audit_llm(repo, summaries, "lewis_clark", client=mock_client)
    assert out["patterns"][0]["suggested_hint"] == "Try a different arc."


def test_build_episode_summaries_for_audit(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    _write_narration(
        narr / "narration18040528.json",
        {"title": "A", "narration_script": [{"narration": "x", "video_prompt": "y"}]},
    )
    rows = build_episode_summaries_for_audit(narr, limit=5)
    assert len(rows) == 1
    assert rows[0]["date_id"] == "18040528"


def test_validate_audit_response_rejects_invalid_meta_flag() -> None:
    raw = {
        "patterns": [
            {
                "id": "x",
                "category": "character_cast",
                "suggested_hint": "Give Shields a dialogue beat when the journal names him.",
                "evidence": [],
            }
        ],
        "recommended_checkers": [
            {
                "id": "x",
                "check_type": "meta_flag",
                "meta_flag": "character_cast_repetition",
                "patterns": [],
                "skew_threshold": 0.5,
            }
        ],
    }
    out = validate_audit_response(raw)
    assert out["recommended_checkers"] == []


def test_hint_redundant_with_static() -> None:
    generic = (
        "Consider varying the opening segments to include different scenarios or moods, "
        "especially when the journal entries reflect unique circumstances."
    )
    assert hint_redundant_with_static(generic, [OPEN_BOOKEND_DIVERSITY_HINT])
    story_arc = (
        "Consider varying the story arc structure; instead of always resolving with a camp setup, "
        "explore closing on a challenge, like an unresolved river hazard or an unexpected encounter."
    )
    assert hint_has_concrete_alternative(story_arc)
    assert not hint_redundant_with_static(story_arc, [OPEN_BOOKEND_DIVERSITY_HINT])
    specific = (
        "Three recent episodes open on rain-at-dawn travel only; when the journal allows, "
        "open in medias res on the day's council or trade dispute instead."
    )
    assert not hint_redundant_with_static(specific, [OPEN_BOOKEND_DIVERSITY_HINT])


def test_normalize_checker_pattern() -> None:
    import re

    broken = "\\\\bday concluded\\\\b"
    fixed = normalize_checker_pattern(broken)
    assert re.search(fixed, "the day concluded with camp", re.I)


def test_dynamic_hints_suppressed_when_static_bookend_fires(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did in ("18040527", "18040528", "18040529", "18040530"):
        _write_narration(
            narr / f"narration{did}.json",
            {
                "narration_script": [
                    {
                        "narration": "At dawn the party set out early along the river.",
                        "video_prompt": "Morning mist; men embark at dawn.",
                    },
                    {
                        "narration": "Evening camp.",
                        "video_prompt": "Tents by the fire as night fell.",
                    },
                ]
            },
        )
    cache = {
        "patterns": [
            {
                "id": "open_wake_morning_repetition",
                "category": "open_bookend",
                "suggested_hint": "Vary dawn openers when the journal allows.",
            },
            {
                "id": "shields_dialogue",
                "category": "character_cast",
                "suggested_hint": "Give Shields a dialogue beat when the journal names him.",
            },
        ],
        "recommended_checkers": [],
    }
    metas = load_prior_episode_metas(narr, "18040531", limit=4)
    static = [OPEN_BOOKEND_DIVERSITY_HINT]
    fired = {"open_wake_morning_bookend"}
    active = dynamic_hints_from_cache(
        cache,
        metas,
        narr,
        static_hints=static,
        fired_static_rule_ids=fired,
    )
    assert "Vary dawn openers" not in " ".join(active)
    assert any("Shields" in h for h in active)


def test_build_audit_cache_payload() -> None:
    audit = validate_audit_response(
        {
            "patterns": [
                {
                    "id": "a",
                    "category": "tone_lens",
                    "suggested_hint": "Hint A.",
                    "evidence": [],
                }
            ],
            "recommended_checkers": [],
            "unrenderable_language_findings": [
                {
                    "date_id": "18040531",
                    "segment_index": 1,
                    "category": "sound",
                    "excerpt": "the fire crackles nearby",
                    "suggested_pattern": r"\bcrackles\b",
                },
                {
                    "date_id": "18040531",
                    "segment_index": 3,
                    "category": "invisible_state",
                    "excerpt": "a strange hum fills the clearing",
                    "suggested_pattern": "hum",
                },
            ],
        }
    )
    payload = build_audit_cache_payload(
        prompt_pack="lewis_clark",
        summaries=[{"date_id": "18040531"}],
        audit=audit,
        model="gpt-4o-mini",
    )
    assert payload["through_date_id"] == "18040531"
    assert payload["hints"] == ["Hint A."]
    findings = payload["unrenderable_language_findings"]
    assert len(findings) == 2
    assert findings[0]["already_handled"] is True
    assert findings[1]["already_handled"] is False


def test_validate_single_checker() -> None:
    out = _validate_single_checker(
        {
            "id": "morning_opener",
            "check_type": "first_segment_regex",
            "patterns": ["\\\\bat dawn\\\\b"],
            "skew_threshold": 0.6,
        },
        "morning_opener",
    )
    assert out["id"] == "morning_opener"
    assert out["check_type"] == "first_segment_regex"
    assert out["patterns"] == ["\\bat dawn\\b"]
    assert out["skew_threshold"] == 0.6
    assert out["status"] == "proposed"


def test_regenerate_diversity_checker_updates_cache(tmp_path: Path, monkeypatch) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    _write_narration(
        narr / "narration18040531.json",
        {
            "title": "Day",
            "narration_script": [
                {
                    "segment_type": "hook",
                    "narration": "At dawn we set out.",
                    "video_prompt": "Morning river.",
                }
            ],
        },
    )
    state = tmp_path / "state"
    state.mkdir()
    cache_path = state / "episode_diversity_lewis_clark.json"
    cache_path.write_text(
        json.dumps(
            {
                "prompt_pack": "lewis_clark",
                "through_date_id": "18040531",
                "episode_count": 1,
                "model": "gpt-4o-mini",
                "patterns": [
                    {
                        "id": "morning_opener",
                        "category": "open_bookend",
                        "suggested_hint": "Open in medias res when the journal allows.",
                        "evidence": ["18040531: dawn"],
                    }
                ],
                "recommended_checkers": [
                    {
                        "id": "morning_opener",
                        "check_type": "first_segment_regex",
                        "patterns": ["old_pattern"],
                        "skew_threshold": 0.5,
                        "status": "proposed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    new_checker = {
        "id": "morning_opener",
        "check_type": "first_segment_regex",
        "patterns": ["\\bat dawn\\b"],
        "meta_flag": "",
        "skew_threshold": 0.5,
        "status": "proposed",
    }

    def _fake_llm(*_a, **_k):
        return new_checker

    monkeypatch.setattr(
        "pipeline.episode_diversity_audit.run_checker_regeneration_llm",
        _fake_llm,
    )

    out = regenerate_diversity_checker(
        tmp_path,
        narr,
        "lewis_clark",
        "morning_opener",
    )
    assert out["ok"] is True
    assert out["checker"]["patterns"] == ["\\bat dawn\\b"]
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved["recommended_checkers"][0]["patterns"] == ["\\bat dawn\\b"]


def test_refresh_diversity_audit_writes_cache(tmp_path: Path, monkeypatch) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    _write_narration(
        narr / "narration18040531.json",
        {
            "title": "Day",
            "narration_script": [
                {
                    "segment_type": "hook",
                    "narration": "At dawn we set out.",
                    "video_prompt": "Morning river.",
                }
            ],
        },
    )
    (tmp_path / "state").mkdir()

    fake_audit = validate_audit_response(
        {
            "patterns": [
                {
                    "id": "morning_opener",
                    "category": "open_bookend",
                    "suggested_hint": "Open in medias res when the journal allows.",
                    "evidence": ["18040531: dawn"],
                }
            ],
            "recommended_checkers": [],
            "unrenderable_language_findings": [
                {
                    "date_id": "18040531",
                    "segment_index": 1,
                    "category": "sound",
                    "excerpt": "a strange hum fills the clearing",
                    "suggested_pattern": "hum",
                }
            ],
            "notes": "test",
        }
    )

    monkeypatch.setattr(
        "pipeline.episode_diversity_audit.run_diversity_audit_llm",
        lambda *_a, **_k: fake_audit,
    )

    out = refresh_diversity_audit(
        tmp_path,
        narr,
        "lewis_clark",
        last=5,
        narration_config={
            "episode_diversity": {
                "enabled": True,
                "llm_audit": {"enabled": True, "sample_size": 5, "model": "gpt-4o-mini"},
            }
        },
    )
    assert out["ok"] is True
    assert out["episode_count"] == 1
    assert out["through_date_id"] == "18040531"
    saved = json.loads(Path(out["cache_path"]).read_text(encoding="utf-8"))
    assert saved["hints"] == ["Open in medias res when the journal allows."]
    assert len(saved["unrenderable_language_findings"]) == 1
    assert saved["unrenderable_language_findings"][0]["already_handled"] is False
    assert len(out["unrenderable_language_uncaught"]) == 1
    assert saved["notes"] == "test"
