"""
Character helpers: load expedition cast, match narration to portraits, usage totals.

Split across submodules: types, storage, text, matching, usage, paths.
"""

from __future__ import annotations

from pipeline.narration_characters.matching import (
    assign_reference_character_hints,
    find_character_match,
    render_character_snippet,
    video_prompt_grounds_character,
)
from pipeline.narration_characters.paths import (
    CHAR_PATH,
    CHAR_USAGE_PATH,
    CONFIG_DIR,
    LIB_DIR,
    PIPELINE_DIR,
    PORTRAITS_DIR,
)
from pipeline.narration_characters.storage import (
    character_portrait_asset_exists,
    composite_portrait_ids,
    lewis_clark_duo_portrait_exists,
    load_characters,
    load_pair_portrait_rules,
    lookup_composite_pair,
    pair_implicit_trigger_for_composite,
    pair_member_characters_for_composite,
    portrait_exists_for_composite,
    resolve_composite_for_speakers,
)
from pipeline.narration_characters.text import normalize_text, parse_date_id, tokenize_words
from pipeline.narration_characters.types import (
    Character,
    CharacterMatch,
    CharacterRelationship,
    DialogueProfile,
    PairPortraitRule,
)
from pipeline.narration_characters.usage import load_character_usage, update_character_usage
from pipeline.narration_characters.voice_prompt import dialogue_profile_prompt_section

__all__ = [
    "assign_reference_character_hints",
    "character_portrait_asset_exists",
    "CHAR_PATH",
    "CHAR_USAGE_PATH",
    "CONFIG_DIR",
    "Character",
    "CharacterMatch",
    "CharacterRelationship",
    "DialogueProfile",
    "dialogue_profile_prompt_section",
    "composite_portrait_ids",
    "find_character_match",
    "LIB_DIR",
    "PIPELINE_DIR",
    "lewis_clark_duo_portrait_exists",
    "load_character_usage",
    "load_characters",
    "load_pair_portrait_rules",
    "lookup_composite_pair",
    "normalize_text",
    "PairPortraitRule",
    "pair_implicit_trigger_for_composite",
    "pair_member_characters_for_composite",
    "PORTRAITS_DIR",
    "portrait_exists_for_composite",
    "resolve_composite_for_speakers",
    "render_character_snippet",
    "update_character_usage",
    "video_prompt_grounds_character",
    "parse_date_id",
    "tokenize_words",
]
