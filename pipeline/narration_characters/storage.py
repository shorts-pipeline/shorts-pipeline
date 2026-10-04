"""Load character definitions, pair-portrait rules, and portrait file paths."""

from __future__ import annotations

import json
from functools import lru_cache

from pipeline.narration_characters.paths import CHAR_PATH, PORTRAITS_DIR
from pipeline.narration_characters.text import normalize_text, parse_iso_date
from pipeline.narration_characters.types import (
    Character,
    CharacterRelationship,
    DialogueProfile,
    PairPortraitRule,
)


def lookup_composite_pair(
    speaker_a: str,
    speaker_b: str,
) -> tuple[str, str, str] | None:
    """
    Return ``(composite_id, left_member_id, right_member_id)`` for two speakers.

    Uses ``pair_portraits`` when configured (member_ids array order = left/right).
    Otherwise falls back to ``{a}_{b}`` with alphabetical left/right placement.
    Does not require the composite PNG to exist on disk.
    """
    a = (speaker_a or "").strip().lower()
    b = (speaker_b or "").strip().lower()
    if not a or not b or a == b:
        return None
    target = frozenset({a, b})
    if CHAR_PATH.is_file():
        try:
            raw = json.loads(CHAR_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
        for row in raw.get("pair_portraits") or []:
            if not isinstance(row, dict):
                continue
            comp = str(row.get("composite_id") or "").strip()
            mids_raw = row.get("member_ids")
            if not comp or not isinstance(mids_raw, list) or len(mids_raw) < 2:
                continue
            mids = [str(x).strip().lower() for x in mids_raw if str(x).strip()]
            if len(mids) < 2:
                continue
            if frozenset(mids[:2]) != target:
                continue
            return comp, mids[0], mids[1]
    ordered = sorted((a, b))
    return f"{ordered[0]}_{ordered[1]}", ordered[0], ordered[1]


def resolve_composite_for_speakers(
    speaker_a: str,
    speaker_b: str,
) -> tuple[str, str, str] | None:
    """Alias for :func:`lookup_composite_pair` (composite file may be built at runtime)."""
    return lookup_composite_pair(speaker_a, speaker_b)


def portrait_exists_for_composite(composite_id: str) -> bool:
    cid = (composite_id or "").strip()
    if not cid:
        return False
    for ext in (".png", ".jpg", ".jpeg"):
        if (PORTRAITS_DIR / f"{cid}{ext}").exists():
            return True
    return False


def lewis_clark_duo_portrait_exists() -> bool:
    return portrait_exists_for_composite("lewis_clark")


@lru_cache(maxsize=1)
def load_pair_portrait_rules() -> tuple[PairPortraitRule, ...]:
    if not CHAR_PATH.exists():
        return ()
    try:
        raw = json.loads(CHAR_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ()
    rows = raw.get("pair_portraits") or []
    out: list[PairPortraitRule] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        comp = str(row.get("composite_id") or "").strip()
        mids = row.get("member_ids")
        if not comp or not isinstance(mids, list) or len(mids) < 2:
            continue
        member_set = frozenset(str(x).strip() for x in mids if str(x).strip())
        if len(member_set) < 2:
            continue
        extra = row.get("joint_shot_hints")
        hints: tuple[str, ...]
        if isinstance(extra, list) and extra:
            hints = tuple(normalize_text(str(h)) for h in extra if str(h).strip())
        else:
            hints = ()
        implicit_when: tuple[str, str] | None = None
        imp = row.get("implicit_when")
        if isinstance(imp, dict):
            we = str(imp.get("when_explicit_id") or "").strip()
            rx = str(imp.get("narration_or_video_regex") or "").strip()
            if we and rx:
                implicit_when = (we, rx)
        out.append(
            PairPortraitRule(
                composite_id=comp,
                member_ids=member_set,
                joint_shot_hints=hints,
                implicit_when=implicit_when,
            )
        )
    return tuple(out)


@lru_cache(maxsize=1)
def composite_portrait_ids() -> frozenset[str]:
    return frozenset(r.composite_id for r in load_pair_portrait_rules())


@lru_cache(maxsize=1)
def load_characters() -> list[Character]:
    if not CHAR_PATH.exists():
        return []
    try:
        raw = json.loads(CHAR_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    people = raw.get("people") or []
    out: list[Character] = []
    for p in people:
        try:
            pid = str(p.get("id") or "").strip()
            name = str(p.get("name") or "").strip()
            if not pid or not name:
                continue
            roles = tuple(str(r).strip() for r in (p.get("roles") or []) if str(r).strip())
            phys = str(p.get("physical_description") or "").strip()
            if not phys:
                continue
            active_from = parse_iso_date(str(p.get("active_from") or "1803-01-01"))
            active_to = parse_iso_date(str(p.get("active_to") or "1806-12-31"))
            key_skills = tuple(
                str(s).strip() for s in (p.get("key_skills") or []) if str(s).strip()
            )
            skill_keywords = tuple(
                str(s).strip().lower() for s in (p.get("skill_keywords") or []) if str(s).strip()
            )
            raw_aliases = p.get("aliases") or p.get("journal_spellings") or []
            aliases = tuple(str(a).strip().lower() for a in raw_aliases if str(a).strip())
            rels_out: list[CharacterRelationship] = []
            raw_rels = p.get("relationships")
            if isinstance(raw_rels, list):
                for r in raw_rels:
                    if not isinstance(r, dict):
                        continue
                    rel = str(r.get("relation") or "").strip()
                    oid = str(r.get("character_id") or "").strip()
                    if rel and oid:
                        rels_out.append(CharacterRelationship(relation=rel, character_id=oid))
            relationships = tuple(rels_out)
            dp: DialogueProfile | None = None
            dp_raw = p.get("dialogue_profile") or p.get("narration_voice")
            if isinstance(dp_raw, dict):
                hw = dp_raw.get("habitual_words") or dp_raw.get("lexical_tics")
                words = tuple(str(w).strip() for w in (hw or []) if str(w).strip())
                rhythm_raw = dp_raw.get("speech_rhythm") or dp_raw.get("speech_style")
                rhythm_s = str(rhythm_raw).strip() if rhythm_raw else None
                cues_raw = (
                    dp_raw.get("cues") or dp_raw.get("behaviors") or dp_raw.get("prompt_cues")
                )
                cue_tup = tuple(str(c).strip() for c in (cues_raw or []) if str(c).strip())
                if words or rhythm_s or cue_tup:
                    dp = DialogueProfile(
                        habitual_words=words,
                        speech_rhythm=rhythm_s,
                        cues=cue_tup,
                    )
            out.append(
                Character(
                    id=pid,
                    name=name,
                    roles=roles,
                    active_from=active_from,
                    active_to=active_to,
                    physical_description=phys,
                    key_skills=key_skills,
                    skill_keywords=skill_keywords,
                    aliases=aliases,
                    relationships=relationships,
                    dialogue_profile=dp,
                )
            )
        except Exception:
            continue
    return out


def pair_member_characters_for_composite(composite_id: str) -> tuple[Character, ...] | None:
    cid = (composite_id or "").strip()
    if not cid:
        return None
    by_id = {c.id: c for c in load_characters()}
    for rule in load_pair_portrait_rules():
        if rule.composite_id != cid:
            continue
        members: list[Character] = []
        for mid in sorted(rule.member_ids):
            m = by_id.get(mid)
            if m is None:
                return None
            members.append(m)
        return tuple(members)
    try:
        from pipeline.dynamic_pair_scene_anchor import resolve_pair_member_ids_for_composite

        resolved = resolve_pair_member_ids_for_composite(cid)
        if resolved is None:
            return None
        left_id, right_id = resolved
        left = by_id.get(left_id)
        right = by_id.get(right_id)
        if left is None or right is None:
            return None
        return (left, right)
    except Exception:
        return None


def pair_implicit_trigger_for_composite(composite_id: str) -> tuple[str, str] | None:
    cid = (composite_id or "").strip()
    if not cid:
        return None
    for rule in load_pair_portrait_rules():
        if rule.composite_id == cid and rule.implicit_when is not None:
            return rule.implicit_when
    return None


def character_portrait_asset_exists(character_id: str) -> bool:
    cid = (character_id or "").strip()
    if not cid:
        return False
    for ext in (".png", ".jpg", ".jpeg"):
        if (PORTRAITS_DIR / f"{cid}{ext}").exists():
            return True
    return False
