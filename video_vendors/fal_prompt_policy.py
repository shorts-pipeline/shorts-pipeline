"""Detect weapon and animal-focal policies in Wan/FAL prompt text."""

from __future__ import annotations

import re

_EMPTY_HANDS_SIGNAL_RE = re.compile(
    r"(?i)\b(?:"
    r"empty hands?|hands empty|no musket|no rifle|no longarm|no firearms?|"
    r"longarm(?:s)? out of frame|firearms? out of frame|without (?:a )?(?:musket|rifle|longarm)"
    r")\b"
)

_HUNT_OR_ARMED_RE = re.compile(
    r"(?i)\b(?:"
    r"hunt(?:ing|er|ed)?|stalk(?:ing)?|game animal|"
    r"priming powder|loading powder|shouldered longarm|shouldered musket|shouldered rifle|"
    r"longarm grounded|grounded (?:at|beside) (?:his|her) side|grounded musket|grounded rifle|"
    r"aim(?:ing|s)? (?:a |the )?(?:musket|rifle|longarm)|"
    r"spyglass.{0,40}(?:deer|elk)|distant live (?:deer|elk)"
    r")\b"
)

_ANIMAL_FOCAL_SUBJECT_RE = re.compile(
    r"(?i)\b(?:"
    r"seaman|newfoundland(?: dog)?|companion dog|the dog|scout.?animal|"
    r"horse|mule|oxen?|livestock"
    r")\b"
)

_ANIMAL_FOCAL_COMPOSITION_RE = re.compile(
    r"(?i)\b(?:"
    r"sole focal|single animal|animal as (?:the )?(?:clear )?focal|"
    r"alone in frame|no (?:armed |rifle |musket )"
    r")\b"
)

_WEAPON_PROP_RE = re.compile(
    r"(?i)\b(?:"
    r"rifles?|muskets?|longarms?|firearms?|weapons?|guns?|pistols?|bayonets?|"
    r"priming powder|loading powder|powder horn"
    r")\b"
)


def prompt_has_empty_hands_policy(text: str) -> bool:
    return bool(_EMPTY_HANDS_SIGNAL_RE.search(text or ""))


def prompt_is_hunt_or_armed(text: str) -> bool:
    return bool(_HUNT_OR_ARMED_RE.search(text or ""))


def prompt_mentions_weapon_prop(text: str) -> bool:
    """True when the prompt mentions firearms/weapons as props (for conditional negatives)."""
    return bool(_WEAPON_PROP_RE.search(text or "")) or prompt_is_hunt_or_armed(text)


def prompt_mentions_animal_focal_subject(text: str) -> bool:
    return bool(_ANIMAL_FOCAL_SUBJECT_RE.search(text or ""))


def prompt_requests_animal_focal_composition(text: str) -> bool:
    return bool(_ANIMAL_FOCAL_COMPOSITION_RE.search(text or ""))


def prompt_is_animal_focal(
    text: str,
    *,
    character_id: str | None = None,
) -> bool:
    """True when the segment should keep one animal as sole focal subject."""
    if character_id:
        try:
            from . import _is_animal_character

            if _is_animal_character(character_id):
                return True
        except Exception:
            pass
    if prompt_requests_animal_focal_composition(text):
        return True
    if prompt_mentions_animal_focal_subject(text) and not prompt_is_hunt_or_armed(text):
        return True
    return False


def should_apply_weapon_to_longarms_rewrite(text: str) -> bool:
    """First-pass sanitizer: skip weapons→longarms when empty-hands or hunt/armed policy is set."""
    if prompt_has_empty_hands_policy(text):
        return False
    if prompt_is_hunt_or_armed(text):
        return False
    return True


def should_apply_aggressive_weapon_softening(text: str) -> bool:
    """Aggressive retry: soften weapon terms only when not hunt/armed and not empty-hands."""
    if prompt_has_empty_hands_policy(text):
        return False
    if prompt_is_hunt_or_armed(text):
        return False
    return True
