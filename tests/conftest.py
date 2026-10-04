"""Shared pytest hooks for the Lewis & Clark pipeline test suite."""

from __future__ import annotations

import pytest

from pipeline.narration_characters import storage as character_storage


def clear_narration_character_caches() -> None:
    """Reset ``lru_cache`` loaders so tests using ``monkeypatch.setattr(CHAR_PATH, ...)`` stay isolated."""
    character_storage.load_characters.cache_clear()
    character_storage.load_pair_portrait_rules.cache_clear()
    character_storage.composite_portrait_ids.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_narration_character_caches() -> None:
    clear_narration_character_caches()
    yield
    clear_narration_character_caches()


# The public GitHub snapshot (scripts/publish_public.py) omits the private
# ``lewis_clark*`` prompt packs and the portrait images. These tests read those
# assets directly, so skip them when the assets are absent.
_PRIVATE_ASSET_TESTS = frozenset(
    {
        "tests/test_anchor_preview.py::test_scene_anchor_eligible_portrait_b_roll_without_global_flag",
        "tests/test_anchor_preview.py::test_fal_markers_for_eligible_segment",
        "tests/test_broll_scene_anchor.py::test_portrait_backed_b_roll_eligible_when_global_flag_off",
        "tests/test_broll_scene_anchor.py::test_plan_helpers_match_row_helpers",
        "tests/test_conversation_scene_anchor.py::test_discover_conversation_run_uses_raw_shared_setting_without_props",
        "tests/test_conversation_scene_anchor.py::test_conversation_anchor_ui_enrichment",
        "tests/test_plan_workstreams_6_9.py::PromptPackMetadataTests::test_lineage_phase1_dialogue_when_requested",
        "tests/test_prompt_packs.py::TestPromptPacks::test_build_phase2_system_prompt_anchor_boat_guardrails",
        "tests/test_prompt_packs.py::TestPromptPacks::test_build_phase2_system_prompt_substitutions",
        "tests/test_prompt_packs.py::TestPromptPacks::test_long_conversation_phase2_template_loads",
        "tests/test_prompt_packs.py::TestPromptPacks::test_phase1_ambient_sound_in_narration",
        "tests/test_prompt_packs.py::TestPromptPacks::test_phase2_fallback_pack",
        "tests/test_prompt_packs.py::TestPromptPacks::test_phase2_template_loads_and_validates",
        "tests/test_segment_plan.py::test_scene_anchor_eligible_excludes_b_roll_without_character",
        "tests/test_video_vendor_composite_prefix.py::test_build_prompts_adds_separation_guardrail_for_composite_portrait",
        "tests/test_conversation_scene_anchor.py::test_composite_i2i_prompt_avoids_one_person_suffix",
        "tests/test_dual_reference_i2i_prompt.py::test_dual_reference_prompt_binds_portraits_and_scene",
        "tests/test_fal_scene_anchor_i2i_composite.py::test_i2i_prompt_uses_two_person_primary_for_composite_anchor",
        "tests/test_fal_scene_anchor_i2i_composite.py::test_i2i_prompt_dual_reference_fallback_without_opening_frame",
        "tests/test_phase1_dialogue_character_segment_cap.py::test_validate_phase1_dialogue_three_character_dialogue_segments_ok",
        "tests/test_phase1_drouillard_dialogue_rules.py::test_drouillard_trail_hardship_ok",
        "tests/test_phase1_drouillard_dialogue_rules.py::test_validate_phase1_wires_speaker_dialogue_rules",
        "tests/test_phase1_long_conversation_requirements.py::test_long_conversation_requires_sustained_two_speaker_exchange",
        "tests/test_phase1_long_conversation_requirements.py::test_long_conversation_accepts_consecutive_b_roll_alternation",
        "tests/test_phase1_long_conversation_requirements.py::test_long_conversation_rejects_b_roll_stacked_speakers",
        "tests/test_phase1_long_conversation_requirements.py::test_long_conversation_rejects_consecutive_talking_head_same_subject",
        "tests/test_phase1_long_conversation_requirements.py::test_long_conversation_rejects_two_dialogue_rows_in_talking_head",
        "tests/test_phase1_long_conversation_requirements.py::test_long_conversation_rejects_four_line_talking_head_monologue",
        "tests/test_phase1_long_conversation_requirements.py::test_long_conversation_accepts_ping_pong_talking_head_segments",
        "tests/test_phase1_long_conversation_requirements.py::test_long_conversation_require_talking_head_rejects_b_roll_exchange_plus_unrelated_clip",
        "tests/test_phase1_long_conversation_requirements.py::test_long_conversation_require_talking_head_accepts_mixed_window",
        "tests/test_phase1_long_conversation_requirements.py::test_long_conversation_accepts_narrator_interleaved_b_roll_alternation",
    }
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    if (repo / "prompt_packs" / "lewis_clark").is_dir():
        return
    skip = pytest.mark.skip(reason="needs private lewis_clark prompt packs / portraits")
    for item in items:
        if item.nodeid.replace("\\", "/") in _PRIVATE_ASSET_TESTS:
            item.add_marker(skip)
