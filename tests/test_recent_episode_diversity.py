import json
from pathlib import Path

from pipeline.narration_common import load_narration_config
from pipeline.narration_phase1 import build_phase1_user_prompt
from pipeline.recent_episode_diversity import (
    build_diversity_hints,
    build_episode_diversity_bundle,
    build_recent_episode_history_lines,
    build_recent_episodes_diversity_digest,
    is_lewis_clark_prompt_pack,
    load_prior_episode_metas,
    parse_recent_episodes_diversity_digest,
    resolve_episode_diversity_config,
)


def _write_narration(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body), encoding="utf-8")


def test_is_lewis_clark_prompt_pack() -> None:
    assert is_lewis_clark_prompt_pack("lewis_clark")
    assert is_lewis_clark_prompt_pack("lewis_clark_dialogue")
    assert is_lewis_clark_prompt_pack("lewis_clark_long_conversation")
    assert not is_lewis_clark_prompt_pack("generic_documentary")
    assert not is_lewis_clark_prompt_pack("")


def test_non_lc_pack_skips_diversity_bundle(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    (repo / "prompt_packs" / "generic_documentary").mkdir(parents=True)
    (repo / "prompt_packs" / "generic_documentary" / "pack.json").write_text(
        '{"id":"generic_documentary","version":"1.0.0"}',
        encoding="utf-8",
    )
    narr = repo / "narrations"
    narr.mkdir()
    bundle = build_episode_diversity_bundle(repo, narr, "18040510", "generic_documentary", {})
    assert bundle["applicable"] is False
    assert bundle["digest"] == ""
    assert bundle["hints"] == []


def test_lc_pack_reads_episode_diversity_from_pack_json(tmp_path: Path) -> None:
    repo = tmp_path
    pack_dir = repo / "prompt_packs" / "lewis_clark"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.json").write_text(
        '{"id":"lewis_clark","episode_diversity":{"enabled":true,"recent_window":3}}',
        encoding="utf-8",
    )
    cfg = resolve_episode_diversity_config(
        repo, "lewis_clark", {"episode_diversity": {"recent_window": 9}}
    )
    assert cfg is not None
    assert cfg["recent_window"] == 3


def test_build_digest_empty_when_no_prior(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    assert build_recent_episodes_diversity_digest(narr, "18040510", limit=5) == ""


def test_history_lines_list_prior_newest_first(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    _write_narration(
        narr / "narration18040508.json",
        {
            "episode_metadata": {"primary_lens": "navigation", "location_summary": "River bend"},
            "tone_register": "tense",
            "dialogue_mode": True,
            "narration_script": [
                {"visual_mode": "talking_head", "dialogue": [{"speaker_id": "lewis", "text": "a"}]},
                {"visual_mode": "b_roll", "dialogue": []},
            ],
        },
    )
    _write_narration(
        narr / "narration18040509.json",
        {
            "episode_metadata": {"primary_lens": "environmental", "location_summary": "Prairie"},
            "tone_register": "warm",
            "dialogue_mode": False,
            "narration_script": [{"visual_mode": "b_roll"}],
        },
    )
    metas = load_prior_episode_metas(narr, "18040510", limit=5)
    lines = build_recent_episode_history_lines(metas)
    text = "\n".join(lines)
    assert "18040509" in text
    assert "18040508" in text
    assert text.index("18040509") < text.index("18040508")
    assert "primary_lens=environmental" in text
    assert "seaman≈no" in text
    digest = build_recent_episodes_diversity_digest(narr, "18040510", limit=5)
    assert "RECENT EPISODES ON DISK" not in digest


def test_digest_skew_hint_for_repeated_lens(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did in ("18040501", "18040502", "18040503", "18040504"):
        _write_narration(
            narr / f"narration{did}.json",
            {
                "episode_metadata": {"primary_lens": "logistical", "location_summary": "Camp"},
                "tone_register": "light",
                "dialogue_mode": False,
                "narration_script": [{"visual_mode": "b_roll"}],
            },
        )
    text = build_recent_episodes_diversity_digest(narr, "18040510", limit=5)
    assert "DIVERSITY HINTS" in text
    assert "primary_lens" in text
    assert "logistical" in text
    assert "RECENT EPISODES ON DISK" not in text


def test_digest_ordway_streak_hint(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did in ("18040501", "18040502", "18040503", "18040504"):
        _write_narration(
            narr / f"narration{did}.json",
            {
                "episode_metadata": {"primary_lens": "navigation", "location_summary": "River"},
                "tone_register": "reflective",
                "dialogue_mode": True,
                "narration_script": [
                    {"visual_mode": "b_roll", "dialogue": [{"speaker_id": "ordway", "text": "x"}]}
                ],
            },
        )
    text = build_recent_episodes_diversity_digest(narr, "18040510", limit=5)
    assert "Ordway" in text


def test_build_phase1_user_prompt_includes_digest() -> None:
    digest = "DIVERSITY HINTS (soft; obey facts and pack rules):\n- Vary tone."
    prompt = build_phase1_user_prompt(
        "Sample journal body for test.",
        "18040510",
        entry_author="Clark",
        recent_episodes_digest=digest,
    )
    assert digest in prompt


def test_digest_seaman_hint_when_absent_in_window(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did in ("18040501", "18040502", "18040503", "18040504"):
        _write_narration(
            narr / f"narration{did}.json",
            {
                "title": "River miles",
                "episode_metadata": {"primary_lens": "navigation", "location_summary": "River"},
                "tone_register": "reflective",
                "dialogue_mode": False,
                "narration_script": [
                    {
                        "visual_mode": "b_roll",
                        "narration": "The party made distance in the rain.",
                        "video_prompt": "Wide river; men at oars.",
                    }
                ],
            },
        )
    metas = load_prior_episode_metas(narr, "18040510", limit=5)
    assert any(m.get("seaman_label") == "no" for m in metas)
    text = build_recent_episodes_diversity_digest(narr, "18040510", limit=5)
    assert "Seaman" in text
    assert "DIVERSITY HINTS" in text
    assert "star beat" in text
    assert "Ambient dog is light" in text or "Seaman presence has been light" in text


def test_digest_no_soft_hints_until_four_prior_episodes(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did in ("18040501", "18040502", "18040503"):
        _write_narration(
            narr / f"narration{did}.json",
            {
                "episode_metadata": {"primary_lens": "logistical", "location_summary": "Camp"},
                "tone_register": "light",
                "dialogue_mode": False,
                "narration_script": [{"visual_mode": "b_roll"}],
            },
        )
    metas = load_prior_episode_metas(narr, "18040510", limit=5)
    assert len(build_recent_episode_history_lines(metas)) == 3
    assert build_recent_episodes_diversity_digest(narr, "18040510", limit=5) == ""


def test_digest_ambient_dog_video_counts_as_presence(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    _write_narration(
        narr / "narration18040509.json",
        {
            "narration_script": [
                {
                    "visual_mode": "b_roll",
                    "narration": "The men pitched camp before dusk.",
                    "video_prompt": "Corps members raise tents; a large black dog sleeps near the fire.",
                }
            ],
        },
    )
    metas = load_prior_episode_metas(narr, "18040510", limit=5)
    assert metas[0].get("seaman_label") == "ambient"


def test_digest_star_and_ambient_labels(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    _write_narration(
        narr / "narration18040509.json",
        {
            "narration_script": [
                {
                    "visual_mode": "b_roll",
                    "narration": "Seaman, the Newfoundland dog, shook river water from his coat.",
                    "video_prompt": "Seaman at the prow while the pirogue poles upstream.",
                    "reference_character_id": "seaman",
                },
                {
                    "visual_mode": "b_roll",
                    "narration": "Evening camp routine.",
                    "video_prompt": "Men at tents; a black Newfoundland among the party by the fire.",
                },
            ],
        },
    )
    metas = load_prior_episode_metas(narr, "18040510", limit=5)
    assert metas[0].get("seaman_label") == "star+ambient"


def test_digest_seaman_yes_when_narration_names_dog(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    _write_narration(
        narr / "narration18040509.json",
        {
            "title": "Seaman swims",
            "episode_metadata": {"primary_lens": "environmental", "location_summary": "River"},
            "narration_script": [
                {
                    "visual_mode": "b_roll",
                    "narration": "Seaman crossed and shook the water from his coat.",
                }
            ],
        },
    )
    metas = load_prior_episode_metas(narr, "18040510", limit=5)
    assert metas[0].get("seaman_label") == "star"


def test_seaman_detected_via_reference_character_id(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    _write_narration(
        narr / "narration18040509.json",
        {
            "narration_script": [
                {
                    "visual_mode": "b_roll",
                    "reference_character_id": "lewis_seaman",
                    "narration": "Camp evening.",
                }
            ],
        },
    )
    metas = load_prior_episode_metas(narr, "18040510", limit=5)
    assert metas[0].get("seaman_label") == "star"


def test_parse_recent_episodes_diversity_digest_hints_only() -> None:
    digest = (
        "DIVERSITY HINTS (soft; obey facts and pack rules):\n- Vary tone when the source allows.\n"
    )
    parsed = parse_recent_episodes_diversity_digest(digest)
    assert parsed["recent_episodes"] == []
    assert parsed["hints"] == ["Vary tone when the source allows."]
    assert parse_recent_episodes_diversity_digest("") == {"recent_episodes": [], "hints": []}


def test_bookend_hints_when_wake_open_and_camp_close_skew(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    body = {
        "episode_metadata": {"primary_lens": "navigation", "location_summary": "River"},
        "tone_register": "reflective",
        "narration_script": [
            {
                "visual_mode": "b_roll",
                "narration": "At dawn the Corps set out early on a fair morning.",
                "video_prompt": "Men leave camp at first light.",
            },
            {
                "visual_mode": "b_roll",
                "narration": "They measured the river.",
                "video_prompt": "Clark at the bank.",
            },
            {
                "visual_mode": "b_roll",
                "narration": "Night fell as they made camp by the fire.",
                "video_prompt": "Tents pitched at dusk; fire crackling.",
            },
        ],
    }
    for did in ("18040501", "18040502", "18040503", "18040504"):
        _write_narration(narr / f"narration{did}.json", body)
    hints = build_diversity_hints(load_prior_episode_metas(narr, "18040510", limit=5))
    assert any("segment-1 hooks skew" in h for h in hints)
    assert any("final-segment endings skew" in h for h in hints)
    line = build_recent_episode_history_lines(load_prior_episode_metas(narr, "18040510", limit=5))[
        0
    ]
    assert "open≈wake" in line
    assert "close≈camp" in line


def test_close_bookend_detects_campfire_and_camp_settles_endings(tmp_path: Path) -> None:
    """Camp endings in video_prompt only (campfire, camp settles) count toward close skew."""
    narr = tmp_path / "narrations"
    narr.mkdir()
    body = {
        "episode_metadata": {"primary_lens": "diplomacy", "location_summary": "River"},
        "tone_register": "reflective",
        "narration_script": [
            {
                "visual_mode": "b_roll",
                "narration": "River travel.",
                "video_prompt": "Keelboat upstream.",
            },
            {
                "segment_type": "reflection",
                "visual_mode": "b_roll",
                "narration": "The alliance could shape what lay ahead.",
                "video_prompt": "As the sun sets, a man sits by the campfire; the camp settles into quiet calm.",
            },
        ],
    }
    for did in ("18040501", "18040502", "18040503", "18040504"):
        _write_narration(narr / f"narration{did}.json", body)
    metas = load_prior_episode_metas(narr, "18040510", limit=5)
    assert all(m.get("close_evening_camp") for m in metas)
    hints = build_diversity_hints(metas)
    assert any("final-segment endings skew" in h for h in hints)


def test_hunt_haul_skew_hint_when_prior_episodes_haul_heavy(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    body = {
        "episode_metadata": {"primary_lens": "logistical", "location_summary": "Timber"},
        "tone_register": "grounded",
        "narration_script": [
            {
                "visual_mode": "b_roll",
                "narration": "Hunters returned with meat.",
                "video_prompt": "Colter carrying a deer over his shoulder toward camp.",
            },
            {
                "visual_mode": "b_roll",
                "narration": "More game came in.",
                "video_prompt": "Drouillard hauling an elk through the timber.",
            },
        ],
    }
    for did in ("18040501", "18040502", "18040503", "18040504"):
        _write_narration(narr / f"narration{did}.json", body)
    hints = build_diversity_hints(load_prior_episode_metas(narr, "18040510", limit=5))
    assert any("carrying" in h.lower() and "stalking" in h.lower() for h in hints)


def test_stock_phrase_skew_hint_when_prior_episodes_repeat_phrase(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    body = {
        "episode_metadata": {"primary_lens": "environmental", "location_summary": "River"},
        "tone_register": "reflective",
        "narration_script": [
            {
                "visual_mode": "b_roll",
                "narration": "A gentle breeze crossed the water as they passed the sandbar.",
                "video_prompt": "Wide river shot; gentle breeze moves the grass along the sand bar.",
            }
        ],
    }
    for did in ("18040501", "18040502", "18040503", "18040504"):
        _write_narration(narr / f"narration{did}.json", body)
    metas = load_prior_episode_metas(narr, "18040510", limit=5)
    assert metas[0].get("stock_phrase_hits") == {"gentle breeze", "sandbar"}
    hints = build_diversity_hints(metas)
    assert any(
        ("gentle breeze" in h or "sandbar" in h) and "swap in a concrete" in h for h in hints
    )

    from pipeline.recent_episode_diversity import evaluate_static_diversity_rules

    fired = {r["id"] for r in evaluate_static_diversity_rules(metas) if r["fired"]}
    assert "stock_phrase_repetition" in fired


def test_stock_phrase_hint_absent_when_phrases_vary(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    bodies = [
        {
            "narration_script": [
                {"narration": "A gentle breeze crossed the water.", "video_prompt": "River."}
            ]
        },
        {
            "narration_script": [
                {"narration": "They camped by a riverbank.", "video_prompt": "Tents."}
            ]
        },
        {
            "narration_script": [
                {"narration": "Prairie stretches went on for miles.", "video_prompt": "Grass."}
            ]
        },
        {"narration_script": [{"narration": "Clark took a reading.", "video_prompt": "Compass."}]},
    ]
    for did, body in zip(("18040501", "18040502", "18040503", "18040504"), bodies, strict=True):
        _write_narration(narr / f"narration{did}.json", body)
    metas = load_prior_episode_metas(narr, "18040510", limit=5)
    hints = build_diversity_hints(metas)
    assert not any("swap in a concrete" in h for h in hints)


def test_ambient_hint_suppressed_after_recent_star_beats(tmp_path: Path) -> None:
    """Star-only episodes (e.g. two Seaman beats in 0530) satisfy presence; no ambient nudge next day."""
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did, body in (
        (
            "18040526",
            {
                "narration_script": [
                    {
                        "visual_mode": "b_roll",
                        "narration": "River travel.",
                        "video_prompt": "Men at oars.",
                    }
                ]
            },
        ),
        (
            "18040527",
            {
                "narration_script": [
                    {
                        "visual_mode": "b_roll",
                        "narration": "Camp.",
                        "video_prompt": "Tents in rain.",
                    }
                ]
            },
        ),
        (
            "18040528",
            {
                "narration_script": [
                    {
                        "visual_mode": "b_roll",
                        "narration": "Seaman, the Newfoundland dog, watched the river.",
                        "video_prompt": "Seaman at the prow.",
                        "reference_character_id": "seaman",
                    }
                ],
            },
        ),
        (
            "18040529",
            {
                "narration_script": [
                    {
                        "visual_mode": "b_roll",
                        "narration": "Evening camp.",
                        "video_prompt": "Men at tents; a large black dog sleeps near the fire.",
                    }
                ],
            },
        ),
        (
            "18040530",
            {
                "narration_script": [
                    {
                        "visual_mode": "b_roll",
                        "narration": "Seaman, the Newfoundland dog, stood watchful beside the men.",
                        "video_prompt": "Seaman on the bank as tents go up.",
                        "reference_character_id": "seaman",
                    },
                    {
                        "visual_mode": "b_roll",
                        "narration": "Lewis rested.",
                        "video_prompt": "Lewis at his tent; Seaman, the Newfoundland dog beside him.",
                        "reference_character_id": "lewis_seaman",
                    },
                ],
            },
        ),
    ):
        _write_narration(narr / f"narration{did}.json", body)
    hints = build_diversity_hints(load_prior_episode_metas(narr, "18040531", limit=5))
    assert not any("Ambient dog is light" in h for h in hints)
    assert not any("Seaman presence has been light" in h for h in hints)
    assert not any("no star beat" in h for h in hints)


def test_min_seaman_presence_three_of_five() -> None:
    from pipeline.recent_episode_diversity import _min_seaman_episodes_in_window

    assert _min_seaman_episodes_in_window(5) == 3
    assert _min_seaman_episodes_in_window(4) == 3


def test_ambient_hint_when_only_two_of_five_have_seaman(tmp_path: Path) -> None:
    """0603-style window: 2/5 with Seaman should nudge ambient (threshold is 3/5)."""
    narr = tmp_path / "narrations"
    narr.mkdir()
    bodies = {
        "18040602": {"narration_script": [{"narration": "Rain.", "video_prompt": "River."}]},
        "18040601": {"narration_script": [{"narration": "Camp.", "video_prompt": "Tents."}]},
        "18040531": {"narration_script": [{"narration": "Travel.", "video_prompt": "Canoes."}]},
        "18040530": {
            "narration_script": [
                {
                    "narration": "Seaman, the Newfoundland dog, stood watchful.",
                    "video_prompt": "Seaman on the bank.",
                    "reference_character_id": "seaman",
                }
            ]
        },
        "18040529": {
            "narration_script": [
                {
                    "narration": "Evening camp.",
                    "video_prompt": "Men at tents; a large black dog sleeps near the fire.",
                }
            ]
        },
    }
    for did, body in bodies.items():
        _write_narration(narr / f"narration{did}.json", body)
    hints = build_diversity_hints(load_prior_episode_metas(narr, "18040603", limit=5))
    assert any("Seaman presence has been light" in h for h in hints)


def test_parse_legacy_digest_with_history() -> None:
    digest = (
        "RECENT EPISODES ON DISK (newest first; for variety only—do not contradict today's journal):\n"
        "- 18040527: primary_lens=cultural | tone=warm\n"
        "\n"
        "DIVERSITY HINTS (soft; obey facts and pack rules):\n"
        "- Vary tone when the source allows.\n"
    )
    parsed = parse_recent_episodes_diversity_digest(digest)
    assert parsed["recent_episodes"] == ["18040527: primary_lens=cultural | tone=warm"]
    assert parsed["hints"] == ["Vary tone when the source allows."]


def test_open_bookend_detects_past_tense_set_out_and_sunrise(tmp_path: Path) -> None:
    """Regression: 'set out early' (past tense) and 'sunrise' openers were missed before."""
    narr = tmp_path / "narrations"
    narr.mkdir()
    bodies = [
        {
            "episode_metadata": {"primary_lens": "navigation", "location_summary": "River"},
            "narration_script": [
                {
                    "visual_mode": "b_roll",
                    "narration": "The Corps of Discovery set out early under a gentle southern breeze.",
                }
            ],
        },
        {
            "episode_metadata": {"primary_lens": "navigation", "location_summary": "River"},
            "narration_script": [
                {
                    "visual_mode": "b_roll",
                    "narration": "Just after sunrise, the Corps of Discovery faces a critical navigation test.",
                }
            ],
        },
        {
            "episode_metadata": {"primary_lens": "navigation", "location_summary": "River"},
            "narration_script": [
                {
                    "visual_mode": "b_roll",
                    "narration": "As a shot rings out from the swivel cannon.",
                }
            ],
        },
        {
            "episode_metadata": {"primary_lens": "navigation", "location_summary": "River"},
            "narration_script": [
                {
                    "visual_mode": "b_roll",
                    "narration": "As the expedition sets out into the swift waters.",
                }
            ],
        },
    ]
    for did, body in zip(("18040501", "18040502", "18040503", "18040504"), bodies, strict=True):
        _write_narration(narr / f"narration{did}.json", body)
    metas = load_prior_episode_metas(narr, "18040510", limit=5)
    assert metas[3].get("open_wake_morning") is True  # 18040501: "set out early"
    assert metas[2].get("open_wake_morning") is True  # 18040502: "sunrise"
    assert metas[1].get("open_wake_morning") is False  # 18040503: cannon salute, not generic
    assert metas[0].get("open_wake_morning") is False  # 18040504: "sets out into", not "early"


def test_closing_type_skew_hint_nudges_toward_forward_tension(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did in ("18040701", "18040702", "18040703", "18040704", "18040705"):
        _write_narration(
            narr / f"narration{did}.json",
            {
                "episode_metadata": {"primary_lens": "navigation", "location_summary": "River"},
                "tone_register": "warm",
                "closing_type": "reflective_lift",
                "narration_script": [{"visual_mode": "b_roll"}],
            },
        )
    metas = load_prior_episode_metas(narr, "18040710", limit=5)
    hints = build_diversity_hints(metas)
    assert any("closing_type" in h and "forward_tension" in h for h in hints)
    from pipeline.recent_episode_diversity import evaluate_static_diversity_rules

    fired = {r["id"] for r in evaluate_static_diversity_rules(metas) if r["fired"]}
    assert "closing_type_skew" in fired


def test_narrative_energy_stagnation_hint_when_no_high_scores(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did, energy in (
        ("18040701", 2),
        ("18040702", 3),
        ("18040703", 2),
        ("18040704", 3),
        ("18040705", 2),
    ):
        _write_narration(
            narr / f"narration{did}.json",
            {
                "episode_metadata": {
                    "primary_lens": "navigation",
                    "location_summary": "River",
                    "narrative_energy": energy,
                },
                "tone_register": "warm",
                "narration_script": [{"visual_mode": "b_roll"}],
            },
        )
    metas = load_prior_episode_metas(narr, "18040710", limit=5)
    hints = build_diversity_hints(metas)
    assert any("narrative_energy" in h and "4 or 5" in h for h in hints)
    from pipeline.recent_episode_diversity import evaluate_static_diversity_rules

    fired = {r["id"] for r in evaluate_static_diversity_rules(metas) if r["fired"]}
    assert "narrative_energy_stagnant" in fired


def test_narrative_energy_hint_absent_when_a_high_score_present(tmp_path: Path) -> None:
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did, energy in (
        ("18040701", 2),
        ("18040702", 4),
        ("18040703", 2),
        ("18040704", 3),
    ):
        _write_narration(
            narr / f"narration{did}.json",
            {
                "episode_metadata": {
                    "primary_lens": "navigation",
                    "location_summary": "River",
                    "narrative_energy": energy,
                },
                "tone_register": "warm",
                "narration_script": [{"visual_mode": "b_roll"}],
            },
        )
    metas = load_prior_episode_metas(narr, "18040710", limit=5)
    hints = build_diversity_hints(metas)
    assert not any("narrative_energy" in h and "4 or 5" in h for h in hints)


def test_closing_type_and_energy_hints_suppressed_when_week_arc_governs_pacing(
    tmp_path: Path,
) -> None:
    """A known week-arc role (e.g. 'build') already dictates closing_type/energy—don't also nudge."""
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did, energy in (
        ("18040701", 2),
        ("18040702", 3),
        ("18040703", 2),
        ("18040704", 3),
        ("18040705", 2),
    ):
        _write_narration(
            narr / f"narration{did}.json",
            {
                "episode_metadata": {
                    "primary_lens": "navigation",
                    "location_summary": "River",
                    "narrative_energy": energy,
                },
                "tone_register": "warm",
                "closing_type": "reflective_lift",
                "narration_script": [{"visual_mode": "b_roll"}],
            },
        )
    metas = load_prior_episode_metas(narr, "18040710", limit=5)

    hints_no_role = build_diversity_hints(metas)
    assert any("closing_type" in h and "forward_tension" in h for h in hints_no_role)
    assert any("narrative_energy" in h and "4 or 5" in h for h in hints_no_role)

    hints_with_role = build_diversity_hints(metas, week_arc_role="build")
    assert not any("closing_type" in h and "forward_tension" in h for h in hints_with_role)
    assert not any("narrative_energy" in h and "4 or 5" in h for h in hints_with_role)

    from pipeline.recent_episode_diversity import evaluate_static_diversity_rules

    fired_no_role = {r["id"] for r in evaluate_static_diversity_rules(metas) if r["fired"]}
    assert "closing_type_skew" in fired_no_role
    assert "narrative_energy_stagnant" in fired_no_role

    fired_with_role = {
        r["id"] for r in evaluate_static_diversity_rules(metas, week_arc_role="build") if r["fired"]
    }
    assert "closing_type_skew" not in fired_with_role
    assert "narrative_energy_stagnant" not in fired_with_role


def test_closing_type_and_energy_hints_active_for_unrecognized_role(tmp_path: Path) -> None:
    """An unknown/missing role does not govern pacing, so the global nudges still apply."""
    narr = tmp_path / "narrations"
    narr.mkdir()
    for did in ("18040701", "18040702", "18040703", "18040704", "18040705"):
        _write_narration(
            narr / f"narration{did}.json",
            {
                "episode_metadata": {"primary_lens": "navigation", "location_summary": "River"},
                "tone_register": "warm",
                "closing_type": "reflective_lift",
                "narration_script": [{"visual_mode": "b_roll"}],
            },
        )
    metas = load_prior_episode_metas(narr, "18040710", limit=5)
    hints = build_diversity_hints(metas, week_arc_role="not_a_real_role")
    assert any("closing_type" in h and "forward_tension" in h for h in hints)


def test_load_narration_config_episode_diversity(tmp_path: Path) -> None:
    p = tmp_path / "cfg.json"
    p.write_text(
        json.dumps(
            {
                "episode_diversity": {"enabled": False, "recent_window": 3},
                "style_diversity": {"enabled": True, "recent_window": 1},
            }
        ),
        encoding="utf-8",
    )
    cfg = load_narration_config(p)
    assert cfg["episode_diversity"]["enabled"] is False
    assert cfg["episode_diversity"]["recent_window"] == 3
