"""
Build merged ``scene_spine`` from Phase 2 ``episode_visual_world`` and vendor continuity suffixes.
"""

from __future__ import annotations

from typing import Any


def _strip_str(v: Any) -> str:
    return str(v or "").strip()


def block_for_segment(
    scene_spine: dict[str, Any] | None,
    segment_index: int,
) -> dict[str, Any] | None:
    """Return the visual_blocks entry covering ``segment_index`` (1-based), if any."""
    if not scene_spine or not isinstance(scene_spine, dict):
        return None
    blocks = scene_spine.get("visual_blocks")
    if not isinstance(blocks, list):
        return None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        indices = block.get("segment_indices")
        if not isinstance(indices, list):
            continue
        try:
            nums = {int(x) for x in indices}
        except (TypeError, ValueError):
            continue
        if segment_index in nums:
            return block
    return None


def resolve_core_location_for_segment(
    scene_spine: dict[str, Any] | None,
    segment_index: int,
    core_location_override: str | None = None,
) -> str:
    """Opening-line location: override, else block place_label, else spine core_location."""
    ovr = _strip_str(core_location_override)
    if ovr:
        return ovr
    block = block_for_segment(scene_spine, segment_index)
    if block:
        pl = _strip_str(block.get("place_label"))
        if pl:
            return pl
    if scene_spine and isinstance(scene_spine, dict):
        return _strip_str(scene_spine.get("core_location"))
    return ""


def resolve_lighting_for_segment(
    scene_spine: dict[str, Any] | None,
    segment_index: int,
) -> str:
    """Opening-line lighting: block's own ``lighting``, else spine ``lighting_progression``."""
    block = block_for_segment(scene_spine, segment_index)
    if block:
        lt = _strip_str(block.get("lighting"))
        if lt:
            return lt
    if scene_spine and isinstance(scene_spine, dict):
        return _strip_str(scene_spine.get("lighting_progression"))
    return ""


def continuity_prompt_suffix(
    scene_spine: dict[str, Any] | None,
    segment_index: int,
    *,
    max_chars: int = 160,
) -> str:
    """
    Legacy helper: block note + persistent anchors as a vendor suffix.

    **Not used by** ``video_vendors.build_prompts`` (continuity belongs in Phase 2
    ``video_prompt`` text). Kept for tests and optional tooling.
    """
    if not scene_spine or not isinstance(scene_spine, dict):
        return ""
    parts: list[str] = []
    block = block_for_segment(scene_spine, segment_index)
    if block:
        note = _strip_str(block.get("continuity_note"))
        bid = _strip_str(block.get("block_id"))
        if note:
            parts.append(note)
        elif bid:
            parts.append(f"same visual block {bid}")
    anchors = scene_spine.get("persistent_anchors")
    if isinstance(anchors, list):
        kept = [_strip_str(a) for a in anchors if _strip_str(a)][:2]
        if kept:
            parts.append("Keep visible: " + "; ".join(kept) + ".")
    if not parts:
        env = _strip_str(scene_spine.get("environmental_elements"))
        if env and len(env) <= 120:
            parts.append(env)
    if not parts:
        return ""
    text = " Continuity: " + " ".join(parts)
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "."
    return text


def build_scene_spine_from_phase2(
    phase1: dict[str, Any],
    phase2: dict[str, Any],
) -> dict[str, Any]:
    """Merge Phase 2 episode_visual_world into pipeline scene_spine (fallback if absent)."""
    ep_meta = (
        phase1.get("episode_metadata") if isinstance(phase1.get("episode_metadata"), dict) else {}
    )
    vid_meta = (
        phase2.get("video_metadata") if isinstance(phase2.get("video_metadata"), dict) else {}
    )
    fallback_loc = _strip_str(ep_meta.get("location_summary")) or "Expedition location"
    overall_tone = _strip_str(vid_meta.get("overall_visual_tone")) or "grounded"

    evw = phase2.get("episode_visual_world")
    if not isinstance(evw, dict):
        return {
            "core_location": fallback_loc,
            "environmental_elements": "Period-appropriate boats, camp, crew; early 1800s frontier.",
            "lighting_progression": "Dawn to dusk, natural light.",
            "color_palette": "Earth tones, muted greens and browns.",
            "visual_mood": overall_tone,
        }

    primary_set = _strip_str(evw.get("primary_set"))
    lighting_arc = (
        _strip_str(evw.get("lighting_arc")) or "Natural light progression matching narration."
    )
    anchors_raw = evw.get("persistent_anchors")
    anchors: list[str] = []
    if isinstance(anchors_raw, list):
        anchors = [_strip_str(a) for a in anchors_raw if _strip_str(a)][:4]

    blocks_raw = evw.get("visual_blocks")
    blocks: list[dict[str, Any]] = []
    if isinstance(blocks_raw, list):
        for b in blocks_raw:
            if isinstance(b, dict):
                blocks.append(b)

    core_location = fallback_loc
    if blocks:
        pl = _strip_str(blocks[0].get("place_label"))
        if pl:
            core_location = pl

    if primary_set:
        environmental_elements = (
            primary_set if len(primary_set) <= 280 else primary_set[:277] + "..."
        )
    else:
        environmental_elements = "Period-appropriate boats, camp, crew; early 1800s frontier."

    color_palette = _strip_str(evw.get("color_palette")) or "Earth tones, muted greens and browns."

    out: dict[str, Any] = {
        "core_location": core_location,
        "environmental_elements": environmental_elements,
        "lighting_progression": lighting_arc,
        "color_palette": color_palette,
        "visual_mood": overall_tone,
    }
    if anchors:
        out["persistent_anchors"] = anchors
    if blocks:
        out["visual_blocks"] = blocks
    return out


def validate_episode_visual_world(
    evw: Any,
    segment_count: int,
) -> None:
    """Raise ValueError when Phase 2 episode_visual_world is missing or invalid."""
    if not isinstance(evw, dict):
        raise ValueError("Phase 2 must include 'episode_visual_world' object")
    if not _strip_str(evw.get("primary_set")):
        raise ValueError("episode_visual_world.primary_set must be a non-empty string")
    if not _strip_str(evw.get("lighting_arc")):
        raise ValueError("episode_visual_world.lighting_arc must be a non-empty string")
    if not _strip_str(evw.get("color_palette")):
        raise ValueError("episode_visual_world.color_palette must be a non-empty string")
    anchors = evw.get("persistent_anchors")
    if not isinstance(anchors, list) or len(anchors) < 2:
        raise ValueError(
            "episode_visual_world.persistent_anchors must be an array of at least 2 strings"
        )
    for j, a in enumerate(anchors):
        if not _strip_str(a):
            raise ValueError(f"episode_visual_world.persistent_anchors[{j}] must be non-empty")
    blocks = evw.get("visual_blocks")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("episode_visual_world.visual_blocks must be a non-empty array")
    covered: set[int] = set()
    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise ValueError(f"episode_visual_world.visual_blocks[{i}] must be an object")
        if not _strip_str(block.get("block_id")):
            raise ValueError(f"episode_visual_world.visual_blocks[{i}].block_id required")
        if not _strip_str(block.get("place_label")):
            raise ValueError(f"episode_visual_world.visual_blocks[{i}].place_label required")
        if not _strip_str(block.get("continuity_note")):
            raise ValueError(f"episode_visual_world.visual_blocks[{i}].continuity_note required")
        if not _strip_str(block.get("lighting")):
            raise ValueError(f"episode_visual_world.visual_blocks[{i}].lighting required")
        indices = block.get("segment_indices")
        if not isinstance(indices, list) or not indices:
            raise ValueError(
                f"episode_visual_world.visual_blocks[{i}].segment_indices must be a non-empty array"
            )
        for j, raw in enumerate(indices):
            try:
                n = int(raw)
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"episode_visual_world.visual_blocks[{i}].segment_indices[{j}] must be int"
                ) from e
            if n < 1 or n > segment_count:
                raise ValueError(
                    f"episode_visual_world.visual_blocks[{i}] segment_index {n} out of range 1..{segment_count}"
                )
            covered.add(n)
    if covered != set(range(1, segment_count + 1)):
        missing = sorted(set(range(1, segment_count + 1)) - covered)
        extra = sorted(covered - set(range(1, segment_count + 1)))
        raise ValueError(
            f"episode_visual_world.visual_blocks must cover every segment 1..{segment_count}; "
            f"missing={missing}, extra={extra}"
        )
