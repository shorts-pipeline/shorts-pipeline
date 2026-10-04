"""Pluggable AI video generation vendors: sora, google, fal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.scene_spine import resolve_core_location_for_segment, resolve_lighting_for_segment

from .base import VideoVendor
from .fal import FalVendor
from .google import GoogleVendor
from .prompt_parts import NO_ON_SCREEN_TEXT_PREFIX, SAFETY_SUFFIX
from .sora import SoraVendor

VENDORS: dict[str, type] = {
    "sora": SoraVendor,
    "google": GoogleVendor,
    "fal": FalVendor,
}


def get_vendor(name: str, **kwargs) -> VideoVendor:
    """Return a vendor instance by name (sora, google, fal)."""
    name = name.lower()
    if name not in VENDORS:
        raise ValueError(f"Unknown vendor '{name}'. Available: {', '.join(VENDORS)}")
    return VENDORS[name](**kwargs)


# Keep the lighting/palette shards short — they ride in the highest-attention opening line
# of an 800-char (fal) / 500-char (Google) prompt budget alongside era, place, and mood.
_OPENING_LIGHTING_MAX_CHARS = 70
_OPENING_PALETTE_MAX_CHARS = 90


def _clipped(text: str, max_chars: int) -> str:
    t = (text or "").strip().rstrip(".")
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 1].rstrip() + "…"


def _opening_period_shot_prefix(
    scene_spine: dict | None,
    core_location_override: str | None = None,
    *,
    lighting: str | None = None,
    color_palette: str | None = None,
) -> str:
    """Short opening shard: era label + per-segment place + optional mood/lighting/palette.

    River-travel attitude, persistent anchors, and block continuity are planned in Phase 2
    and merged into each segment's ``video_prompt``—not injected here (avoids rowing/boat bleed
    on forest, hunt, and interior clips). Lighting and color grade are episode/block-planned
    (Phase 2 ``episode_visual_world.lighting_arc`` / block ``lighting`` / ``color_palette``) but
    otherwise unused downstream, so they ride in this opening line instead.
    """
    era = "early 1800's"
    mood = ""
    if scene_spine and isinstance(scene_spine, dict):
        mood = (scene_spine.get("visual_mood") or "").strip()
    loc = ""
    ovr = (core_location_override or "").strip()
    if ovr:
        loc = ovr
    elif scene_spine and isinstance(scene_spine, dict):
        loc = (scene_spine.get("core_location") or "").strip()
    if loc:
        parts = f"Photorealistic period scene set in {era}, {loc}"
    else:
        parts = f"Photorealistic period scene set in {era}, American frontier river country"
    if mood:
        parts += f", overall mood {mood}"
    lit = _clipped(lighting or "", _OPENING_LIGHTING_MAX_CHARS)
    if lit:
        parts += f". Lighting: {lit}"
    pal = _clipped(color_palette or "", _OPENING_PALETTE_MAX_CHARS)
    if pal:
        parts += f". Color grade: {pal}"
    return parts + ". "


def _narration_script_row_for_1based(
    data: dict[str, Any], segment_index: int
) -> dict[str, Any] | None:
    """Return ``narration_script`` row for logical 1-based segment index (same rules as b-roll suggest)."""
    script = data.get("narration_script") or []
    if not isinstance(script, list):
        return None
    for j, seg in enumerate(script):
        if not isinstance(seg, dict):
            continue
        raw_idx = seg.get("segment_index")
        if raw_idx is None:
            seg_i = j + 1
        else:
            try:
                seg_i = int(raw_idx)
            except (TypeError, ValueError):
                seg_i = j + 1
        if seg_i == segment_index:
            return seg
    return None


def opening_period_line_for_narration_segment(
    data: dict[str, Any],
    segment_index: int,
    *,
    date_id: str | None = None,
) -> str:
    """
    Opening location/mood sentence for one logical segment (FAL scene-anchor i2i ``world_prefix``,
    must match ``build_prompts`` for that segment). Uses ``core_location_override`` on the row when set.
    """
    spine = data.get("scene_spine") if isinstance(data.get("scene_spine"), dict) else {}
    lighting = resolve_lighting_for_segment(spine, segment_index)
    color_palette = (spine.get("color_palette") or "").strip() if isinstance(spine, dict) else ""
    row = _narration_script_row_for_1based(data, segment_index)
    if not row:
        return _opening_period_shot_prefix(
            spine, None, lighting=lighting, color_palette=color_palette
        )
    ovr = row.get("core_location_override")
    loc_ovr = (ovr.strip() if isinstance(ovr, str) else "") or None
    resolved_loc = resolve_core_location_for_segment(spine, segment_index, loc_ovr)
    return _opening_period_shot_prefix(
        spine, resolved_loc or loc_ovr, lighting=lighting, color_palette=color_palette
    )


def _stage_cue_only(stage_direction: str) -> str:
    """Use only the camera/mood cue to avoid repeating video_prompt text."""
    stage = (stage_direction or "").strip()
    if not stage:
        return ""
    return stage


def _video_prompt_has_explicit_camera_language(video_prompt: str) -> bool:
    """True when the segment prompt already asks for camera motion — avoid doubling with motion prefix."""
    s = (video_prompt or "").lower()
    if not s.strip():
        return False
    needles = (
        "camera ",
        "camera.",
        "camera,",
        "pull back",
        "pullback",
        "push-in",
        "push in",
        "dolly",
        "pan ",
        "panning",
        "tracking shot",
        "zoom",
        "crane",
        "slowly pull",
    )
    return any(n in s for n in needles)


def _motion_cue_prose(motion_style: str) -> str:
    """Turn merged stage_direction / motion tokens into a short sentence (avoid Shot: labels)."""
    key = (motion_style or "").strip().lower().replace(" ", "_")
    prose = {
        "static": "Hold a steady, static frame. ",
        "slow_push": "Very slow push-in. ",
        "drift": "Very subtle camera drift. ",
        "tracking": "Slow, subtle tracking; avoid handheld shake. ",
        "parallax": "Subtle parallax only. ",
        "slow_pull": "Slow pull-back. ",
    }.get(key)
    if prose:
        return prose
    if key:
        return f"Subtle {key.replace('_', ' ')} motion. "
    return ""


def _portrait_exists(character_id: str) -> bool:
    """Return True when a portrait-backed FAL i2i anchor is possible (solo or dynamic pair)."""
    try:
        from pipeline.dynamic_pair_scene_anchor import portrait_reference_ready

        return portrait_reference_ready(character_id)
    except Exception:
        pass
    base = Path("character-portraits")
    cid = (character_id or "").strip()
    if not cid:
        return False
    for ext in (".png", ".jpg", ".jpeg"):
        if (base / f"{cid}{ext}").exists():
            return True
    return False


def _character_description_prefix(character_id: str) -> str:
    """Return a concise visual description for known characters (fallback when no portrait exists)."""
    cid = (character_id or "").strip().lower()
    if not cid:
        return ""
    try:
        from pipeline.narration_characters import load_characters
    except Exception:
        return ""
    for c in load_characters():
        if c.id.lower() == cid and c.physical_description:
            desc = (c.physical_description or "").strip()
            if not desc:
                return ""
            if _is_animal_character(character_id):
                return f"Keep this breed and build: {desc}. "
            return f"{desc}. "
    return ""


def _is_animal_character(character_id: str) -> bool:
    """
    True for expedition animals (dog, horse, etc.) — not humans.

    When a portrait exists we normally omit animal breed text for people (i2i+i2v
    is enough). Animals still benefit from explicit breed/build cues in the prompt because
    i2i can drift toward a generic breed (e.g. black Lab vs Newfoundland).
    """
    cid = (character_id or "").strip().lower()
    if not cid:
        return False
    # Known animal ids (expand if you add more non-human cast).
    if cid in ("seaman",):
        return True
    try:
        from pipeline.narration_characters import load_characters
    except Exception:
        return False
    animal_role_markers = frozenset({"dog", "animal", "horse", "mule", "ox", "livestock"})
    for c in load_characters():
        if c.id.lower() != cid:
            continue
        for r in c.roles:
            if str(r).strip().lower() in animal_role_markers:
                return True
        return False
    return False


def _is_composite_character(character_id: str) -> bool:
    """True when character_id maps to a two-person pair (configured or ad-hoc)."""
    cid = (character_id or "").strip().lower()
    if not cid:
        return False
    try:
        from pipeline.dynamic_pair_scene_anchor import is_dynamic_pair_composite

        return is_dynamic_pair_composite(cid)
    except Exception:
        pass
    try:
        from pipeline.narration_characters import composite_portrait_ids

        return cid in composite_portrait_ids()
    except Exception:
        return False


def _composite_separation_prefix(character_id: str) -> str:
    """Hard guardrail text to keep composite pair subjects visually distinct."""
    if not _is_composite_character(character_id):
        return ""
    if _is_animal_character(character_id):
        return ""
    return (
        "Two distinct people in frame, clearly separated bodies and faces, standing or sitting side by side. "
        "Preserve two visible heads, two torsos, and separate left/right arm boundaries at all times; "
        "no fused body, no merged face, no extra limbs, and no overlap that hides one subject. "
    )


def build_prompts(
    date_id: str,
    narrations_dir: Path | None = None,
    vendor: str | None = None,
) -> list[str]:
    """Load narrations/narration<date_id>.json and return list of video_prompt strings.
    Order (after any fal FAL_* markers): opening line (photorealistic period scene: early 1800's +
    scene_spine place/mood + lighting + color grade); short no-readable-text fragment; visual_style
    description; optional character snippet; motion sentence + video_prompt; family-friendly suffix.
    Text replacements are done in generate-narration before saving. Vendors may truncate
    (e.g. Google 500 chars, fal 800)."""
    dir_ = narrations_dir or Path("narrations")
    path = dir_ / f"narration{date_id}.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    segments = data.get("narration_script", [])
    vendor = (vendor or "").strip().lower() or None
    vs = data.get("visual_style")
    if vs and isinstance(vs, dict) and vs.get("name") and vs.get("description"):
        # Omit style *name* from the vendor string: short Title Case names read like labels the
        # model may paint on frame. Name stays in narration JSON for selection, CLI, analytics.
        # Description is written as a self-contained stylistic brief (often already names pace/motion).
        # Avoid "Aim for …" before style text that already states pacing/motion (redundant).
        dn = (vs["description"] or "").strip().rstrip(".")
        style_prefix = f"{dn}. "
    else:
        style_prefix = ""
    spine_d = data.get("scene_spine") if isinstance(data.get("scene_spine"), dict) else {}
    result = []
    for j, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        raw_idx = seg.get("segment_index")
        if raw_idx is None:
            seg_i = j + 1
        else:
            try:
                seg_i = int(raw_idx)
            except (TypeError, ValueError):
                seg_i = j + 1
        ovr = seg.get("core_location_override")
        loc_ovr = (ovr.strip() if isinstance(ovr, str) else "") or None
        resolved_loc = resolve_core_location_for_segment(spine_d, seg_i, loc_ovr)
        lighting = resolve_lighting_for_segment(spine_d, seg_i)
        color_palette = (spine_d.get("color_palette") or "").strip()
        opening_prefix = _opening_period_shot_prefix(
            spine_d,
            resolved_loc or loc_ovr,
            lighting=lighting,
            color_palette=color_palette,
        )
        prompt = seg.get("video_prompt") or ""
        cue = _stage_cue_only(seg.get("stage_direction") or "")
        motion_sentence = _motion_cue_prose(cue) if cue else ""
        if motion_sentence and not _video_prompt_has_explicit_camera_language(prompt):
            prompt = motion_sentence + prompt
        marker = ""
        character_prefix = ""
        if vendor == "fal":
            cid = (seg.get("reference_character_id") or "").strip()
            if cid:
                if _portrait_exists(cid):
                    marker = f"FAL_IMAGE_CHAR={cid} "
                    # Always run image-to-image before image-to-video when we have a portrait:
                    # builds a full-scene first frame instead of a transparent cutout on black.
                    marker += "FAL_SCENE_ANCHOR=1 "
                    # People: description omitted (portrait + i2i is enough). Animals: keep
                    # physical_description in the prompt so i2i/i2v stay on-breed.
                    if _is_animal_character(cid):
                        character_prefix = _character_description_prefix(cid)
                    else:
                        # Composite portraits need an explicit anti-fusion constraint so i2i/i2v
                        # does not collapse two humans into one blended figure.
                        character_prefix = _composite_separation_prefix(cid)
                else:
                    # Keep character grounding even without a portrait asset.
                    character_prefix = _character_description_prefix(cid)
        result.append(
            marker
            + opening_prefix
            + NO_ON_SCREEN_TEXT_PREFIX
            + style_prefix
            + character_prefix
            + prompt
            + SAFETY_SUFFIX
        )
    return result


def load_fal_negative_extras(
    date_id: str,
    narrations_dir: Path | None = None,
) -> list[str | None]:
    """Per-segment optional ``fal_negative_extra`` strings (parallel to ``build_prompts`` order)."""
    dir_ = narrations_dir or Path("narrations")
    path = dir_ / f"narration{date_id}.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    segments = data.get("narration_script", [])
    extras: list[str | None] = []
    for seg in segments:
        if not isinstance(seg, dict):
            extras.append(None)
            continue
        raw = seg.get("fal_negative_extra")
        extras.append(raw.strip() if isinstance(raw, str) and raw.strip() else None)
    return extras


def load_fal_scene_anchor_i2i_meta(
    date_id: str,
    narrations_dir: Path | None = None,
) -> tuple[list[str | None], list[str], list[str | None]]:
    """
    Per-segment optional opening_frame strings for FAL i2i (scene anchor), plus per-segment opening
    period-shot lines (era + location + optional mood) matching ``build_prompts``.

    Order matches build_prompts (narration_script iteration order). Each opening entry is stripped text or None.
    Each world prefix uses ``segment.core_location_override`` when set, else ``scene_spine.core_location``.
    The third list is the raw ``core_location_override`` string per segment (or None).
    """
    dir_ = narrations_dir or Path("narrations")
    path = dir_ / f"narration{date_id}.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    segments = data.get("narration_script", [])
    openings: list[str | None] = []
    worlds: list[str] = []
    core_overrides: list[str | None] = []
    for j, seg in enumerate(segments):
        if not isinstance(seg, dict):
            openings.append(None)
            worlds.append(
                opening_period_line_for_narration_segment(data, j + 1, date_id=str(date_id))
            )
            core_overrides.append(None)
            continue
        raw = seg.get("opening_frame")
        if isinstance(raw, str) and raw.strip():
            openings.append(raw.strip())
        else:
            openings.append(None)
        raw_idx = seg.get("segment_index")
        if raw_idx is None:
            seg_i = j + 1
        else:
            try:
                seg_i = int(raw_idx)
            except (TypeError, ValueError):
                seg_i = j + 1
        worlds.append(opening_period_line_for_narration_segment(data, seg_i, date_id=str(date_id)))
        clo = seg.get("core_location_override")
        core_overrides.append(clo.strip() if isinstance(clo, str) and clo.strip() else None)
    return openings, worlds, core_overrides
