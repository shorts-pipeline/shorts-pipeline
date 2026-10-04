"""Wan negative_prompt composition and fal prompt sanitization."""

from video_vendors.fal import _sanitize_fal_prompt
from video_vendors.fal_prompt_policy import prompt_mentions_weapon_prop
from video_vendors.fal_scene_anchor_i2i import (
    build_scene_anchor_t2i_prompt,
    compose_fal_wan_negative_prompt,
    video_prompt_mentions_animal,
)


def test_video_prompt_mentions_animal_keywords():
    assert video_prompt_mentions_animal("A distant deer on the prairie.")
    assert video_prompt_mentions_animal("Snake on warm stones near the trail.")
    assert video_prompt_mentions_animal("Men haul a net of fish from the river.")
    assert not video_prompt_mentions_animal("Boatmen pole the keelboat through riffles.")


def test_prompt_mentions_weapon_prop():
    assert prompt_mentions_weapon_prop("Men with muskets on the bank.")
    assert prompt_mentions_weapon_prop("Stalking elk through timber with a longarm.")
    assert not prompt_mentions_weapon_prop("Crew poles the keelboat through sand bars.")


def test_compose_wan_negative_keelboat_scene_is_short_without_weapons_or_animals():
    neg = compose_fal_wan_negative_prompt(
        "Clark checks lashings on the keelboat deck.",
        character_id="clark",
    )
    assert "extra limbs" in neg
    assert "disappearing objects" in neg
    assert "morphing weapons" not in neg
    assert "vanishing rifle" not in neg
    assert "chimera" not in neg
    assert "carrying game" not in neg
    assert "bare breasts" not in neg
    assert "anus" not in neg
    assert "animal genitals" not in neg
    assert "nsfw" in neg
    assert len(neg) <= 500


def test_compose_wan_negative_adds_weapons_when_prompt_mentions_firearm():
    neg = compose_fal_wan_negative_prompt(
        "Clark rests a longarm against the tent pole.",
        character_id="clark",
    )
    assert "morphing weapons" in neg
    assert "vanishing rifle" in neg
    assert "chimera" not in neg


def test_compose_wan_negative_adds_animal_handling_when_prompt_mentions_game():
    neg = compose_fal_wan_negative_prompt(
        "Hunter sights a distant elk through morning haze.",
        character_id="clark",
    )
    assert "two heads" in neg
    assert "chimera" in neg
    assert "carrying game" in neg
    assert "shouldering game" in neg
    # Hunt/armed also gates weapon permanence terms.
    assert "morphing weapons" in neg


def test_compose_wan_negative_adds_animal_terms_for_animal_character():
    neg = compose_fal_wan_negative_prompt(
        "Camp scene at dusk with men near the fire.",
        character_id="seaman",
    )
    assert "two heads" in neg
    assert "carrying game" in neg
    assert "anthropomorphic animal" in neg
    assert "dog-headed man" in neg


def test_compose_wan_negative_adds_animal_terms_for_environment_still():
    neg = compose_fal_wan_negative_prompt(
        "Wide shot of the Missouri River at dawn, keelboat on the water.",
        character_id=None,
        environment_still=True,
    )
    assert "chimera" in neg
    assert "extra limbs" in neg
    assert "morphing weapons" not in neg


def test_build_scene_anchor_t2i_prompt_environment_guard_without_wildlife():
    from video_vendors.fal import _sanitize_fal_prompt

    prompt = build_scene_anchor_t2i_prompt(
        prompt_without_markers_sanitized="Wide river and prairies at dawn.",
        segment_index=1,
        opening_frames=[None],
        world_prefix_for_i2i="Early 1800s Missouri River.",
        aggressive=False,
        sanitize=_sanitize_fal_prompt,
        character_id="",
    )
    assert "no wildlife" in prompt.lower()


def test_compose_wan_negative_respects_max_length():
    neg = compose_fal_wan_negative_prompt(
        "Deer and fish along the riverbank; hunter with a musket.",
        character_id=None,
        max_len=500,
    )
    assert len(neg) <= 500
    assert "two heads" in neg
    assert "extra limbs" in neg
    assert "morphing weapons" in neg


def test_sanitize_fal_prompt_keeps_longarms_on_first_pass():
    text = "Men draw their weapons as they ready the boats."
    out = _sanitize_fal_prompt(text, aggressive=False)
    assert "longarms" in out
    assert "walking sticks" not in out


def test_sanitize_fal_prompt_softens_weapons_on_aggressive_pass():
    text = "Men draw their weapons with muskets at the ready."
    out = _sanitize_fal_prompt(text, aggressive=True)
    assert "walking sticks" in out
    assert "muskets" not in out
    assert "weapons" not in out


def test_sanitize_fal_prompt_preserves_empty_hands_on_aggressive_pass():
    text = "Empty hands; men draw their weapons."
    out = _sanitize_fal_prompt(text, aggressive=True)
    assert "walking sticks" not in out
    assert "empty hands" in out.lower()


def test_compose_fal_i2i_negative_for_human_adds_animal_when_prompt_mentions_deer():
    from video_vendors.fal_scene_anchor_i2i import compose_fal_i2i_negative_for_human

    neg = compose_fal_i2i_negative_for_human(
        prompt="Drouillard stands by a canoe; a deer lies on the bank."
    )
    assert "extra limbs" in neg
    assert "person riding deer" in neg or "chimera" in neg
    assert "carrying game" in neg
    assert len(neg) <= 500


def test_compose_fal_i2i_negative_for_human_skips_animal_without_wildlife():
    from video_vendors.fal_scene_anchor_i2i import compose_fal_i2i_negative_for_human

    neg = compose_fal_i2i_negative_for_human(prompt="Clark checks rope on the keelboat deck.")
    assert "extra limbs" in neg
    assert "person riding deer" not in neg
    assert "morphing weapons" not in neg
