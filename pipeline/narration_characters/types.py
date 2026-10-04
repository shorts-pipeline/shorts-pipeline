from __future__ import annotations

from dataclasses import dataclass
from datetime import date

DEFAULT_JOINT_SHOT_HINTS: tuple[str, ...] = (
    " with ",
    " beside ",
    " alongside ",
    " together ",
    " between them",
    " both ",
    "discuss",
    "confer",
    "planning",
    "maps and",
    "two leaders",
    "co-captain",
    " side by side",
)


@dataclass(frozen=True)
class PairPortraitRule:
    """When narration names exactly this set of people and a composite portrait exists, prefer one i2i anchor."""

    composite_id: str
    member_ids: frozenset[str]
    joint_shot_hints: tuple[str, ...]
    implicit_when: tuple[str, str] | None = None


@dataclass(frozen=True)
class CharacterRelationship:
    relation: str
    character_id: str


@dataclass(frozen=True)
class DialogueProfile:
    """Optional temperament cues for Phase 1 dialogue polish (from config, not TTS audio)."""

    habitual_words: tuple[str, ...] = ()
    speech_rhythm: str | None = None
    cues: tuple[str, ...] = ()


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    roles: tuple[str, ...]
    active_from: date
    active_to: date
    physical_description: str
    key_skills: tuple[str, ...]
    skill_keywords: tuple[str, ...]
    aliases: tuple[str, ...]
    relationships: tuple[CharacterRelationship, ...] = ()
    dialogue_profile: DialogueProfile | None = None


@dataclass
class CharacterMatch:
    character: Character
    reason: str
    score: int
