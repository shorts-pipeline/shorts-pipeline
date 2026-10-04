"""Weapon and animal-focal policy helpers for FAL prompts."""

from video_vendors.fal import _sanitize_fal_prompt
from video_vendors.fal_prompt_policy import (
    prompt_has_empty_hands_policy,
    prompt_is_animal_focal,
    prompt_is_hunt_or_armed,
    prompt_mentions_weapon_prop,
    should_apply_aggressive_weapon_softening,
    should_apply_weapon_to_longarms_rewrite,
)
from video_vendors.fal_scene_anchor_i2i import (
    _fal_i2i_policy_suffix,
    build_scene_anchor_i2i_prompt,
)


def test_empty_hands_policy_detected():
    assert prompt_has_empty_hands_policy("Hands empty at his side.")
    assert not prompt_has_empty_hands_policy("Shouldered longarm on the prairie.")


def test_hunt_or_armed_detected():
    assert prompt_is_hunt_or_armed("Stalking elk through timber with a longarm.")
    assert not prompt_is_hunt_or_armed("Campfire near the tents.")


def test_prompt_mentions_weapon_prop():
    assert prompt_mentions_weapon_prop("Musket resting by the tent.")
    assert prompt_mentions_weapon_prop("Stalking elk through timber.")
    assert not prompt_mentions_weapon_prop("Keelboat crew poles through sand bars.")


def test_animal_focal_for_seaman_character():
    assert prompt_is_animal_focal("Riverbank at dusk.", character_id="seaman")


def test_sanitizer_skips_weapon_rewrite_for_empty_hands():
    text = "Council scene with empty hands and no musket."
    out = _sanitize_fal_prompt(text, aggressive=False)
    assert "weapons" not in text.lower() or "longarms" not in out.lower()
    assert "empty hands" in out


def test_sanitizer_aggressive_skips_weapons_when_empty_hands():
    text = "Empty hands; men draw their weapons."
    out = _sanitize_fal_prompt(text, aggressive=True)
    assert "walking sticks" not in out


def test_sanitizer_aggressive_softens_generic_weapons():
    text = "Men draw their weapons on the trail."
    out = _sanitize_fal_prompt(text, aggressive=True)
    assert "walking sticks" in out


def test_should_apply_weapon_rewrite_flags():
    assert not should_apply_weapon_to_longarms_rewrite("Hands empty at camp.")
    assert should_apply_weapon_to_longarms_rewrite("Men ready their weapons.")
    assert not should_apply_aggressive_weapon_softening("Priming powder on the musket.")


def test_i2i_policy_suffix_only_when_explicit():
    assert (
        _fal_i2i_policy_suffix(
            "Photorealistic camp scene. Clark checks lashings.",
            "clark",
        )
        == ""
    )
    suffix = _fal_i2i_policy_suffix(
        "Camp scene with empty hands at his side.",
        "clark",
    )
    assert "do not copy musket" in suffix.lower()


def test_i2i_prompt_includes_policy_suffix():
    prompt = build_scene_anchor_i2i_prompt(
        prompt_without_markers_sanitized="Camp scene with empty hands.",
        character_id="clark",
        segment_index=1,
        opening_frames=["Clark stands with empty hands."],
        world_prefix_for_i2i="Period scene.",
        aggressive=False,
        sanitize=lambda text, aggressive=False: text,
    )
    assert "do not copy musket" in prompt.lower()


def test_i2i_animal_anchor_blocks_anthropomorphic_hybrid():
    """Seaman anchors must not inherit the human 'clothing / held objects' intro."""
    prompt = build_scene_anchor_i2i_prompt(
        prompt_without_markers_sanitized=(
            "Keep this breed and build: A large black Newfoundland dog with a thick coat. "
            "Seaman, the Newfoundland dog sits alert beside the men."
        ),
        character_id="seaman",
        segment_index=4,
        opening_frames=[None, None, None, "Seaman seated, watching intently."],
        world_prefix_for_i2i="Photorealistic early-1800s expedition riverbank, period dress and gear.",
        aggressive=False,
        sanitize=lambda text, aggressive=False: text,
    )
    low = prompt.lower()
    # Breed grounding is retained on the opening-frame path.
    assert "newfoundland" in low
    # Anti-anthropomorphism guard is present.
    assert "not anthropomorphic" in low
    assert "on all fours" in low
    # The human intro line that invites clothing/held objects is not used.
    assert "held objects follow the opening frame" not in low


def test_i2i_animal_anchor_fallback_has_anatomy_guard():
    prompt = build_scene_anchor_i2i_prompt(
        prompt_without_markers_sanitized="Seaman the Newfoundland dog on the riverbank.",
        character_id="seaman",
        segment_index=1,
        opening_frames=None,
        world_prefix_for_i2i="Period scene.",
        aggressive=False,
        sanitize=lambda text, aggressive=False: text,
    )
    assert "not anthropomorphic" in prompt.lower()
