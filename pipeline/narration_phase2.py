#!/usr/bin/env python3
"""
Phase 2 visual-plan generation helpers.

Extracted from generate-narration-two-phase.py so the CLI script can be a thin
orchestrator. This module owns the Phase 2 system/user prompts, validation,
LLM call wrapper (see pipeline.llm_provider), and merge logic that synthesizes
pipeline-shaped JSON.
"""

from __future__ import annotations

import json
import re
import sys
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from pipeline.conversation_visual_spine import (
    conversation_run_setting_by_segment_index,
    inject_conversation_backdrop,
)
from pipeline.journey_river_context import resolved_expedition_route_hint
from pipeline.llm_provider import call_llm
from pipeline.map_display_policy import compute_phase2_runtime_map_policy_block
from pipeline.narration_characters import (
    character_portrait_asset_exists,
    find_character_match,
    load_characters,
    normalize_text,
    pair_implicit_trigger_for_composite,
    pair_member_characters_for_composite,
    video_prompt_grounds_character,
)
from pipeline.narration_common import clean_json_reply
from pipeline.narration_visual_mode import VISUAL_MODE_TALKING_HEAD, normalize_visual_mode
from pipeline.prompt_pack_text import load_phase2_system_template
from pipeline.scene_spine import (
    build_scene_spine_from_phase2,
    validate_episode_visual_world,
)
from pipeline.tts_post_narration_silence import (
    cap_post_narration_silence_in_script,
    segment_post_narration_silence_seconds,
)
from pipeline.video_prompt_language_stripper import strip_unrenderable_language

MAX_RETRIES = 2
DEFAULT_MAX_TOKENS = 8192

_REPO_ROOT = Path(__file__).resolve().parent.parent


def load_ambient_tag_allowlist() -> tuple[list[str], str]:
    """Return (sorted tag keys, default_tag) from ambient library manifest, or fallbacks."""
    path = _REPO_ROOT / "ambient_library" / "manifest.json"
    fallback_keys = [
        "campfire",
        "chirping_birds",
        "creek",
        "distant_thunder",
        "evening_insects",
        "forest",
        "hailstorm",
        "prairie_wind",
        "river",
        "spring_frogs",
        "wetland",
    ]
    fallback_default = "prairie_wind"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback_keys, fallback_default
    tags = manifest.get("tags") or {}
    if not isinstance(tags, dict) or not tags:
        return fallback_keys, fallback_default
    keys = sorted(str(k) for k in tags.keys() if str(k).strip())
    if not keys:
        return fallback_keys, fallback_default
    dt = manifest.get("default_tag")
    default_tag = str(dt).strip() if isinstance(dt, str) and dt.strip() else fallback_default
    return keys, default_tag


def _character_anchor_hints_active(
    character_anchor_hints: list[str | None] | None,
) -> bool:
    return bool(character_anchor_hints and any((h or "").strip() for h in character_anchor_hints))


def build_phase2_system_prompt(
    focus_topic: str | None = None,
    character_anchor_hints: list[str | None] | None = None,
    *,
    focus_expedition_era: bool = True,
    historical_only_visuals: bool = True,
    repo_root: Path | None = None,
    prompt_pack: str = "lewis_clark",
    runtime_map_policy_block: str = "",
) -> str:
    focus_tone_relax = ""
    if focus_topic:
        if focus_expedition_era:
            focus_tone_relax = (
                "\n- This episode has a focus theme (1803 vs today). Where the narration compares period to modern, "
                "visuals may support that contrast; keep the 1800s setting for segments that stay in period."
            )
        else:
            focus_tone_relax = (
                "\n- This episode may reference a focus theme in the narration; visuals may support that theme "
                "where the narration does, while staying consistent with the period implied by each segment."
            )
    allowed_keys, _default_tag = load_ambient_tag_allowlist()
    allowed_enum_str = " | ".join(allowed_keys)
    allowed_csv_str = ", ".join(allowed_keys)

    root = repo_root or _REPO_ROOT
    prompt = load_phase2_system_template(root, prompt_pack)
    if not historical_only_visuals:
        prompt = prompt.replace(
            """7) HISTORICAL TONE
- No modern props.
- No modern infrastructure.
- Respectful representation of Native communities.
- Avoid stereotypes or generic "tribal" imagery.{focus_tone_relax}""",
            """7) PERIOD AND REPRESENTATION
- Match props, wardrobe, and environments to the period implied by the narration (contemporary settings are allowed when the narration is clearly modern).
- Respectful representation of people and cultures.
- Avoid stereotypes or generic stock imagery.{focus_tone_relax}""",
        )
    anchor_block = ""
    if _character_anchor_hints_active(character_anchor_hints):
        anchor_block = """2k) CHARACTER ANCHOR HINTS (only when the user message lists segments)
- For **each listed** segment: non-empty **opening_frame** (t=0 still for portrait i2i) and **primary_visual** must show that person (or both members for a composite)—face, hands, or body; not environment-only wallpaper.
- **opening_frame** must stay **simple**: one clear pose, minimal props in hand (none or one). Avoid plural “hands” and multi-step actions in the still; save complexity for **primary_visual**.
- The anchored subject must **drive visible motion** (rule 2); i2v centers the portrait—passive "watching others work" fails. Continue motion from opening_frame without duplicating the still.
- **Portrait anchor + boats (rule 2 boats):** When a listed segment is on a **keelboat or barge deck**, **opening_frame** shows only the anchored lead **on deck** (one hand on the gunwale is ok)—**empty river beside the hull**, no crew, no oars in the still. Put **all** seated rowers, oar blades, and splash in **primary_visual** using **on-hull** / **blade-at-waterline-beside-hull** language and the keelboat negatives in rule 2. Avoid vague “crew members,” “push off,” or “into the water” without “beside the hull.”
- **Same character_id on more than one segment:** keep **wardrobe, hair length, and gear** aligned across those segments (and with the portrait pipeline)—do not restyle between listings. Where narration allows, reuse **one** recognizable set cue (same tent edge, table, or bank feature) in **secondary_elements** so anchored shots still honor **VISUAL CONTINUITY** with neighbors.
- If grounding is impossible, add **open_questions**; never omit opening_frame when listed.

"""
    prompt = prompt.replace("__ANCHOR_HINTS_BLOCK__", anchor_block)
    prompt = prompt.replace("__AMBIENT_TAG_ENUM__", allowed_enum_str)
    prompt = prompt.replace("__AMBIENT_TAG_CSV__", allowed_csv_str)
    prompt = prompt.replace("__RUNTIME_MAP_POLICY_BLOCK__", runtime_map_policy_block or "")
    return prompt.replace("{focus_tone_relax}", focus_tone_relax)


def _month_from_date_id(date_id: str) -> int | None:
    """Return calendar month 1-12 from YYYYMMDD-style date_id, or None if not parseable."""
    s = "".join(c for c in str(date_id).strip() if c.isdigit())
    if len(s) < 8:
        return None
    try:
        m = int(s[4:6])
    except ValueError:
        return None
    if 1 <= m <= 12:
        return m
    return None


def _spring_ambient_user_hint(date_id: str) -> str | None:
    """
    March–May (northern spring): nudge ambient_tag choices using only existing manifest keys.
    Omits new tag names so the model stays aligned with the system prompt enum.
    """
    m = _month_from_date_id(date_id)
    if m not in (3, 4, 5):
        return None
    return (
        "SPRING AMBIENT (journal month March–May): When you set audio_design.ambience_level to light or moderate, "
        "choose one ambient_tag only from the manifest tag keys listed in the system instructions. "
        "Bias toward one tag for the whole entry: prairie_wind for open air, marching, and steady breeze; river for the main channel, "
        "boats, banks, and spring high water; creek for small branches and fords; forest for timber, bottomland, "
        "and mixed woods (temperate North American, not tropical); chirping_birds when the journal foregrounds "
        "daytime birdsong along the bank or in open timber (lighter than full forest); wetland for marsh and slough; "
        "spring_frogs for frog-heavy shore or pond; distant_thunder for rain / distant storm mood (not calm sky); "
        "hailstorm only when the journal clearly describes hail or ice pellets (not rain-only); "
        "evening_insects for dusk or night insect beds; campfire for night camp, councils, or drying gear by the "
        "fire. Do not invent tag names outside the manifest keys."
    )


def _summer_ambient_user_hint(date_id: str) -> str | None:
    """
    June–August (northern summer): same manifest-only constraint as spring.
    """
    m = _month_from_date_id(date_id)
    if m not in (6, 7, 8):
        return None
    return (
        "SUMMER AMBIENT (journal month June–August): When you set audio_design.ambience_level to light or moderate, "
        "choose one ambient_tag only from the manifest tag keys listed in the system instructions. "
        "Bias toward one tag for the whole entry: prairie_wind for hot open prairie, steady breeze, and marching; "
        "river for main-channel boating, sandbars, and broad water; creek for shallows and small branches; "
        "forest for shade, timber, and canopy (temperate North American woods); "
        "chirping_birds for prominent daytime songbirds (creek margins, perches, open woods); "
        "wetland for marsh and backwater; evening_insects for warm dusk and night haze; spring_frogs where the journal puts you at frog-heavy shore or pond; "
        "distant_thunder for summer storm buildup (not clear-sky calm); "
        "hailstorm only when the journal clearly describes hail or ice pellets (not rain-only); "
        "campfire for night camp and councils. "
        "Do not invent tag names outside the manifest keys."
    )


def _autumn_ambient_user_hint(date_id: str) -> str | None:
    """
    September–November (northern autumn): same manifest-only constraint as spring.
    """
    m = _month_from_date_id(date_id)
    if m not in (9, 10, 11):
        return None
    return (
        "AUTUMN AMBIENT (journal month September–November): When you set audio_design.ambience_level to light or moderate, "
        "choose one ambient_tag only from the manifest tag keys listed in the system instructions. "
        "Bias toward one tag for the whole entry: prairie_wind for cool open air, gusty march days, and river bluffs; "
        "river for fall travel, crossings, and falling water level; creek for fords and wooded runs; "
        "forest for timber camps, hunting, and mixed woods; "
        "chirping_birds for clear daytime birdsong (not evening_insects); wetland for slough and migration stopovers; "
        "evening_insects for early-fall dusk (taper toward frosty nights); campfire for cold evenings and drying gear; "
        "distant_thunder for overcast rain (not decorative); "
        "hailstorm only when the journal clearly describes hail or ice pellets; "
        "spring_frogs only if the journal clearly has active frogs late season. "
        "Do not invent tag names outside the manifest keys."
    )


def _winter_ambient_user_hint(date_id: str) -> str | None:
    """
    December–February (northern winter): same manifest-only constraint as spring.
    """
    m = _month_from_date_id(date_id)
    if m not in (12, 1, 2):
        return None
    return (
        "WINTER AMBIENT (journal month December–February): When you set audio_design.ambience_level to light or moderate, "
        "choose one ambient_tag only from the manifest tag keys listed in the system instructions. "
        "Bias toward one tag for the whole entry: prairie_wind for cold open air, ice-edge wind, and exposed travel; "
        "river for ice along the bank, open leads, and winter boating where the journal supports it; "
        "creek for small frozen margins and thin ice crossings (mood only—no invented drama); "
        "forest for quiet timber, snow-laden branches, and sparse birds; "
        "chirping_birds only when the journal clearly has active daytime songbirds in mild thaw; wetland for frozen marsh and reeds; "
        "campfire for heating, councils, and long winter nights; distant_thunder for winter storm or freezing rain mood; "
        "hailstorm only when the journal clearly describes hail or ice pellets (not rain-only); "
        "evening_insects sparingly (only if the journal is clearly mild late winter / early thaw); avoid spring_frogs unless the entry is unmistakably frog-active. "
        "Do not invent tag names outside the manifest keys."
    )


def _format_character_anchor_hints_user_lines(
    character_anchor_hints: list[str | None] | None,
) -> list[str]:
    """Human-readable CHARACTER ANCHOR HINTS block for Phase 2 user message."""
    if not _character_anchor_hints_active(character_anchor_hints):
        return []
    assert character_anchor_hints is not None
    by_id = {c.id: c for c in load_characters()}
    lines: list[str] = [
        "CHARACTER ANCHOR HINTS (Phase 1.5 — system rule 2k):",
    ]
    for i, hid in enumerate(character_anchor_hints):
        if not (hid or "").strip():
            continue
        ch = by_id.get(hid.strip())
        label = (ch.name or hid).strip() if ch else hid
        lines.append(
            f"  - Segment {i + 1}: character_id={hid.strip()} ({label}) — "
            "opening_frame + primary_visual required; anchor must be primary actor (rule 2), not passive."
        )
    lines.append("")
    return lines


def _prompt_pack_is_lewis_clark_family(prompt_pack: str) -> bool:
    p = (prompt_pack or "").strip().lower()
    return p.startswith("lewis_clark")


def build_phase2_user_prompt(
    phase1: dict[str, Any],
    date_id: str,
    style: dict[str, str] | None = None,
    focus_topic: str | None = None,
    character_anchor_hints: list[str | None] | None = None,
    *,
    seasonal_ambient_from_date_id: bool = True,
    focus_expedition_era: bool = True,
    prompt_pack: str = "lewis_clark",
    recent_episodes_digest: str | None = None,
) -> str:
    parts = [
        "Convert the following Phase 1 narration JSON into a production-ready visual plan (visual_strategy per segment; final mux uses Phase 1 narration for audio).",
        "Apply **system rule 2** (generative video): one continuous shot per segment, concrete motion, focal performs the work, calm camera—see JSON schema and anchor hints if present.",
        "When opening_frame is set, primary_visual continues from t=0; keep opening_frame a single simple pose (rule 2 opening_frame discipline). Season/date should inform lighting and clothing where consistent with the narration.",
        "",
        json.dumps(phase1, indent=2, ensure_ascii=False),
    ]
    hint_lines = _format_character_anchor_hints_user_lines(character_anchor_hints)
    if hint_lines:
        parts.extend(["", *hint_lines])
    if style:
        parts.append("")
        parts.append(
            f"Apply this visual style to the plan: {style.get('name', '')} — {style.get('description', '')}"
        )
    if focus_topic:
        parts.append("")
        if focus_expedition_era:
            parts.append(
                "This episode uses a focus theme comparing 1803 to today; visuals may support that contrast "
                "(e.g. modern comparisons or contrasts) where the narration does, while keeping the 1800s setting for period segments."
            )
        else:
            parts.append(
                "This episode may use a focus theme from the narration; visuals may support that theme "
                "where the narration does, while keeping each segment visually consistent with its content."
            )
    loc = (phase1.get("episode_metadata") or {}).get("location_summary") or ""
    if loc:
        parts.append("")
        parts.append(f"Location context: {loc}")
    if _prompt_pack_is_lewis_clark_family(prompt_pack):
        ep_meta = (
            phase1.get("episode_metadata")
            if isinstance(phase1.get("episode_metadata"), dict)
            else {}
        )
        rte = resolved_expedition_route_hint(str(date_id), ep_meta).strip()
        if rte:
            parts.append("")
            parts.append(
                f"ROUTE ATTITUDE (journal date {date_id} — **Phase 2 only**; weave into "
                f"`primary_visual` / `secondary_elements` / merged `video_prompt` **only** on segments "
                f"that show **active boating** on a major river—keelboat deck, canoe on the Missouri, "
                f"cordelle tow, poling. **Do not** paste this block into forest, hunt, wildlife, tent "
                f"interior, or windbound camp-maintenance segments. Narration overrides when it contradicts "
                f"(e.g. party halted, windbound, game in timber):\n{rte}"
            )
    parts.append("")
    parts.append(
        "Per-segment **core_location_override** (Phase 2 JSON): When a segment's on-screen place differs from the episode "
        "default (e.g. a letter written in St. Louis while the episode hook is Camp Dubois; flashback; separate town), set "
        "`core_location_override` on that `scene_plan` entry to a short phrase (town + interior or waterfront as needed). "
        "Omit the field when the segment stays in the default camp/river setting. Merged narration uses this for a "
        "short vendor opening location line and FAL scene-anchor i2i only—**not** for global boat/rowing injection; "
        "episode structure and route attitude belong in each segment's `video_prompt` text."
    )
    if seasonal_ambient_from_date_id:
        seasonal_ambient_hint = (
            _spring_ambient_user_hint(date_id)
            or _summer_ambient_user_hint(date_id)
            or _autumn_ambient_user_hint(date_id)
            or _winter_ambient_user_hint(date_id)
        )
        if seasonal_ambient_hint:
            parts.append("")
            parts.append(seasonal_ambient_hint)
    if (recent_episodes_digest or "").strip():
        parts.append("")
        parts.append((recent_episodes_digest or "").strip())
    return "\n\n".join(parts)


def _validate_phase2(
    parsed: dict,
    phase1_segment_count: int,
    character_anchor_hints: list[str | None] | None = None,
    *,
    phase1_segments: list[Any] | None = None,
) -> None:
    if not isinstance(parsed, dict):
        raise ValueError("Phase 2 JSON must be an object")
    scene_plan = parsed.get("scene_plan")
    if not isinstance(scene_plan, list):
        raise ValueError("Phase 2 must have 'scene_plan' array")
    if len(scene_plan) != phase1_segment_count:
        raise ValueError(
            f"Phase 2 scene_plan length ({len(scene_plan)}) must match Phase 1 segments ({phase1_segment_count})"
        )
    for i, scene in enumerate(scene_plan):
        if not isinstance(scene, dict):
            raise ValueError(f"Phase 2 scene_plan[{i}] must be an object")
        clo = scene.get("core_location_override")
        if clo is not None and not isinstance(clo, str):
            raise ValueError(
                f"Phase 2 scene_plan[{i}].core_location_override must be a string if present"
            )
        vs = scene.get("visual_strategy")
        if not isinstance(vs, dict):
            raise ValueError(f"Phase 2 scene_plan[{i}] must have 'visual_strategy' object")
        for key in ("primary_visual", "motion_style", "pacing_note"):
            if key not in vs:
                raise ValueError(f"Phase 2 scene_plan[{i}].visual_strategy must have '{key}'")
        _validate_scene_plan_visual_safety(vs, i)
        if character_anchor_hints and i < len(character_anchor_hints):
            hid = character_anchor_hints[i]
            if hid and str(hid).strip():
                of = (vs.get("opening_frame") or "").strip()
                if not of:
                    raise ValueError(
                        f"Phase 2 scene_plan[{i}].visual_strategy.opening_frame is required non-empty "
                        f"when character anchor hint is set (character_id={hid!r})"
                    )
        if phase1_segments and i < len(phase1_segments):
            seg_p1 = phase1_segments[i]
            if isinstance(seg_p1, dict):
                vm = normalize_visual_mode(seg_p1)
                th = (seg_p1.get("talking_head_subject") or "").strip().lower()
                if vm == VISUAL_MODE_TALKING_HEAD and th and character_portrait_asset_exists(th):
                    of_th = (vs.get("opening_frame") or "").strip()
                    if not of_th:
                        raise ValueError(
                            f"Phase 2 scene_plan[{i}].visual_strategy.opening_frame is required non-empty "
                            f"for talking_head segment (talking_head_subject={th!r}) so FAL scene-anchor i2i can run"
                        )
                if vm != VISUAL_MODE_TALKING_HEAD:
                    cf = (vs.get("closing_frame") or "").strip()
                    if not cf:
                        raise ValueError(
                            f"Phase 2 scene_plan[{i}].visual_strategy.closing_frame is required non-empty "
                            "for non-talking_head segments (rule 2 closing_frame)"
                        )
    if "open_questions" not in parsed:
        raise ValueError("Phase 2 must include 'open_questions' (array of strings, may be empty)")
    oq = parsed["open_questions"]
    if not isinstance(oq, list):
        raise ValueError("Phase 2 open_questions must be an array")
    for j, item in enumerate(oq):
        if not isinstance(item, str):
            raise ValueError(f"Phase 2 open_questions[{j}] must be a string")
    if "editor_notes" not in parsed:
        raise ValueError("Phase 2 must include 'editor_notes' (string, may be empty)")
    if not isinstance(parsed["editor_notes"], str):
        raise ValueError("Phase 2 editor_notes must be a string")
    validate_episode_visual_world(parsed.get("episode_visual_world"), phase1_segment_count)


# Phrases that routinely produce fused human+game stills in Wan scene-anchor i2i.
_BANNED_SCENE_VISUAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(?:gripping|holding|grasping|seizing)\b.{0,48}\b(?:deer|elk)\b.{0,24}\bantlers?\b"
        ),
        "hand-on-antler / gripping game language (causes person-deer fusion in i2i)",
    ),
    (
        re.compile(
            r"(?i)\b(?:deer|elk)\b.{0,24}\bantlers?\b.{0,48}\b(?:gripping|holding|grasping|seizing)\b"
        ),
        "antler-grip language (causes person-deer fusion in i2i)",
    ),
    (
        re.compile(
            r"(?i)\b(?:riding|straddling|mounted on|astride|sits? astride)\b.{0,40}"
            r"\b(?:deer|elk|buck|stag|game animal)\b"
        ),
        "riding/straddling game animal",
    ),
)


def _validate_scene_plan_visual_safety(vs: dict[str, Any], scene_index: int) -> None:
    """Reject opening_frame / primary_visual phrasing known to break FAL scene anchors."""
    blob = " ".join(
        [
            str(vs.get("opening_frame") or ""),
            str(vs.get("primary_visual") or ""),
            str(vs.get("secondary_elements") or ""),
        ]
    )
    for pattern, label in _BANNED_SCENE_VISUAL_PATTERNS:
        if pattern.search(blob):
            raise ValueError(
                f"Phase 2 scene_plan[{scene_index}].visual_strategy uses banned {label}; "
                f"use hunt recipes (a)–(d) without carcass-in-hand or antler-grip language"
            )


def _call_api(
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    provider: str = "openai",
) -> str:
    return call_llm(provider, system_prompt, user_prompt, model, max_tokens, purpose="phase2")


def run_phase2(
    phase1: dict[str, Any],
    date_id: str,
    model: str = "gpt-4o",
    style: dict[str, str] | None = None,
    focus_topic: str | None = None,
    max_retries: int = MAX_RETRIES,
    user_suffix: str | None = None,
    character_anchor_hints: list[str | None] | None = None,
    *,
    focus_expedition_era: bool = True,
    historical_only_visuals: bool = True,
    seasonal_ambient_from_date_id: bool = True,
    repo_root: Path | None = None,
    narrations_dir: Path | None = None,
    prompt_pack: str = "lewis_clark",
    recent_episodes_digest: str | None = None,
    provider: str = "openai",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    root = repo_root or _REPO_ROOT
    narr_dir = narrations_dir if narrations_dir is not None else root / "narrations"
    map_block, map_log = compute_phase2_runtime_map_policy_block(phase1, date_id, narr_dir)
    if map_log:
        print(map_log, file=sys.stderr)
    system = build_phase2_system_prompt(
        focus_topic=focus_topic,
        character_anchor_hints=character_anchor_hints,
        focus_expedition_era=focus_expedition_era,
        historical_only_visuals=historical_only_visuals,
        repo_root=repo_root,
        prompt_pack=prompt_pack,
        runtime_map_policy_block=map_block,
    )
    user = build_phase2_user_prompt(
        phase1,
        date_id,
        style,
        focus_topic=focus_topic,
        character_anchor_hints=character_anchor_hints,
        seasonal_ambient_from_date_id=seasonal_ambient_from_date_id,
        focus_expedition_era=focus_expedition_era,
        prompt_pack=prompt_pack,
        recent_episodes_digest=recent_episodes_digest,
    )
    if user_suffix:
        user = f"{user}\n\n{user_suffix}"
    base_user = user
    n_segments = len(phase1.get("segments") or [])
    last_raw = None
    for attempt in range(max_retries + 1):
        raw = _call_api(system, user, model, max_tokens=max_tokens, provider=provider)
        cleaned = clean_json_reply(raw)
        last_raw = raw
        try:
            parsed = json.loads(cleaned)
            _validate_phase2(
                parsed,
                n_segments,
                character_anchor_hints,
                phase1_segments=phase1.get("segments"),
            )
            return parsed
        except (JSONDecodeError, ValueError):
            if attempt >= max_retries:
                break
            user = (
                f"{base_user}\n\n"
                "----------------------------------------------------------------\n"
                "RETRY: The previous response was invalid or did not match the required JSON schema.\n"
                "Fix it and return ONLY valid JSON, using the Phase 1 input above as source. "
                "Preserve segment count (one scene_plan entry per Phase 1 segment).\n"
                "If the error mentions banned hunt/antler language, rewrite that segment with hunt recipes "
                "(a)–(d) only—no gripping game by the antlers, no riding/straddling animals.\n\n"
                f"Previous (invalid) reply:\n{cleaned}"
            )
    # If we reach here, Phase 2 failed
    print("[ERROR] Phase 2 failed to produce valid JSON", file=sys.stderr)
    print(f"Date ID: {date_id}", file=sys.stderr)
    if last_raw:
        print("----- Raw reply begin -----", file=sys.stderr)
        print(last_raw[:2000], file=sys.stderr)
        print("----- Raw reply end -------", file=sys.stderr)
    raise RuntimeError(f"Phase 2 failed after {max_retries + 1} attempts for date {date_id}")


def _synthesize_video_prompt(vs: dict[str, Any]) -> str:
    """Concatenate Phase 2 visual_strategy fields into the vendor-facing video_prompt (physical micro-sequence).

    Each field is passed through ``strip_unrenderable_language`` first — a deterministic,
    best-effort cleanup of sound/abstraction language rule 2b/2c already forbids but the model
    sometimes emits anyway (ai-plans/fal-video-quality-followups-2026-09.md item 6).

    ``closing_frame`` (rule 2 closing_frame) is appended as an explicit end-state clause so the clip
    describes a trajectory (begin -> end) rather than a static situation—see
    ai-plans/fal-video-quality-followups-2026-09.md item 5.
    """
    primary = strip_unrenderable_language((vs.get("primary_visual") or "").strip())
    secondary = strip_unrenderable_language((vs.get("secondary_elements") or "").strip())
    parts = []
    if primary:
        parts.append(primary)
    if secondary:
        parts.append(secondary)
    text = " ".join(parts)
    closing = strip_unrenderable_language((vs.get("closing_frame") or "").strip()).rstrip(".")
    if closing:
        closing = closing[:1].lower() + closing[1:]
        clause = f"By the end of the shot, {closing}."
        text = f"{text} {clause}".strip() if text else clause
    return text


def _synthesize_stage_direction(vs: dict[str, Any]) -> str:
    """Build a short camera/mood cue only (no repeat of video_prompt)."""
    motion = vs.get("motion_style") or "static"
    return motion


def merge_phase1_phase2(
    phase1: dict[str, Any],
    phase2: dict[str, Any],
    style: dict[str, str] | None = None,
    character_config: dict[str, Any] | None = None,
    date_id: str = "",
    character_anchor_hints: list[str | None] | None = None,
    dialogue_mode: bool = False,
    *,
    long_conversation_mode: bool = False,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Build pipeline-shaped JSON and return (merged, used_counts) for character usage tracking."""
    segments_p1 = phase1.get("segments") or []
    scene_plan = phase2.get("scene_plan") or []
    if len(scene_plan) != len(segments_p1):
        raise ValueError(
            f"Segment count mismatch: Phase 1 has {len(segments_p1)}, Phase 2 scene_plan has {len(scene_plan)}"
        )

    # Sort scene_plan by segment_index if present
    scene_plan_sorted = sorted(
        scene_plan,
        key=lambda s: s.get("segment_index", 0),
    )

    scene_spine = build_scene_spine_from_phase2(phase1, phase2)

    ep_meta = (
        phase1.get("episode_metadata") if isinstance(phase1.get("episode_metadata"), dict) else {}
    )
    conv_setting_by_idx = conversation_run_setting_by_segment_index(
        phase1,
        segments_p1,
        long_conversation_mode=long_conversation_mode,
    )
    from pipeline.talking_head_prompt_merge import (
        build_merged_talking_head_prompt,
        ping_pong_talking_head_indices,
    )

    ping_pong_seg_1based = ping_pong_talking_head_indices(segments_p1)

    narration_script = []
    # Track how often each character is used this episode.
    used_counts: dict[str, int] = {}
    char_enabled = False
    max_per_episode = 0
    max_per_character = 0
    if character_config:
        char_enabled = bool(character_config.get("enabled", False))
        max_per_episode = int(character_config.get("max_per_episode", 0))
        max_per_character = int(character_config.get("max_per_character", 0))
    total_char_usages = 0
    for i, seg_p1 in enumerate(segments_p1):
        scene = scene_plan_sorted[i] if i < len(scene_plan_sorted) else {}
        vs = scene.get("visual_strategy") or {}
        narration = seg_p1.get("narration") or ""
        oframe = (vs.get("opening_frame") or "").strip()
        vm = normalize_visual_mode(seg_p1)
        video_prompt = _synthesize_video_prompt(vs)
        ct_raw = seg_p1.get("conversation_tracking")
        if isinstance(ct_raw, dict):
            dn_ct = str(ct_raw.get("director_note") or "").strip()
            if dn_ct and vm != VISUAL_MODE_TALKING_HEAD:
                video_prompt = f"{dn_ct} {video_prompt}".strip()
        reference_character_id: str | None = None
        th_sub = (seg_p1.get("talking_head_subject") or "").strip().lower()

        if (
            vm == VISUAL_MODE_TALKING_HEAD
            and th_sub
            and character_portrait_asset_exists(th_sub)
            and not oframe
        ):
            pv0 = (vs.get("primary_visual") or "").strip()
            if pv0:
                m = re.match(r"^([^.!?]+[.!?]?)", pv0)
                chunk = (m.group(1) if m else pv0[:240]).strip()
                oframe = (chunk or pv0[:240]).strip()
            if not oframe:
                loc = str(
                    (ep_meta.get("location_summary") or "").strip() or "expedition river or camp"
                )
                oframe = (
                    f"Head-and-shoulders period portrait, {th_sub}, relaxed neutral mouth before speech, "
                    f"steady eyes; soft natural depth suggesting {loc}, earth tones—single t=0 frozen instant only, "
                    "not a plain white void."
                )

        # Optional: inject character description into the visual prompt.
        if (
            vm != VISUAL_MODE_TALKING_HEAD
            and char_enabled
            and narration
            and max_per_episode > 0
            and max_per_character > 0
            and date_id
        ):
            if total_char_usages < max_per_episode:
                # Give the final (often reflection/closure) segment one extra per-character slot
                # so an explicit, portrait-backed lead (e.g. Clark on a bedroll) isn't starved.
                effective_max_per_character = max_per_character
                if i == len(segments_p1) - 1:
                    effective_max_per_character = max_per_character + 1

                match = find_character_match(
                    date_id,
                    narration,
                    used_counts,
                    max_per_character=effective_max_per_character,
                    video_prompt=video_prompt,
                )
                if match is not None:
                    cid = match.character.id
                    try:
                        from pipeline.dynamic_pair_scene_anchor import portrait_reference_ready

                        has_portrait = portrait_reference_ready(cid)
                    except Exception:
                        has_portrait = character_portrait_asset_exists(cid)
                    if not has_portrait:
                        # No portrait asset: don't spend character-injection quota or set image anchors.
                        # The base Phase 2 visual prompt should still describe the people/actions correctly.
                        pass
                    elif match.reason in ("pair_portrait", "dynamic_pair_portrait"):
                        # Composite portrait: require each pair member grounded in the visual (not only the merged name).
                        pm = pair_member_characters_for_composite(cid)
                        if pm is None or not all(
                            video_prompt_grounds_character(video_prompt, m) for m in pm
                        ):
                            pass
                        else:
                            reference_character_id = cid
                            used_counts[cid] = used_counts.get(cid, 0) + 1
                            total_char_usages += 1
                    elif match.reason == "pair_portrait_implicit":
                        trig = pair_implicit_trigger_for_composite(cid)
                        by_id = {c.id: c for c in load_characters()}
                        trigger_char = by_id.get(trig[0]) if trig else None
                        full = normalize_text((narration or "") + " " + (video_prompt or ""))
                        try:
                            pat_ok = bool(trig and re.search(trig[1], full))
                        except re.error:
                            pat_ok = False
                        if (
                            trigger_char is not None
                            and pat_ok
                            and video_prompt_grounds_character(video_prompt, trigger_char)
                        ):
                            reference_character_id = cid
                            used_counts[cid] = used_counts.get(cid, 0) + 1
                            total_char_usages += 1
                    elif not video_prompt_grounds_character(video_prompt, match.character):
                        # Narration matched a character, but Phase 2 visual is environment-only (no id/name/alias/keyword).
                        # Do not anchor a portrait when no human is described in the shot.
                        pass
                    else:
                        # Portrait-backed segment: FAL uses i2i→i2v from the portrait (see video_vendors.build_prompts).
                        # Phase 2 does not inject extra subject text here; for humans that's enough. For animals,
                        # build_prompts may still prepend Character reference (breed/build) so i2i stays on-breed.
                        reference_character_id = cid
                        used_counts[cid] = used_counts.get(cid, 0) + 1
                        total_char_usages += 1

        if vm == VISUAL_MODE_TALKING_HEAD and th_sub and character_portrait_asset_exists(th_sub):
            reference_character_id = th_sub

        if reference_character_id and not oframe:
            hint_seg: str | None = None
            if character_anchor_hints and i < len(character_anchor_hints):
                hint_seg = character_anchor_hints[i]
            if (hint_seg or "").strip():
                reference_character_id = None

        shared_conv_setting = conv_setting_by_idx.get(i)
        if shared_conv_setting and vm == VISUAL_MODE_TALKING_HEAD and th_sub:
            oframe = inject_conversation_backdrop(
                oframe,
                subject_id=th_sub,
                shared_setting=shared_conv_setting,
            )

        seg_out: dict[str, Any] = {
            "segment_index": i + 1,
            "stage_direction": _synthesize_stage_direction(vs),
            "narration": narration,
        }
        if vm != VISUAL_MODE_TALKING_HEAD:
            seg_out["video_prompt"] = video_prompt
        st_raw = seg_p1.get("segment_type")
        if isinstance(st_raw, str) and st_raw.strip():
            seg_out["segment_type"] = st_raw.strip()
        clo = scene.get("core_location_override")
        if isinstance(clo, str) and clo.strip() and i not in conv_setting_by_idx:
            seg_out["core_location_override"] = clo.strip()
        if oframe:
            seg_out["opening_frame"] = oframe
        if reference_character_id:
            seg_out["reference_character_id"] = reference_character_id
        dlg = seg_p1.get("dialogue")
        if isinstance(dlg, list) and dlg:
            seg_out["dialogue"] = [
                {k: v for k, v in row.items()} for row in dlg if isinstance(row, dict)
            ]
        seg_out["visual_mode"] = vm
        if isinstance(ct_raw, dict) and ct_raw:
            ct_copy: dict[str, Any] = {}
            for k, v in ct_raw.items():
                if isinstance(k, str) and k.strip():
                    ct_copy[k.strip()] = v
            if ct_copy:
                seg_out["conversation_tracking"] = ct_copy

        if vm == VISUAL_MODE_TALKING_HEAD and th_sub:
            seg_out["talking_head_subject"] = th_sub
            merged_thp = build_merged_talking_head_prompt(
                seg_p1,
                opening_frame=oframe,
                motion_style=str(vs.get("motion_style") or ""),
                in_ping_pong_run=(i + 1) in ping_pong_seg_1based,
                repo_root=repo_root,
            )
            if merged_thp:
                seg_out["talking_head_prompt"] = merged_thp
        post_narr = segment_post_narration_silence_seconds(
            seg_p1 if isinstance(seg_p1, dict) else {}
        )
        if post_narr > 0.0:
            seg_out["post_narration_silence_seconds"] = post_narr
        narration_script.append(seg_out)

    cap_post_narration_silence_in_script(narration_script)

    out = {
        "narration_version": "2.0",
        "dialogue_mode": bool(dialogue_mode),
        "long_conversation_mode": bool(long_conversation_mode),
        "title": phase1.get("title") or "",
        "scene_spine": scene_spine,
        "narration_script": narration_script,
        "visual_style": (
            {"name": style["name"], "description": style.get("description") or style["name"]}
            if style and style.get("name")
            else None
        ),
        "video_metadata": phase2.get("video_metadata"),
        "map_insertions": phase2.get("map_insertions"),
        "audio_design": phase2.get("audio_design"),
    }
    did_s = str(date_id or "").strip()
    if did_s:
        out["date_id"] = did_s
    oq_raw = phase2.get("open_questions") or []
    out["open_questions"] = [
        str(x).strip() for x in oq_raw if isinstance(x, str) and str(x).strip()
    ]
    out["editor_notes"] = str(phase2.get("editor_notes") or "").strip()
    # Optional: keep Phase 1 metadata for debugging
    if phase1.get("episode_metadata"):
        out["episode_metadata"] = phase1["episode_metadata"]
    if phase1.get("narrative_spine"):
        out["narrative_spine"] = phase1["narrative_spine"]
    if phase1.get("conversation_micro_arc"):
        out["conversation_micro_arc"] = phase1["conversation_micro_arc"]
    if phase1.get("closing_type"):
        out["closing_type"] = phase1["closing_type"]
    if phase1.get("tone_register"):
        out["tone_register"] = phase1["tone_register"]
    return out, used_counts


def build_voice_sidecar_document(phase1: dict[str, Any], date_id: str) -> dict[str, Any]:
    """Disk artifact: Phase 1 output plus ids (not merged with visuals)."""
    return {
        "sidecar_version": "1.0",
        "sidecar_role": "voice",
        "date_id": date_id,
        **phase1,
    }


def build_visual_sidecar_document(phase2: dict[str, Any], date_id: str) -> dict[str, Any]:
    """Disk artifact: Phase 2 visual plan + editor telemetry (not merged with voice)."""
    return {
        "sidecar_version": "1.0",
        "sidecar_role": "visual",
        "date_id": date_id,
        "video_metadata": phase2.get("video_metadata"),
        "episode_visual_world": phase2.get("episode_visual_world"),
        "scene_plan": phase2.get("scene_plan"),
        "map_insertions": phase2.get("map_insertions"),
        "audio_design": phase2.get("audio_design"),
        "open_questions": list(phase2.get("open_questions") or []),
        "editor_notes": phase2.get("editor_notes") or "",
    }


def merge_narration_sidecars_to_pipeline_json(
    phase1: dict[str, Any],
    phase2: dict[str, Any],
    *,
    style: dict[str, str] | None = None,
    character_config: dict[str, Any] | None = None,
    date_id: str = "",
    character_anchor_hints: list[str | None] | None = None,
    dialogue_mode: bool = False,
    long_conversation_mode: bool = False,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """
    Stitch voice (phase1) + visual (phase2) JSON dicts into canonical pipeline narration JSON.
    Same as merge_phase1_phase2; exposed for tooling that loads sidecar files from disk.
    """
    return merge_phase1_phase2(
        phase1,
        phase2,
        style=style,
        character_config=character_config,
        date_id=date_id,
        character_anchor_hints=character_anchor_hints,
        dialogue_mode=dialogue_mode,
        long_conversation_mode=long_conversation_mode,
        repo_root=repo_root,
    )
