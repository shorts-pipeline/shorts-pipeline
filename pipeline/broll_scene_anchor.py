"""B-roll Wan scene-anchor stills: portrait-backed segments by default."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pipeline.narration_common import load_narration_config
from pipeline.narration_utils import narration_script_row_1based
from pipeline.narration_visual_mode import VISUAL_MODE_B_ROLL, normalize_visual_mode

if TYPE_CHECKING:
    from pipeline.segment_plan import EpisodeSegmentPlans, SegmentPlan


def broll_scene_anchor_enabled() -> bool:
    """When True, all B-roll segments may use scene anchors (not only portrait-backed)."""
    cfg = load_narration_config() or {}
    block = cfg.get("fal_broll_scene_anchor")
    if not isinstance(block, dict):
        return False
    return bool(block.get("enabled"))


def segment_anchor_character_id(row: dict[str, Any]) -> str:
    """Resolve the character id used for scene anchors on this narration row."""
    th = str(row.get("talking_head_subject") or "").strip().lower()
    ref = str(row.get("reference_character_id") or "").strip().lower()
    return th or ref


def segment_has_portrait_backed_character(row: dict[str, Any]) -> bool:
    """True when the segment names a character with a portrait (solo or dynamic pair)."""
    cid = segment_anchor_character_id(row)
    if not cid:
        return False
    try:
        from pipeline.dynamic_pair_scene_anchor import portrait_reference_ready

        return portrait_reference_ready(cid)
    except Exception:
        return False


def broll_uses_scene_anchor(
    *,
    visual_mode: str,
    segment_row: dict[str, Any] | None = None,
    character_id: str = "",
) -> bool:
    """
    Whether a segment should build or consume a scene-anchor still.

    Talking-head segments are always eligible. B-roll segments are eligible when
    ``fal_broll_scene_anchor.enabled`` is true, or when the segment has a
    portrait-backed ``reference_character_id`` / ``talking_head_subject``.
    """
    mode = (visual_mode or VISUAL_MODE_B_ROLL).strip().lower()
    if mode != VISUAL_MODE_B_ROLL:
        return True
    if broll_scene_anchor_enabled():
        return True
    if segment_row is not None:
        return segment_has_portrait_backed_character(segment_row)
    cid = (character_id or "").strip().lower()
    if not cid:
        return False
    try:
        from pipeline.dynamic_pair_scene_anchor import portrait_reference_ready

        return portrait_reference_ready(cid)
    except Exception:
        return False


def scene_anchor_eligible_for_visual_mode(
    visual_mode: str,
    *,
    segment_row: dict[str, Any] | None = None,
) -> bool:
    return broll_uses_scene_anchor(visual_mode=visual_mode, segment_row=segment_row)


def segment_is_b_roll(narr: dict[str, Any] | None, segment_index: int) -> bool:
    row = narration_script_row_1based(narr, segment_index) if narr else None
    if not row:
        return True
    return normalize_visual_mode(row) == VISUAL_MODE_B_ROLL


def plan_is_b_roll(plan: SegmentPlan) -> bool:
    """``SegmentPlan`` view of ``segment_is_b_roll``."""
    return plan.is_b_roll


def plan_eligible_for_scene_anchor(plan: SegmentPlan) -> bool:
    """``SegmentPlan`` view of ``segment_eligible_for_scene_anchor``."""
    return plan.scene_anchor_eligible


def scene_anchor_eligible_plans(episode: EpisodeSegmentPlans) -> tuple[SegmentPlan, ...]:
    """Episode segments that may receive a scene-anchor still."""
    return episode.scene_anchor_eligible()


def segment_eligible_for_scene_anchor(narr: dict[str, Any] | None, segment_index: int) -> bool:
    row = narration_script_row_1based(narr, segment_index) if narr else None
    if not row:
        return True
    return scene_anchor_eligible_for_visual_mode(
        normalize_visual_mode(row),
        segment_row=row,
    )
