"""Name/activity matching, pair portraits, Phase 1.5 hints."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any

from pipeline.narration_characters.storage import (
    character_portrait_asset_exists,
    composite_portrait_ids,
    load_characters,
    load_pair_portrait_rules,
    lookup_composite_pair,
)
from pipeline.narration_characters.text import normalize_text, parse_date_id, tokenize_words
from pipeline.narration_characters.types import (
    DEFAULT_JOINT_SHOT_HINTS,
    Character,
    CharacterMatch,
)
from pipeline.narration_characters.usage import load_character_usage

_UNDERUSED_BOOST_POINTS = 1
_UNDERUSED_PERCENTILE = 0.30


def _is_active(c: Character, d: date) -> bool:
    return c.active_from <= d <= c.active_to


def _build_keyword_sets(c: Character) -> tuple[set[str], set[str]]:
    activity = set()
    context = set()
    verb_hints = (
        "hunt",
        "scout",
        "range",
        "build",
        "construct",
        "carry",
        "portage",
        "row",
        "paddle",
        "swim",
        "retrieve",
        "track",
        "repair",
    )
    for kw in c.skill_keywords:
        if any(h in kw for h in verb_hints):
            activity.add(kw)
        else:
            context.add(kw)
    return activity, context


def _contains_phrase(text: str, phrase: str) -> bool:
    return phrase in text


def _underused_character_boost(
    character_id: str,
    totals: dict[str, Any],
    active_usage_values: list[int],
) -> int:
    """
    Return a small score bonus for globally underused active characters.
    Keeps influence gentle: at most +1, and only for the low-usage tail.
    """
    if not active_usage_values:
        return 0
    sorted_vals = sorted(active_usage_values)
    cutoff_idx = min(
        len(sorted_vals) - 1,
        max(0, int((len(sorted_vals) - 1) * _UNDERUSED_PERCENTILE)),
    )
    cutoff = sorted_vals[cutoff_idx]
    char_total = int(totals.get(character_id, 0))
    if char_total <= cutoff:
        return _UNDERUSED_BOOST_POINTS
    return 0


def _explicit_name_match(c: Character, narration_text: str) -> bool:
    name = c.name
    text_lower = narration_text.lower()
    if name:
        full = r"\b" + re.escape(name.lower()) + r"\b"
        if re.search(full, text_lower):
            return True
        parts = name.strip().split()
        if len(parts) >= 2 and c.id not in composite_portrait_ids():
            last = parts[-1].lower()
            if re.search(r"\b" + re.escape(last) + r"\b", text_lower):
                return True
    for alias in c.aliases:
        if alias and re.search(r"\b" + re.escape(alias) + r"\b", text_lower):
            return True
    return False


def _explicit_name_absence_context(c: Character, narration_text: str) -> bool:
    text = normalize_text(narration_text)
    name = (c.name or "").lower().strip()
    if not name:
        return False
    parts = name.split()
    last = parts[-1] if parts else ""
    name_pat = re.escape(name)
    last_pat = re.escape(last) if last else ""

    patterns = []
    if last_pat:
        patterns.extend(
            [
                rf"\bbeyond\b[^.]*\b{name_pat}\b[^.]*\bsupervision\b",
                rf"\bbeyond\b[^.]*\b{last_pat}\b[^.]*\bsupervision\b",
                rf"\bwithout\b[^.]*\b{name_pat}\b[^.]*\bsupervision\b",
                rf"\bwithout\b[^.]*\b{last_pat}\b[^.]*\bsupervision\b",
                rf"\b{name_pat}\b[^.]*\bnot present\b",
                rf"\b{last_pat}\b[^.]*\bnot present\b",
                rf"\b{name_pat}\b[^.]*\babsent\b",
                rf"\b{last_pat}\b[^.]*\babsent\b",
            ]
        )
    return any(re.search(p, text) for p in patterns)


def _explicit_name_observational_landscape_skip(c: Character, narration_text: str) -> bool:
    if c.id not in ("lewis", "clark"):
        return False
    parts = (c.name or "").strip().split()
    if not parts:
        return False
    last = parts[-1].lower()
    if last not in ("lewis", "clark"):
        return False
    last_pat = re.escape(last)
    t = normalize_text(narration_text)
    obs = r"(?:spots|spotted|notices|noticed|sees|saw|observes|observed|notes|noted|finds|found|discovers|discovered|marks|marked)"
    land = r"(?:mound|mounds|formation|formations|fortification|fortifications|earthwork|earthworks|horizon|prairie|landscape|bluff|ridge|ridges|timber|woods|ruins?|structure|structures|features?|elevations?|bank|banks|indian|sheet|sheets|ice)"
    if re.search(rf"\bin the distance\b[^.]{{0,120}}\b{last_pat}\b[^.]{{0,100}}{obs}", t):
        return True
    if re.search(rf"\b{last_pat}\b[^.]{{0,100}}{obs}[^.]{{0,180}}\\b{land}\\b", t):
        return True
    return False


def video_prompt_grounds_character(video_prompt: str, character: Character) -> bool:
    if not (video_prompt or "").strip():
        return False
    t = normalize_text(video_prompt)

    cid = (character.id or "").strip().lower()
    if cid and len(cid) >= 2 and re.search(r"\b" + re.escape(cid) + r"\b", t):
        return True

    name = (character.name or "").strip()
    if name:
        nl = name.lower()
        if len(nl) >= 5 and nl in t:
            return True
        for w in name.split():
            wl = w.lower()
            if len(wl) >= 3 and re.search(r"\b" + re.escape(wl) + r"\b", t):
                return True

    for a in character.aliases:
        al = (a or "").strip().lower()
        if not al:
            continue
        if " " in al:
            if al in t:
                return True
        elif len(al) >= 3 and re.search(r"\b" + re.escape(al) + r"\b", t):
            return True
        elif len(al) < 3 and al in t:
            return True

    for kw in character.skill_keywords:
        kl = (kw or "").strip().lower()
        if not kl:
            continue
        if " " in kl:
            if kl in t:
                return True
        elif re.search(r"\b" + re.escape(kl) + r"\b", t):
            return True

    return False


def _try_pair_portrait_implicit_match(
    chars: list[Character],
    explicit: list[CharacterMatch],
    narration_text: str,
    video_prompt: str | None,
    used_counts: dict[str, int],
    max_per_character: int,
) -> CharacterMatch | None:
    explicit_ids = {cm.character.id for cm in explicit}
    if len(explicit_ids) != 1:
        return None
    only_id = next(iter(explicit_ids))
    vp = (video_prompt or "").strip()
    if not vp:
        return None
    full = normalize_text((narration_text or "") + " " + vp)
    by_id = {c.id: c for c in chars}
    for rule in load_pair_portrait_rules():
        if rule.implicit_when is None:
            continue
        trig_id, pattern = rule.implicit_when
        if trig_id != only_id:
            continue
        try:
            if not re.search(pattern, full):
                continue
        except re.error:
            continue
        if used_counts.get(rule.composite_id, 0) >= max_per_character:
            continue
        if not _pair_reference_ready(rule.composite_id):
            continue
        trigger_char = by_id.get(only_id)
        if trigger_char is None:
            continue
        if not video_prompt_grounds_character(vp, trigger_char):
            continue
        composite = by_id.get(rule.composite_id)
        if composite is None:
            continue
        return CharacterMatch(character=composite, reason="pair_portrait_implicit", score=100)
    return None


def _video_suggests_joint_pair_shot(
    norm_vp: str,
    members: list[Character],
    extra_hints: tuple[str, ...],
) -> bool:
    if not norm_vp or len(members) < 2:
        return False
    for c in members:
        tok = (c.id or "").strip().lower()
        if len(tok) < 2:
            return False
        if not re.search(r"\b" + re.escape(tok) + r"\b", norm_vp):
            return False
    hints = DEFAULT_JOINT_SHOT_HINTS + extra_hints
    if any(h in norm_vp for h in hints):
        return True
    handoff_tokens = (
        "handing",
        "hands ",
        "extends",
        "envelope",
        "letter",
        "parcel",
        "dispatch",
        "toward",
        "towards",
        "between",
        "face one another",
    )
    return any(t in norm_vp for t in handoff_tokens)


def _collect_explicit_candidates(
    chars: list[Character],
    text: str,
    used_counts: dict[str, int],
    max_per_character: int,
) -> list[CharacterMatch]:
    out: list[CharacterMatch] = []
    if not (text or "").strip():
        return out
    for c in chars:
        if used_counts.get(c.id, 0) >= max_per_character:
            continue
        if _explicit_name_match(c, text):
            if _explicit_name_absence_context(c, text):
                continue
            if _explicit_name_observational_landscape_skip(c, text):
                continue
            out.append(CharacterMatch(character=c, reason="explicit_name", score=100))
    return out


def _earliest_mention_index(norm_text: str, character: Character) -> int:
    if not norm_text:
        return 10**9
    best = 10**9
    name = (character.name or "").strip().lower()
    if len(name) >= 5 and name in norm_text:
        best = min(best, norm_text.find(name))
    for w in (character.name or "").split():
        wl = w.lower()
        if len(wl) < 3:
            continue
        for m in re.finditer(r"\b" + re.escape(wl) + r"\b", norm_text):
            best = min(best, m.start())
    cid = (character.id or "").strip().lower()
    if cid and len(cid) >= 2:
        for m in re.finditer(r"\b" + re.escape(cid) + r"\b", norm_text):
            best = min(best, m.start())
    for a in character.aliases:
        al = (a or "").strip().lower()
        if not al:
            continue
        if " " in al:
            if al in norm_text:
                best = min(best, norm_text.find(al))
        elif len(al) >= 3:
            for m in re.finditer(r"\b" + re.escape(al) + r"\b", norm_text):
                best = min(best, m.start())
    return best


def _pair_reference_ready(composite_id: str) -> bool:
    try:
        from pipeline.dynamic_pair_scene_anchor import pair_reference_ready

        return pair_reference_ready(composite_id)
    except Exception:
        return False


def _composite_character_for_pair(
    composite_id: str,
    members: tuple[Character, ...],
    by_id: dict[str, Character],
) -> Character | None:
    existing = by_id.get(composite_id)
    if existing is not None:
        return existing
    if len(members) != 2:
        return None
    left, right = members
    name = f"{left.name} and {right.name}"
    aliases = tuple(
        sorted(
            {
                f"{left.id} and {right.id}",
                f"{right.id} and {left.id}",
                f"{left.id} with {right.id}",
                f"{right.id} with {left.id}",
            }
        )
    )
    return Character(
        id=composite_id,
        name=name,
        roles=tuple(sorted(set(left.roles) | set(right.roles))),
        active_from=min(left.active_from, right.active_from),
        active_to=max(left.active_to, right.active_to),
        physical_description=f"Two subjects in one frame: {left.physical_description}; {right.physical_description}",
        key_skills=tuple(sorted(set(left.key_skills) | set(right.key_skills))),
        skill_keywords=tuple(sorted(set(left.skill_keywords) | set(right.skill_keywords)))
        + aliases,
        aliases=aliases,
    )


def _try_pair_portrait_match(
    chars: list[Character],
    explicit: list[CharacterMatch],
    video_prompt: str | None,
    used_counts: dict[str, int],
    max_per_character: int,
) -> CharacterMatch | None:
    explicit_ids = {cm.character.id for cm in explicit}
    if len(explicit_ids) < 2:
        return None
    vp = (video_prompt or "").strip()
    if not vp:
        return None
    norm_vp = normalize_text(vp)
    by_id = {c.id: c for c in chars}
    for rule in load_pair_portrait_rules():
        if rule.member_ids != explicit_ids:
            continue
        if used_counts.get(rule.composite_id, 0) >= max_per_character:
            continue
        if not _pair_reference_ready(rule.composite_id):
            continue
        members = [by_id[mid] for mid in sorted(rule.member_ids)]
        if len(members) != len(rule.member_ids):
            continue
        if not all(video_prompt_grounds_character(vp, m) for m in members):
            continue
        if not _video_suggests_joint_pair_shot(norm_vp, members, rule.joint_shot_hints):
            continue
        composite = by_id.get(rule.composite_id)
        if composite is None:
            continue
        return CharacterMatch(character=composite, reason="pair_portrait", score=100)
    return None


def _try_dynamic_pair_portrait_match(
    chars: list[Character],
    explicit: list[CharacterMatch],
    video_prompt: str | None,
    used_counts: dict[str, int],
    max_per_character: int,
) -> CharacterMatch | None:
    """Ad-hoc pair when two roster members co-appear (e.g. colter + seaman) without a pair_portraits row."""
    explicit_ids = {cm.character.id for cm in explicit}
    if len(explicit_ids) != 2:
        return None
    vp = (video_prompt or "").strip()
    if not vp:
        return None
    norm_vp = normalize_text(vp)
    by_id = {c.id: c for c in chars}
    ordered = sorted(explicit_ids)
    resolved = lookup_composite_pair(ordered[0], ordered[1])
    if resolved is None:
        return None
    composite_id, left_id, right_id = resolved
    if composite_id in composite_portrait_ids():
        return None
    if used_counts.get(composite_id, 0) >= max_per_character:
        return None
    if not _pair_reference_ready(composite_id):
        return None
    left = by_id.get(left_id)
    right = by_id.get(right_id)
    if left is None or right is None:
        return None
    members = (left, right)
    if not all(video_prompt_grounds_character(vp, m) for m in members):
        return None
    if not _video_suggests_joint_pair_shot(norm_vp, list(members), DEFAULT_JOINT_SHOT_HINTS):
        return None
    composite = _composite_character_for_pair(composite_id, members, by_id)
    if composite is None:
        return None
    return CharacterMatch(character=composite, reason="dynamic_pair_portrait", score=100)


def _resolve_explicit_candidates(
    candidates: list[CharacterMatch],
    video_prompt: str | None,
    usage_key: Callable[[CharacterMatch], tuple[int, int]],
) -> CharacterMatch:
    if len(candidates) == 1:
        return candidates[0]
    vp = (video_prompt or "").strip()
    norm_vp = normalize_text(vp) if vp else ""
    if vp:
        grounded = [cm for cm in candidates if video_prompt_grounds_character(vp, cm.character)]
        if len(grounded) == 1:
            return grounded[0]
        if len(grounded) >= 2:
            scored = [
                (_earliest_mention_index(norm_vp, cm.character), usage_key(cm), cm)
                for cm in grounded
            ]
            scored.sort(key=lambda x: (x[0], x[1]))
            return scored[0][2]
    return min(candidates, key=usage_key)


def find_character_match(
    date_id: str,
    narration_text: str,
    used_counts: dict[str, int],
    max_per_character: int,
    video_prompt: str | None = None,
) -> CharacterMatch | None:
    d = parse_date_id(date_id)
    if d is None:
        return None
    if not (narration_text or "").strip() and not (video_prompt or "").strip():
        return None

    chars = [c for c in load_characters() if _is_active(c, d)]
    if not chars:
        return None

    usage_data = load_character_usage()
    totals = usage_data.get("totals") or {}
    active_usage_values = [int(totals.get(c.id, 0)) for c in chars]

    def _usage_key(cm: CharacterMatch) -> tuple[int, int]:
        return (int(totals.get(cm.character.id, 0)), int(used_counts.get(cm.character.id, 0)))

    explicit_n = _collect_explicit_candidates(chars, narration_text, used_counts, max_per_character)
    if explicit_n:
        duo = _try_pair_portrait_match(
            chars, explicit_n, video_prompt, used_counts, max_per_character
        )
        if duo is not None:
            return duo
        duo_dyn = _try_dynamic_pair_portrait_match(
            chars, explicit_n, video_prompt, used_counts, max_per_character
        )
        if duo_dyn is not None:
            return duo_dyn
        duo_i = _try_pair_portrait_implicit_match(
            chars, explicit_n, narration_text, video_prompt, used_counts, max_per_character
        )
        if duo_i is not None:
            return duo_i
        return _resolve_explicit_candidates(explicit_n, video_prompt, _usage_key)

    if (video_prompt or "").strip():
        explicit_v = _collect_explicit_candidates(
            chars, video_prompt, used_counts, max_per_character
        )
        if explicit_v:
            duo = _try_pair_portrait_match(
                chars, explicit_v, video_prompt, used_counts, max_per_character
            )
            if duo is not None:
                return duo
            duo_dyn = _try_dynamic_pair_portrait_match(
                chars, explicit_v, video_prompt, used_counts, max_per_character
            )
            if duo_dyn is not None:
                return duo_dyn
            duo_i = _try_pair_portrait_implicit_match(
                chars, explicit_v, narration_text, video_prompt, used_counts, max_per_character
            )
            if duo_i is not None:
                return duo_i
            return _resolve_explicit_candidates(explicit_v, video_prompt, _usage_key)

    if not (narration_text or "").strip():
        return None

    text_norm = normalize_text(narration_text)
    tokens = tokenize_words(narration_text)

    best: CharacterMatch | None = None
    for c in chars:
        if used_counts.get(c.id, 0) >= max_per_character:
            continue
        activity_keywords, context_keywords = _build_keyword_sets(c)
        if not activity_keywords and not context_keywords:
            continue
        score = 0

        if c.id == "seaman":
            if ("dog" in tokens or "seaman" in tokens) and (
                "swim" in tokens or "retrieve" in tokens or "water" in tokens
            ):
                score += 3
        else:
            for kw in activity_keywords:
                if _contains_phrase(text_norm, kw):
                    score += 2
            for kw in context_keywords:
                if _contains_phrase(text_norm, kw):
                    score += 1

        if c.id == "colter":
            if not any(w in tokens for w in ("hunt", "hunting", "scout", "scouting", "range")):
                score = 0

        score += _underused_character_boost(c.id, totals, active_usage_values)

        if score <= 0:
            continue
        candidate = CharacterMatch(character=c, reason="activity_keywords", score=score)
        if best is None or score > best.score:
            best = candidate
        elif score == best.score and _usage_key(candidate) < _usage_key(best):
            best = candidate

    if best is None or best.score < 2:
        return None
    return best


def assign_reference_character_hints(
    phase1: dict[str, Any],
    date_id: str,
    character_config: dict[str, Any] | None,
) -> list[str | None]:
    segments = phase1.get("segments") or []
    if not segments:
        return []
    if not character_config or not character_config.get("enabled"):
        return [None] * len(segments)
    max_per_episode = int(character_config.get("max_per_episode", 0) or 0)
    max_per_character = int(character_config.get("max_per_character", 0) or 0)
    if max_per_episode <= 0 or max_per_character <= 0:
        return [None] * len(segments)
    hints: list[str | None] = []
    used_counts: dict[str, int] = {}
    total_char_usages = 0
    n = len(segments)
    for i, seg in enumerate(segments):
        narration = (seg.get("narration") or "").strip()
        hint: str | None = None
        if total_char_usages < max_per_episode and narration and date_id:
            effective_max = max_per_character + 1 if i == n - 1 else max_per_character
            match = find_character_match(
                date_id,
                narration,
                used_counts,
                max_per_character=effective_max,
                video_prompt=None,
            )
            if match is not None:
                cid = match.character.id
                if not character_portrait_asset_exists(cid):
                    pass
                elif match.reason in ("pair_portrait", "dynamic_pair_portrait"):
                    pass
                elif match.reason == "pair_portrait_implicit":
                    pass
                else:
                    hint = cid
                    used_counts[cid] = used_counts.get(cid, 0) + 1
                    total_char_usages += 1
        hints.append(hint)
    return hints


def render_character_snippet(match: CharacterMatch, include_name: bool = False) -> str:
    return match.character.physical_description
