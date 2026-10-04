"""
Shared master scene anchor for long-conversation talking_head runs.

One composite portrait i2i establishes the shared backdrop. Per-speaker delivery
uses split crops (``shared_master_split``), a reframed Shorts master plus masks
(``shared_master_mask``), or bookend two-shot + middle splits (``shared_master_bookend``).
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from pipeline.conversation_visual_spine import (
    find_talking_head_conversation_runs,
    inject_conversation_backdrop,
    normalize_setting_state_drift,
    raw_conversation_shared_setting,
    resolve_conversation_master_expression,
    resolve_conversation_shared_setting,
    sync_conversation_place_in_scene_spine,
)
from pipeline.narration_characters.storage import (
    character_portrait_asset_exists,
    lookup_composite_pair,
)
from pipeline.narration_utils import narration_script_row_1based
from pipeline.narration_visual_mode import VISUAL_MODE_TALKING_HEAD, normalize_visual_mode
from pipeline.portrait_composite import ensure_composite_portrait_cached


@dataclass(frozen=True)
class TalkingHeadAnchorResolution:
    """Resolved still (+ optional OmniHuman mask) for one talking_head segment."""

    image_url: str | None = None
    image_path: Path | None = None
    mask_path: Path | None = None


@dataclass(frozen=True)
class ConversationAnchorRun:
    """One ping-pong talking_head exchange with a shared composite master still."""

    first_segment_index: int
    segment_indices: tuple[int, ...]
    composite_id: str
    left_speaker_id: str
    right_speaker_id: str
    shared_setting: str
    master_expression: str = ""


# Split-master i2i: camera geometry only — facial mood comes from narration (master_expression).
_CONVERSATION_MASTER_GAZE_PHRASE = (
    "Each in three-quarter view, turned slightly toward the other with partial eye contact "
    "toward the camera."
)

_BACKDROP_SUFFIX_RE = re.compile(
    r"\s*Same fixed backdrop throughout this conversation:.*$",
    re.IGNORECASE | re.DOTALL,
)


def _conversation_scene_anchor_config() -> dict[str, Any]:
    try:
        from pipeline.narration_common import load_narration_config

        block = (load_narration_config() or {}).get("fal_conversation_scene_anchor")
        if isinstance(block, dict):
            return block
    except Exception:
        pass
    return {}


def conversation_scene_anchor_enabled() -> bool:
    """Feature gate via ``fal_conversation_scene_anchor.enabled`` in narration config."""
    block = _conversation_scene_anchor_config()
    if "enabled" in block:
        return bool(block["enabled"])
    return True


def conversation_scene_anchor_strategy() -> str:
    """
    How long-conversation talking_head scene anchors are built.

    ``solo_scene_anchor``: one FAL i2i per turn from that speaker's solo
    portrait — best portrait likeness; shared deck/backdrop comes from identical
    ``opening_frame`` / ``conversation_micro_arc.shared_setting`` text.

    ``shared_master_split`` (default): one dual-portrait master i2i, then split into
    per-speaker anchors — shared backdrop pixels across the conversation run.

    ``shared_master_mask``: one master i2i, reframe to Shorts size, per-turn white
    ellipse masks for OmniHuman v1.5 (both speakers stay in frame; no crop split).

    ``shared_master_bookend``: wide master i2i; first/last turns use reframed two-shot
    + mask; middle turns use per-speaker split crops.
    """
    raw = _strip(_conversation_scene_anchor_config().get("strategy")).lower()
    if raw in ("solo_scene_anchor", "solo", "per_speaker"):
        return "solo_scene_anchor"
    if raw in ("shared_master_bookend", "master_bookend", "bookend"):
        return "shared_master_bookend"
    if raw in ("shared_master_mask", "master_mask", "omnihuman_mask"):
        return "shared_master_mask"
    if raw in ("shared_master_split", "master_split", "composite_master"):
        return "shared_master_split"
    return "shared_master_split"


def conversation_uses_shared_master_bookend() -> bool:
    """True when conversation runs use bookend two-shot + middle split crops."""
    return (
        conversation_scene_anchor_enabled()
        and conversation_scene_anchor_strategy() == "shared_master_bookend"
    )


def conversation_uses_shared_master_mask() -> bool:
    """True when conversation runs use shared master + per-speaker OmniHuman masks."""
    return (
        conversation_scene_anchor_enabled()
        and conversation_scene_anchor_strategy() == "shared_master_mask"
    )


def conversation_uses_shared_master_split() -> bool:
    """True when conversation runs should use composite master i2i + per-speaker split."""
    return (
        conversation_scene_anchor_enabled()
        and conversation_scene_anchor_strategy() == "shared_master_split"
    )


def conversation_uses_shared_conversation_master() -> bool:
    """True for shared-master split, mask, or bookend conversation strategies."""
    return (
        conversation_uses_shared_master_split()
        or conversation_uses_shared_master_mask()
        or conversation_uses_shared_master_bookend()
    )


def conversation_master_reference_mode() -> str:
    """
    How Wan receives speaker portraits for the shared conversation master still.

    ``dual_portrait`` (default): two solo portrait data URIs (left then right) — best likeness.
    ``composite_png``: single side-by-side composite plate (legacy; faces often drift).
    """
    raw = _strip(_conversation_scene_anchor_config().get("master_reference_mode")).lower()
    if raw in ("composite_png", "composite", "single_plate"):
        return "composite_png"
    return "dual_portrait"


def conversation_master_portrait_data_uris(
    repo_root: Path,
    run: ConversationAnchorRun,
) -> list[str]:
    """Solo portrait data URIs for left then right speaker (Wan dual-reference i2i)."""
    from video_vendors.fal import FalVendor

    uris: list[str] = []
    for speaker_id in (run.left_speaker_id, run.right_speaker_id):
        portrait_path = FalVendor._portrait_path_for_character(repo_root, speaker_id)
        if portrait_path is None:
            raise FileNotFoundError(
                f"No portrait for conversation speaker {speaker_id!r} under character-portraits/"
            )
        uris.append(FalVendor._data_uri_for_image(portrait_path))
    return uris


def _strip(v: Any) -> str:
    return str(v or "").strip()


def _speaker_id_for_segment(row: dict[str, Any]) -> str:
    subj = _strip(row.get("talking_head_subject")).lower()
    if subj:
        return subj
    return _strip(row.get("reference_character_id")).lower()


def discover_conversation_anchor_runs(narr: dict[str, Any]) -> list[ConversationAnchorRun]:
    """
    Find talking_head conversation runs eligible for composite master anchors.

    Requires ``long_conversation_mode``, exactly two distinct speakers per run, and solo
    portraits for both (composite PNG may be built and cached at anchor time).
    """
    if not narr.get("long_conversation_mode"):
        return []
    if not conversation_scene_anchor_enabled():
        return []
    script = narr.get("narration_script")
    if not isinstance(script, list):
        return []

    runs_idx = find_talking_head_conversation_runs(script)
    if not runs_idx:
        return []

    out: list[ConversationAnchorRun] = []
    for run_indices in runs_idx:
        speakers: list[str] = []
        seg_1based: list[int] = []
        for idx in run_indices:
            if idx < 0 or idx >= len(script) or not isinstance(script[idx], dict):
                continue
            row = script[idx]
            if normalize_visual_mode(row) != VISUAL_MODE_TALKING_HEAD:
                continue
            sid = _speaker_id_for_segment(row)
            if not sid:
                continue
            seg_1based.append(idx + 1)
            if sid not in speakers:
                speakers.append(sid)
        if len(speakers) != 2 or len(seg_1based) < 2:
            continue
        resolved = lookup_composite_pair(speakers[0], speakers[1])
        if resolved is None:
            continue
        composite_id, left_id, right_id = resolved
        if not character_portrait_asset_exists(left_id) or not character_portrait_asset_exists(
            right_id
        ):
            continue
        setting = raw_conversation_shared_setting(narr) or resolve_conversation_shared_setting(
            narr, script, run_indices
        )
        if not setting:
            continue
        expression = resolve_conversation_master_expression(narr, script, run_indices)
        first = min(seg_1based)
        out.append(
            ConversationAnchorRun(
                first_segment_index=first,
                segment_indices=tuple(sorted(seg_1based)),
                composite_id=composite_id,
                left_speaker_id=left_id,
                right_speaker_id=right_id,
                shared_setting=setting,
                master_expression=expression,
            )
        )
    return out


def segment_to_conversation_run(
    runs: list[ConversationAnchorRun],
) -> dict[int, ConversationAnchorRun]:
    """Map 1-based segment index -> conversation run."""
    out: dict[int, ConversationAnchorRun] = {}
    for run in runs:
        for si in run.segment_indices:
            out[si] = run
    return out


def conversation_run_for_segment(
    narr: dict[str, Any],
    segment_index: int,
) -> ConversationAnchorRun | None:
    """Return the conversation anchor run containing ``segment_index``, if any."""
    if segment_index < 1:
        return None
    return segment_to_conversation_run(discover_conversation_anchor_runs(narr)).get(segment_index)


def conversation_anchor_ui_enrichment(narr: dict[str, Any]) -> dict[str, Any]:
    """
    UI metadata for shared-backdrop conversation runs (Produce / Shots / anchor preview).

    Returns ``conversation_runs`` (summary list) and ``conversation_by_segment`` (1-based keys as strings).
    """
    runs = discover_conversation_anchor_runs(narr)
    run_map = segment_to_conversation_run(runs)
    runs_payload: list[dict[str, Any]] = []
    seen_first: set[int] = set()
    for run in runs:
        if run.first_segment_index in seen_first:
            continue
        seen_first.add(run.first_segment_index)
        setting = _strip(run.shared_setting)
        runs_payload.append(
            {
                "first_segment_index": run.first_segment_index,
                "segment_indices": list(run.segment_indices),
                "composite_id": run.composite_id,
                "left_speaker_id": run.left_speaker_id,
                "right_speaker_id": run.right_speaker_id,
                "shared_setting": setting,
                "shared_setting_preview": setting[:120] + ("…" if len(setting) > 120 else ""),
            }
        )
    by_segment: dict[str, dict[str, Any]] = {}
    for si, run in run_map.items():
        by_segment[str(si)] = {
            "conversation_anchor_run": True,
            "conversation_run_first_segment": run.first_segment_index,
            "conversation_run_segments": list(run.segment_indices),
            "conversation_composite_id": run.composite_id,
        }
    return {
        "conversation_runs": runs_payload,
        "conversation_by_segment": by_segment,
    }


def _strip_conversation_backdrop_suffix(opening_frame: str) -> str:
    return _BACKDROP_SUFFIX_RE.sub("", _strip(opening_frame)).strip()


def apply_conversation_shared_setting_update(
    narr: dict[str, Any],
    shared_setting: str,
    *,
    run_index: int = 0,
) -> dict[str, Any]:
    """
    Update ``conversation_micro_arc.shared_setting`` and re-sync per-turn ``opening_frame``
    / ``conversation_tracking.setting_state`` for one discovered anchor run.

    Returns a summary dict for the UI (segment indices touched).
    """
    setting = _strip(shared_setting)
    if not setting:
        raise ValueError("shared_setting must be non-empty")
    runs = discover_conversation_anchor_runs(narr)
    if not runs:
        raise ValueError("no conversation anchor runs in this narration")
    if run_index < 0 or run_index >= len(runs):
        raise ValueError(f"run_index out of range (have {len(runs)} run(s))")
    run = runs[run_index]
    script = narr.get("narration_script")
    if not isinstance(script, list):
        raise ValueError("narration_script missing")

    arc = narr.get("conversation_micro_arc")
    if not isinstance(arc, dict):
        arc = {}
        narr["conversation_micro_arc"] = arc
    arc["shared_setting"] = setting
    arc["persistent_props"] = []
    arc["spatial_relationship"] = (
        "Both remain in the same vicinity within arm's reach; "
        "eyeline alternates, location does not."
    )
    sync_conversation_place_in_scene_spine(narr, list(run.segment_indices), setting)
    if not arc.get("speakers"):
        arc["speakers"] = sorted({run.left_speaker_id, run.right_speaker_id})
    if not arc.get("segment_indices"):
        arc["segment_indices"] = list(run.segment_indices)

    run_indices_0 = [si - 1 for si in run.segment_indices]
    normalize_setting_state_drift(script, run_indices_0, setting)

    updated_segments: list[int] = []
    for si in run.segment_indices:
        row = narration_script_row_1based(narr, si)
        if not row:
            continue
        subj = _speaker_id_for_segment(row)
        base_frame = _strip_conversation_backdrop_suffix(str(row.get("opening_frame") or ""))
        row["opening_frame"] = inject_conversation_backdrop(
            base_frame,
            subject_id=subj,
            shared_setting=setting,
        )
        updated_segments.append(si)

    return {
        "run_index": run_index,
        "segment_indices": list(run.segment_indices),
        "updated_opening_frame_segments": updated_segments,
        "shared_setting": setting,
    }


def master_anchor_path(output_dir: Path, run: ConversationAnchorRun) -> Path:
    return (
        output_dir
        / "anchors"
        / f"_conv_{run.first_segment_index:02d}_{run.composite_id}_master.png"
    )


def manifest_path(output_dir: Path, run: ConversationAnchorRun) -> Path:
    return (
        output_dir
        / "anchors"
        / f"_conv_{run.first_segment_index:02d}_{run.composite_id}_manifest.json"
    )


def _speaker_display_name(speaker_id: str) -> str:
    sid = _strip(speaker_id).lower()
    if not sid:
        return ""
    try:
        from pipeline.narration_characters import load_characters
    except Exception:
        return sid
    for c in load_characters():
        if c.id.lower() == sid:
            return c.name or sid
    return sid


def _speaker_physical_description(speaker_id: str) -> str:
    from video_vendors import _character_description_prefix

    return _strip(_character_description_prefix(speaker_id)).rstrip(".")


def conversation_master_split_crop_width_fraction() -> float:
    """Horizontal crop width as a fraction of master width when splitting by portrait match."""
    raw = _conversation_scene_anchor_config().get("master_split_crop_width_fraction", 0.45)
    try:
        frac = float(raw)
        return frac if 0.1 <= frac <= 0.9 else 0.45
    except (TypeError, ValueError):
        return 0.45


def conversation_master_split_right_shift_fraction() -> float:
    """When clustered, start the right-speaker strip this far left of center (overlap)."""
    raw = _conversation_scene_anchor_config().get("master_split_right_shift_fraction", 0.07)
    try:
        frac = float(raw)
        return frac if 0.0 <= frac <= 0.25 else 0.07
    except (TypeError, ValueError):
        return 0.07


def conversation_anchor_face_x_fraction() -> float:
    """Horizontal placement of the speaker face in the final anchor (0=left, 1=right)."""
    raw = _conversation_scene_anchor_config().get("anchor_face_x_fraction", 0.5)
    try:
        frac = float(raw)
        return frac if 0.15 <= frac <= 0.85 else 0.5
    except (TypeError, ValueError):
        return 0.5


def conversation_anchor_face_y_fraction() -> float:
    """Vertical placement of the speaker face in the final anchor (0=top, 1=bottom)."""
    raw = _conversation_scene_anchor_config().get("anchor_face_y_fraction", 0.22)
    try:
        frac = float(raw)
        return frac if 0.08 <= frac <= 0.45 else 0.22
    except (TypeError, ValueError):
        return 0.22


def conversation_anchor_face_window_height_fraction() -> float:
    """Height of the vertical slice taken from a strip before 9:16 reframe (fraction of strip)."""
    raw = _conversation_scene_anchor_config().get("anchor_face_window_height_fraction", 0.52)
    try:
        frac = float(raw)
        return frac if 0.35 <= frac <= 0.75 else 0.52
    except (TypeError, ValueError):
        return 0.52


def _effective_split_crop_width_fraction(
    master_width: int,
    crop_width_fraction: float | None = None,
) -> float:
    """Narrower strips on 1× masters so solo crops do not keep both speakers."""
    base = (
        crop_width_fraction
        if crop_width_fraction is not None
        else conversation_master_split_crop_width_fraction()
    )
    if master_width >= 1100:
        return base
    scale = master_width / 1440.0
    return min(base, max(0.24, base * scale))


def _vertical_face_window_crop(
    crop,
    face_y: int,
    *,
    face_y_fraction: float | None = None,
    window_height_fraction: float | None = None,
) -> tuple[Any, int]:
    """
    Crop a strip to a shorter vertical window with ``face_y`` at ``face_y_fraction``.

    Returns ``(sub_crop, y_offset)`` where ``y_offset`` is the top row removed from ``crop``.
    """
    w, h = crop.size
    if h < 48:
        return crop, 0
    whf = (
        window_height_fraction
        if window_height_fraction is not None
        else conversation_anchor_face_window_height_fraction()
    )
    fy_frac = (
        face_y_fraction if face_y_fraction is not None else conversation_anchor_face_y_fraction()
    )
    win_h = max(32, min(h, int(round(h * whf))))
    y0 = int(round(face_y - win_h * fy_frac))
    y0 = max(0, min(y0, h - win_h))
    return crop.crop((0, y0, w, y0 + win_h)), y0


def conversation_master_width_multiplier() -> float:
    raw = _conversation_scene_anchor_config().get("master_width_multiplier", 2)
    try:
        mult = float(raw)
        return mult if mult >= 1.0 else 2.0
    except (TypeError, ValueError):
        return 2.0


def conversation_master_image_size(aspect_ratio: str = "9:16") -> dict[str, int] | str:
    """
    FAL output size for the shared conversation master still.

    For Shorts (9:16), default is **2×** the per-speaker anchor width (720→1440) at the
    same height (1280) so each half is one anchor column wide before reframe.
    """
    if str(aspect_ratio).strip() == "16:9":
        tw, th = _target_anchor_size(aspect_ratio)
        if conversation_uses_shared_master_mask():
            return {"width": tw, "height": th}
        mult = conversation_master_width_multiplier()
        return {
            "width": min(1440, max(384, int(round(tw * mult)))),
            "height": th,
        }
    tw, th = _target_anchor_size(aspect_ratio)
    if conversation_uses_shared_master_mask():
        return {"width": tw, "height": th}
    mult = conversation_master_width_multiplier()
    return {
        "width": min(1440, max(384, int(round(tw * mult)))),
        "height": th,
    }


def _master_expression_clause(master_expression: str) -> str:
    expr = _strip(master_expression).rstrip(".")
    return f"{expr}." if expr else ""


def build_conversation_master_opening_frame(
    *,
    shared_setting: str,
    left_speaker_id: str,
    right_speaker_id: str,
    master_expression: str = "",
    dual_portrait_reference: bool = False,
) -> str:
    """Two-shot t=0 prose for composite scene-anchor i2i (aligned with composite b_roll i2i)."""
    setting = _strip(shared_setting)
    left = _strip(left_speaker_id)
    right = _strip(right_speaker_id)
    left_name = _speaker_display_name(left) or left
    right_name = _speaker_display_name(right) or right
    left_phys = _speaker_physical_description(left)
    right_phys = _speaker_physical_description(right)
    if dual_portrait_reference:
        left_clause = (
            f"the person from the FIRST reference image ({left_name}; {left_phys})"
            if left_phys
            else f"the person from the FIRST reference image ({left_name}; exact portrait likeness)"
        )
        right_clause = (
            f"the person from the SECOND reference image ({right_name}; {right_phys})"
            if right_phys
            else f"the person from the SECOND reference image ({right_name}; exact portrait likeness)"
        )
    else:
        left_clause = (
            f"{left_name} ({left_phys})"
            if left_phys
            else f"{left_name} (match left reference portrait likeness)"
        )
        right_clause = (
            f"{right_name} ({right_phys})"
            if right_phys
            else f"{right_name} (match right reference portrait likeness)"
        )
    expr = _master_expression_clause(master_expression)
    expr_part = f" {expr}" if expr else ""
    return (
        f"Two distinct expedition figures in one medium two-shot: {left_clause} on the left, "
        f"{right_clause} on the right, side by side, {_CONVERSATION_MASTER_GAZE_PHRASE}{expr_part} "
        "Both visible from head to mid-torso with stable left-right placement; "
        f"{setting}—single t=0 frozen instant, "
        "same fixed backdrop throughout, not a plain white void."
    )


def build_conversation_master_i2i_prompt(
    *,
    run: ConversationAnchorRun,
    world_prefix: str = "",
    dual_portrait_reference: bool = False,
) -> str:
    """
    Short Wan i2i prompt for the shared conversation master still.

    Backdrop and staging lead the scene block; portraits carry likeness via ``image_urls``.
    """
    if not dual_portrait_reference:
        left_name = _speaker_display_name(run.left_speaker_id) or run.left_speaker_id
        right_name = _speaker_display_name(run.right_speaker_id) or run.right_speaker_id
        setting = _strip(run.shared_setting).rstrip(".")
        expr = _master_expression_clause(run.master_expression)
        wp = _strip(world_prefix).rstrip(".")
        lead = f"{wp}. " if wp else ""
        expr_part = f" {expr}" if expr else ""
        return (
            f"{lead}{setting}. "
            f"{left_name} on the left, {right_name} on the right. "
            f"{_CONVERSATION_MASTER_GAZE_PHRASE}{expr_part} "
            "Wide medium two-shot. No readable text."
        )

    from pipeline.dual_reference_i2i_prompt import build_dual_reference_i2i_prompt

    return build_dual_reference_i2i_prompt(
        left_speaker_id=run.left_speaker_id,
        right_speaker_id=run.right_speaker_id,
        scene_text=run.shared_setting,
        world_prefix=world_prefix,
        expression=run.master_expression,
        gaze_phrase=_CONVERSATION_MASTER_GAZE_PHRASE.rstrip("."),
    )


def build_conversation_master_vendor_prompt(
    *,
    run: ConversationAnchorRun,
    narr: dict[str, Any],
    world_prefix: str,
) -> str:
    """
    Vendor prompt body for composite master i2i — mirrors ``build_prompts`` for a composite b_roll shot.
    """
    from video_vendors import _composite_separation_prefix
    from video_vendors.prompt_parts import NO_ON_SCREEN_TEXT_PREFIX

    vs = narr.get("visual_style")
    style_prefix = ""
    if isinstance(vs, dict) and vs.get("description"):
        dn = _strip(vs.get("description")).rstrip(".")
        if dn:
            style_prefix = f"{dn}. "

    left_name = _speaker_display_name(run.left_speaker_id) or run.left_speaker_id
    right_name = _speaker_display_name(run.right_speaker_id) or run.right_speaker_id
    action = (
        f"Medium two-shot on deck: {left_name} and {right_name} in conversation, both figures "
        "clearly visible with separate faces and bodies, shared expedition backdrop. "
        f"{_strip(run.shared_setting)}"
    )
    parts = [
        _strip(world_prefix),
        NO_ON_SCREEN_TEXT_PREFIX.strip(),
        style_prefix,
        _composite_separation_prefix(run.composite_id),
        action,
        "Family-friendly.",
    ]
    return " ".join(p for p in parts if p)


def _target_anchor_size(aspect_ratio: str) -> tuple[int, int]:
    if str(aspect_ratio).strip() == "16:9":
        return 1280, 720
    return 720, 1280


def reframe_crop_for_anchor(
    crop,
    *,
    target_w: int,
    target_h: int,
    vertical_align: str = "top",
    scale_mode: str = "cover",
    focus_xy: tuple[float, float] | None = None,
    face_x_fraction: float | None = None,
    face_y_fraction: float | None = None,
):
    """Scale and crop a strip to talking-head anchor dimensions.

    ``cover`` (default): fill the target frame; may crop overflow (legacy half-split).
    ``fit``: scale down to show the **entire** strip (full master height preserved).

    When ``focus_xy`` is set (x, y in source-crop pixels), the output is positioned so
    that point lands at ``(face_x_fraction, face_y_fraction)`` of the target frame —
    ideal for placing the speaker face top-center while keeping the other figure's
    shoulder or arm visible at the frame edge.
    """
    from PIL import Image

    if crop.width < 1 or crop.height < 1:
        return Image.new("RGB", (target_w, target_h), (32, 32, 32))
    if str(scale_mode).strip().lower() == "fit":
        scale = min(target_w / crop.width, target_h / crop.height)
        nw = max(1, int(round(crop.width * scale)))
        nh = max(1, int(round(crop.height * scale)))
        resized = crop.resize((nw, nh), Image.Resampling.LANCZOS)
        out = Image.new("RGB", (target_w, target_h), (32, 32, 32))
        out.paste(resized, ((target_w - nw) // 2, (target_h - nh) // 2))
        return out
    sw = target_w / crop.width
    sh = target_h / crop.height
    scale = max(sw, sh)
    nw = max(1, int(round(crop.width * scale)))
    nh = max(1, int(round(crop.height * scale)))
    resized = crop.resize((nw, nh), Image.Resampling.LANCZOS)
    if focus_xy is not None:
        fx, fy = focus_xy
        fx_frac = (
            conversation_anchor_face_x_fraction()
            if face_x_fraction is None
            else float(face_x_fraction)
        )
        fy_frac = (
            float(face_y_fraction)
            if face_y_fraction is not None
            else conversation_anchor_face_y_fraction()
        )
        left = int(round(fx * scale - target_w * fx_frac))
        left = max(0, min(left, nw - target_w))
        top = int(round(fy * scale - target_h * fy_frac))
        top = max(0, min(top, nh - target_h))
        return resized.crop((left, top, left + target_w, top + target_h))
    left = max(0, (nw - target_w) // 2)
    va = str(vertical_align).strip().lower()
    if va == "center":
        top = max(0, (nh - target_h) // 2)
    elif va == "bottom":
        top = max(0, nh - target_h)
    else:
        top = 0
    return resized.crop((left, top, left + target_w, top + target_h))


def _refine_face_y_in_image(img, face_x: int) -> int:
    """Locate a face row near ``face_x`` via local detail (upper-mid band)."""
    from PIL import ImageStat

    w, h = img.size
    y0, y1 = int(h * 0.20), int(h * 0.50)
    half_w = max(16, w // 10)
    x0 = max(0, int(face_x) - half_w)
    x1 = min(w, int(face_x) + half_w)
    if x1 - x0 < 12:
        return int(h * conversation_anchor_face_y_fraction())
    best_y = y0
    best_score = -1.0
    band_h = max(8, h // 48)
    for y in range(y0, y1, max(3, band_h // 2)):
        region = img.crop((x0, y, x1, min(h, y + band_h)))
        score = float(sum(ImageStat.Stat(region).var))
        if score > best_score:
            best_score = score
            best_y = y + band_h // 2
    return best_y


def _sanitize_face_y(img_height: int, face_y: int) -> int:
    """
    Reject template-match rows that sit in the sky / tree canopy band, without
    dragging an already-plausible detection down onto the collar/shoulders.

    A full-body master still (both captains head-to-boot, not the requested medium
    head-to-torso framing) puts real faces around 15-20% down the frame — well above
    the old 0.26 floor, which silently clamped a correct ~21% detection down to a
    shoulder/epaulette row (confirmed on 18040710's conversation master). Only the
    very top sliver (pure sky/hat-brim edge) is excluded now.
    """
    lo = int(img_height * 0.08)
    hi = int(img_height * 0.42)
    fy = int(face_y)
    if fy < lo:
        return lo
    if fy > hi:
        return hi
    return fy


def _centered_crop_bounds(
    img_width: int,
    center_x: int,
    crop_w: int,
) -> tuple[int, int]:
    left = int(round(center_x - crop_w / 2))
    left = max(0, min(left, img_width - crop_w))
    return left, left + crop_w


def _speaker_strip_crop(
    img_width: int,
    face_x: int,
    partner_x: int,
    crop_width_fraction: float,
) -> tuple[int, int, int]:
    """
    Crop a vertical strip with the speaker face centered and only a shoulder slice of the partner.

    Uses the full configured strip width (no extra horizontal zoom). Partner inset shifts the
    window sideways so the other speaker's face stays mostly out of frame.

    Returns ``(x0, x1, focus_x_in_crop)``.
    """
    crop_w = max(32, min(img_width, int(round(img_width * crop_width_fraction))))
    separation = abs(int(partner_x) - int(face_x))
    if separation < crop_w // 3:
        left = int(round(face_x - crop_w / 2))
        left = max(0, min(left, img_width - crop_w))
        x1 = left + crop_w
        return left, x1, int(face_x) - left
    left = int(round(face_x - crop_w / 2))
    inset = max(40, separation // 4)
    if partner_x < face_x:
        left = max(left, int(partner_x) + inset)
    elif partner_x > face_x:
        left = min(left, int(partner_x) - crop_w + inset)
    left = max(0, min(left, img_width - crop_w))
    x1 = left + crop_w
    return left, x1, int(face_x) - left


def _speaker_adjacent_crop_bounds(
    img_width: int,
    centers: dict[str, int],
    *,
    left_speaker_id: str,
    right_speaker_id: str,
    crop_width_fraction: float,
    right_shift_fraction: float = 0.0,
) -> dict[str, tuple[int, int]]:
    """
    ``crop_width_fraction`` strips centered on each speaker (full master height).

    Each speaker gets a window centered on their face x so the other figure's
    shoulder or arm can appear at the opposite edge after face-aware reframe.
    """
    _ = right_shift_fraction  # legacy config; no longer shifts clustered splits
    crop_w = max(32, min(img_width, int(round(img_width * crop_width_fraction))))
    left_key = _strip(left_speaker_id).lower()
    right_key = _strip(right_speaker_id).lower()
    bounds: dict[str, tuple[int, int]] = {}
    for key in (left_key, right_key):
        bounds[key] = _centered_crop_bounds(img_width, centers[key], crop_w)
    return bounds


def _upper_band_detail_peak_xy(
    img,
    *,
    x_start: int = 0,
    x_end: int | None = None,
    y_frac_top: float = 0.04,
    y_frac_bottom: float = 0.38,
) -> tuple[int, int] | None:
    """Locate a face-sized detail peak in the upper band via per-column variance."""
    from PIL import ImageStat

    w, h = img.size
    band_end = w if x_end is None else min(w, max(0, int(x_end)))
    band_start = max(0, min(int(x_start), band_end))
    if band_end - band_start < 24:
        return None
    y0 = max(0, int(h * y_frac_top))
    y1 = max(y0 + 8, int(h * y_frac_bottom))
    face_y = (y0 + y1) // 2
    best_score = -1.0
    best_x = (band_start + band_end) // 2
    step = max(2, (band_end - band_start) // 120)
    for x in range(band_start + 4, band_end - 4, step):
        region = img.crop((x - 4, y0, x + 5, y1))
        stat = ImageStat.Stat(region)
        score = float(sum(stat.var))
        if score > best_score:
            best_score = score
            best_x = x
    if best_score <= 0.0:
        return None
    return best_x, face_y


def _upper_band_detail_scores(
    img,
    *,
    y_frac_top: float = 0.04,
    y_frac_bottom: float = 0.38,
) -> tuple[int, int, list[tuple[int, float]]]:
    """Return ``(face_y, min_separation_px, [(x, variance), ...])`` for the upper band."""
    from PIL import ImageStat

    w, h = img.size
    y0 = max(0, int(h * y_frac_top))
    y1 = max(y0 + 8, int(h * y_frac_bottom))
    face_y = (y0 + y1) // 2
    step = max(4, w // 160)
    scores: list[tuple[int, float]] = []
    for x in range(4, w - 4, step):
        region = img.crop((x - 4, y0, x + 5, y1))
        score = float(sum(ImageStat.Stat(region).var))
        scores.append((x, score))
    return face_y, max(48, w // 5), scores


def _local_maxima(scores: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """Strict peaks plus the descending edge of flat plateaus (solid face blobs)."""
    if len(scores) < 3:
        return list(scores)
    peaks: list[tuple[int, float]] = []
    for i in range(1, len(scores) - 1):
        x, s = scores[i]
        # Allow plateaus: keep the right edge where score stops being flat.
        if s >= scores[i - 1][1] and s > scores[i + 1][1]:
            peaks.append((x, s))
    return peaks


def _two_shot_detail_face_centers(img) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """
    Find the two strongest separated detail peaks in the upper band (wide two-shot masters).

    Wan conversation masters often place both heads in the middle third; per-side scans
    then lock onto the same cluster. Full-width local maxima avoids that.
    """
    face_y, min_sep, scores = _upper_band_detail_scores(img)
    if not scores:
        return None
    peaks = _local_maxima(scores)
    if len(peaks) < 2:
        return None
    w = img.size[0]
    mid = w // 2
    left_peaks = [p for p in peaks if p[0] < mid]
    right_peaks = [p for p in peaks if p[0] >= mid]
    if left_peaks and right_peaks:
        # Prefer the strongest face peak on each side. Exclude far edges (masts /
        # rigging). Score beats the expected quarter-column target so a weaker
        # river/shoulder blob near 0.28w cannot steal a mid-left face (18040624).
        # If both picks land in the center "gap sandwich", fall back to outer-band
        # peaks so a bright mid gap does not become both speaker xs.
        edge_margin = max(32, w // 12)
        outer_margin = max(48, w // 10)

        def _pick_side_peak(side_peaks: list[tuple[int, float]], target_x: int) -> int:
            max_score = max(s for _, s in side_peaks)
            strong = [p for p in side_peaks if p[1] >= max_score * 0.85]
            return max(strong, key=lambda p: (p[1], -abs(p[0] - target_x)))[0]

        left_edged = [p for p in left_peaks if p[0] >= edge_margin]
        right_edged = [p for p in right_peaks if p[0] < w - edge_margin]
        left_outer = [p for p in left_edged if p[0] <= mid - outer_margin]
        right_outer = [p for p in right_edged if p[0] >= mid + outer_margin]

        def _gap_sandwich(x_lo: int, x_hi: int) -> bool:
            return (mid - outer_margin) <= x_lo <= mid <= x_hi <= (mid + outer_margin)

        if left_edged and right_edged:
            x_lo = _pick_side_peak(left_edged, int(w * 0.28))
            x_hi = _pick_side_peak(right_edged, int(w * 0.72))
            if _gap_sandwich(x_lo, x_hi) and left_outer and right_outer:
                x_lo = _pick_side_peak(left_outer, int(w * 0.28))
                x_hi = _pick_side_peak(right_outer, int(w * 0.72))
            if x_hi - x_lo >= min_sep // 2:
                return (x_lo, face_y), (x_hi, face_y)
        if left_outer and right_outer:
            x_lo = _pick_side_peak(left_outer, int(w * 0.28))
            x_hi = _pick_side_peak(right_outer, int(w * 0.72))
            if x_hi - x_lo >= min_sep // 2:
                return (x_lo, face_y), (x_hi, face_y)
        left_peaks.sort(key=lambda p: -p[1])
        right_peaks.sort(key=lambda p: -p[1])
        x_lo = left_peaks[0][0]
        x_hi = right_peaks[0][0]
        if x_hi - x_lo >= min_sep // 2:
            return (x_lo, face_y), (x_hi, face_y)
    max_score = max(s for _, s in peaks)
    strong = [(x, s) for x, s in peaks if s >= max_score * 0.72]
    if len(strong) < 2:
        strong = peaks[: min(4, len(peaks))]
    best_pair: tuple[int, int] | None = None
    best_sum = -1.0
    for i, (x_lo, s_lo) in enumerate(strong):
        for x_hi, s_hi in strong[i + 1 :]:
            if x_hi - x_lo < min_sep:
                continue
            total = s_lo + s_hi
            if total > best_sum:
                best_sum = total
                best_pair = (x_lo, x_hi)
    if best_pair is None:
        by_x = sorted(strong, key=lambda p: p[0])
        if len(by_x) >= 2 and by_x[-1][0] - by_x[0][0] >= min_sep // 2:
            return (by_x[0][0], face_y), (by_x[-1][0], face_y)
        return None
    return (best_pair[0], face_y), (best_pair[1], face_y)


def _face_y_at_column(img, face_x: int, hint_y: int) -> int:
    """Face row for a speaker column; avoid epaulette / rigging false positives."""
    h = img.size[1]
    peak = _upper_band_detail_peak_xy(img, x_start=int(face_x) - 48, x_end=int(face_x) + 48)
    if peak is not None:
        return _sanitize_face_y(h, peak[1])
    refined = _refine_face_y_in_image(img, face_x)
    if refined > hint_y + int(h * 0.05):
        return _sanitize_face_y(h, hint_y)
    return _sanitize_face_y(h, refined)


def _pick_face_center_x(
    *,
    detail_xy: tuple[int, int] | None,
    portrait_xy: tuple[int, int] | None,
    img_width: int,
    img_height: int,
    default_y: int,
) -> tuple[int, int]:
    """Prefer upper-band detail for x; use portrait match y when available (eyes, not band mid)."""
    if detail_xy is None and portrait_xy is None:
        return img_width // 4, default_y
    if detail_xy is None:
        return portrait_xy[0], _sanitize_face_y(img_height, portrait_xy[1])
    if portrait_xy is None:
        return detail_xy
    dx, dy = detail_xy
    px, py = portrait_xy
    if abs(dx - px) > max(48, img_width // 8):
        return dx, _sanitize_face_y(img_height, py)
    return px, _sanitize_face_y(img_height, py)


def _portrait_face_template(portrait):
    """Upper-face band of a solo portrait, resized for template matching."""
    from PIL import Image

    pw, ph = portrait.size
    if pw < 8 or ph < 8:
        raise ValueError("portrait too small")
    face_h = max(24, int(ph * 0.58))
    face = portrait.crop((0, 0, pw, face_h))
    tpl = face.resize(
        (max(20, int(pw * 0.35)), max(30, int(face_h * 0.35))),
        Image.Resampling.LANCZOS,
    )
    return tpl, tpl.size[0], tpl.size[1]


def _match_portrait_center_xy(
    img,
    portrait_path: Path,
    *,
    x_start: int = 0,
    x_end: int | None = None,
) -> tuple[int, int] | None:
    """Estimate where a solo portrait best matches the upper band of ``img`` (x, y center).

    Optionally restrict the search to ``[x_start, x_end)`` so left/right speakers do not
    both lock onto the same face cluster when Wan paints both figures on one side.
    """
    from PIL import Image, ImageChops, ImageStat

    if not portrait_path.is_file():
        return None
    try:
        portrait = Image.open(portrait_path).convert("RGB")
    except OSError:
        return None
    pw, ph = portrait.size
    if pw < 8 or ph < 8:
        return None
    try:
        tpl, tpl_w, tpl_h = _portrait_face_template(portrait)
    except ValueError:
        return None
    w, h = img.size
    if w <= tpl_w or h <= tpl_h:
        return None
    band_end = w if x_end is None else min(w, max(0, int(x_end)))
    band_start = max(0, min(int(x_start), band_end))
    scan_end = min(band_end, w - tpl_w)
    if scan_end <= band_start:
        return None
    y0, y1 = int(h * 0.02), int(h * 0.72)
    y_max = max(y0 + tpl_h, y1 - tpl_h)
    best_score: float | None = None
    best_x = band_start + max(0, (scan_end - band_start) // 2)
    best_y = y0 + tpl_h // 2
    y_step = max(4, tpl_h // 5)
    for y in range(y0, y_max + 1, y_step):
        if y + tpl_h > h:
            break
        for x in range(band_start, scan_end, 4):
            region = img.crop((x, y, x + tpl_w, y + tpl_h))
            diff = ImageChops.difference(region, tpl)
            score = float(sum(ImageStat.Stat(diff).sum))
            if best_score is None or score < best_score:
                best_score = score
                best_x = x + tpl_w // 2
                best_y = y + max(8, int(tpl_h * 0.34))
    if best_score is None:
        return None
    return best_x, best_y


def _match_portrait_center_x(
    img,
    portrait_path: Path,
    *,
    x_start: int = 0,
    x_end: int | None = None,
) -> int | None:
    """X-only wrapper around ``_match_portrait_center_xy``."""
    match = _match_portrait_center_xy(img, portrait_path, x_start=x_start, x_end=x_end)
    return match[0] if match is not None else None


def _estimate_speaker_face_centers(
    img,
    *,
    repo_root: Path,
    left_speaker_id: str,
    right_speaker_id: str,
) -> dict[str, tuple[int, int]] | None:
    """Map each speaker id to an estimated face center (x, y) in the master still."""
    from video_vendors.fal import FalVendor

    left_key = _strip(left_speaker_id).lower()
    right_key = _strip(right_speaker_id).lower()
    if not left_key or not right_key:
        return None

    w, h = img.size
    default_y = int(h * conversation_anchor_face_y_fraction())

    pair = _two_shot_detail_face_centers(img)
    if pair is not None:
        (x_lo, y_band), (x_hi, _) = pair
        left_cy = _face_y_at_column(img, x_lo, y_band)
        right_cy = _face_y_at_column(img, x_hi, y_band)
        return {left_key: (x_lo, left_cy), right_key: (x_hi, right_cy)}

    mid = w // 2
    overlap = max(32, w // 12)
    left_detail = _upper_band_detail_peak_xy(img, x_start=0, x_end=mid + overlap)
    right_detail = _upper_band_detail_peak_xy(img, x_start=mid - overlap, x_end=w)

    left_portrait = right_portrait = None
    left_path = FalVendor._portrait_path_for_character(repo_root, left_key)
    right_path = FalVendor._portrait_path_for_character(repo_root, right_key)
    if left_path is not None:
        left_portrait = _match_portrait_center_xy(img, left_path, x_start=0, x_end=mid + overlap)
    if right_path is not None:
        right_portrait = _match_portrait_center_xy(img, right_path, x_start=mid - overlap, x_end=w)

    left_cx, left_cy = _pick_face_center_x(
        detail_xy=left_detail,
        portrait_xy=left_portrait,
        img_width=w,
        img_height=h,
        default_y=default_y,
    )
    right_cx, right_cy = _pick_face_center_x(
        detail_xy=right_detail,
        portrait_xy=right_portrait,
        img_width=w,
        img_height=h,
        default_y=default_y,
    )
    left_cy = _refine_face_y_in_image(img, left_cx)
    right_cy = _refine_face_y_in_image(img, right_cx)

    # Screen-left figure -> left_speaker_id; screen-right -> right_speaker_id.
    if left_cx <= right_cx:
        x_lo, y_lo = left_cx, left_cy
        x_hi, y_hi = right_cx, right_cy
    else:
        x_lo, y_lo = right_cx, right_cy
        x_hi, y_hi = left_cx, left_cy
    if x_hi - x_lo < max(32, w // 8):
        x_lo = w // 4
        x_hi = (3 * w) // 4
        y_lo = y_hi = default_y
    return {left_key: (x_lo, y_lo), right_key: (x_hi, y_hi)}


def _estimate_speaker_centers_x(
    img,
    *,
    repo_root: Path,
    left_speaker_id: str,
    right_speaker_id: str,
) -> dict[str, int] | None:
    """Map each speaker id to an estimated face center x (legacy helper)."""
    faces = _estimate_speaker_face_centers(
        img,
        repo_root=repo_root,
        left_speaker_id=left_speaker_id,
        right_speaker_id=right_speaker_id,
    )
    if faces is None:
        return None
    return {sid: xy[0] for sid, xy in faces.items()}


def _split_master_halves(img, *, center_overlap_fraction: float = 0.06):
    """Legacy 50/50 horizontal split with slight center overlap."""
    w, h = img.size
    mid = w // 2
    overlap = max(0, int(w * center_overlap_fraction))
    left_crop = img.crop((0, 0, min(w, mid + overlap), h))
    right_crop = img.crop((max(0, mid - overlap), 0, w, h))
    return left_crop, right_crop


def split_master_to_speaker_images(
    master_bytes: bytes,
    *,
    left_speaker_id: str,
    right_speaker_id: str,
    aspect_ratio: str = "9:16",
    center_overlap_fraction: float = 0.06,
    repo_root: Path | None = None,
    crop_width_fraction: float | None = None,
) -> dict[str, bytes]:
    """
    Derive per-speaker PNG bytes from a wide conversation master still.

    When ``repo_root`` is set, locates each speaker with solo-portrait template matching
    and crops a window centered on that match (works even when Wan clusters both figures
    on one side of the frame). Otherwise falls back to a 50/50 horizontal split.
    """
    from PIL import Image

    img = Image.open(io.BytesIO(master_bytes)).convert("RGB")
    w, h = img.size
    if w < 32:
        raise ValueError("master anchor too narrow to split")
    tw, th = _target_anchor_size(aspect_ratio)
    left_id = _strip(left_speaker_id).lower()
    right_id = _strip(right_speaker_id).lower()
    out: dict[str, bytes] = {}
    crop_frac = _effective_split_crop_width_fraction(w, crop_width_fraction)

    faces = (
        _estimate_speaker_face_centers(
            img,
            repo_root=repo_root,
            left_speaker_id=left_speaker_id,
            right_speaker_id=right_speaker_id,
        )
        if repo_root is not None
        else None
    )
    if faces and left_id in faces and right_id in faces:
        for sid in (left_id, right_id):
            face_x, face_y = faces[sid]
            partner_sid = right_id if sid == left_id else left_id
            partner_x = faces[partner_sid][0]
            x0, x1, focus_x = _speaker_strip_crop(w, face_x, partner_x, crop_frac)
            crop = img.crop((x0, 0, x1, h))
            crop, y_off = _vertical_face_window_crop(crop, face_y)
            framed = reframe_crop_for_anchor(
                crop,
                target_w=tw,
                target_h=th,
                scale_mode="cover",
                focus_xy=(focus_x, face_y - y_off),
            )
            buf = io.BytesIO()
            framed.save(buf, format="PNG")
            out[sid] = buf.getvalue()
        return out

    left_crop, right_crop = _split_master_halves(
        img, center_overlap_fraction=center_overlap_fraction
    )
    for sid, crop in ((left_id, left_crop), (right_id, right_crop)):
        framed = reframe_crop_for_anchor(crop, target_w=tw, target_h=th)
        buf = io.BytesIO()
        framed.save(buf, format="PNG")
        out[sid] = buf.getvalue()
    return out


def _write_manifest(
    output_dir: Path,
    run: ConversationAnchorRun,
    *,
    master_rel: str,
    segment_anchors: dict[int, str],
) -> None:
    mp = manifest_path(output_dir, run)
    mp.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "first_segment_index": run.first_segment_index,
        "segment_indices": list(run.segment_indices),
        "composite_id": run.composite_id,
        "left_speaker_id": run.left_speaker_id,
        "right_speaker_id": run.right_speaker_id,
        "shared_setting": run.shared_setting,
        "master_anchor": master_rel,
        "segment_anchors": {str(k): v for k, v in sorted(segment_anchors.items())},
    }
    mp.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _speaker_for_segment_in_run(
    _output_dir: Path,
    run: ConversationAnchorRun,
    segment_index: int,
    *,
    narr: dict[str, Any] | None = None,
) -> str:
    if narr is None:
        return ""
    row = narration_script_row_1based(narr, segment_index)
    if not row:
        return ""
    return _speaker_id_for_segment(row)


def _openings_with_master_frame(
    openings: list[str | None] | None,
    *,
    segment_index: int,
    master_opening: str,
) -> list[str | None]:
    n = max(segment_index, len(openings or []))
    base: list[str | None] = list(openings or [])
    if len(base) < n:
        base.extend([None] * (n - len(base)))
    base[segment_index - 1] = master_opening
    return base


def conversation_master_world_prefix(narr: dict[str, Any]) -> str:
    """
    Mood-only world line for the shared master still.

    Location comes from ``shared_setting``; segment ``scene_spine`` block labels must not
    override a user-edited conversation backdrop.
    """
    spine = narr.get("scene_spine") if isinstance(narr.get("scene_spine"), dict) else {}
    mood = _strip(spine.get("visual_mood")) if spine else ""
    parts = "Photorealistic period scene set in early 1800's, American frontier"
    if mood:
        parts += f", overall mood {mood}"
    return parts + "."


def _run_conversation_master_scene_anchor_i2i(
    *,
    repo_root: Path,
    output_dir: Path,
    run: ConversationAnchorRun,
    narr: dict[str, Any],
    openings: list[str | None],
    core_overrides: list[str | None],
    aspect_ratio: str,
    date_id: str = "",
    force_composite_portrait: bool = False,
) -> Path:
    """Run Wan i2i for the shared conversation master still; returns saved master path."""
    from video_vendors.fal_scene_anchor_i2i import run_scene_anchor_i2i_to_disk

    output_dir.mkdir(parents=True, exist_ok=True)
    master_path = master_anchor_path(output_dir, run)
    ref_mode = conversation_master_reference_mode()
    portrait_uris: list[str] | None = None
    if ref_mode == "dual_portrait":
        portrait_uris = conversation_master_portrait_data_uris(repo_root, run)
    else:
        ensure_composite_portrait_cached(
            composite_id=run.composite_id,
            left_member_id=run.left_speaker_id,
            right_member_id=run.right_speaker_id,
            portraits_dir=repo_root / "character-portraits",
            force=force_composite_portrait,
        )

    first = run.first_segment_index
    world = conversation_master_world_prefix(narr)
    clo = core_overrides[first - 1] if 0 < first <= len(core_overrides) else None
    master_prompt = build_conversation_master_i2i_prompt(
        run=run,
        world_prefix=world,
        dual_portrait_reference=(ref_mode == "dual_portrait"),
    )

    _url, saved = run_scene_anchor_i2i_to_disk(
        repo_root=repo_root,
        segment_index=first,
        prompt_without_markers_sanitized="",
        character_id=run.composite_id,
        output_dir=output_dir,
        opening_frames=None,
        world_prefix_for_i2i=world,
        core_location_override=clo,
        aspect_ratio=aspect_ratio,
        aggressive=False,
        anchor_save_basename=f"_conv_{first:02d}_{run.composite_id}_master",
        image_size=conversation_master_image_size(aspect_ratio),
        portrait_data_uris=portrait_uris,
        dual_reference_speakers=(run.left_speaker_id, run.right_speaker_id)
        if ref_mode == "dual_portrait"
        else None,
        image_to_image_prompt_override=master_prompt,
    )
    if saved is None or not saved.is_file():
        raise RuntimeError(
            f"Conversation master scene anchor failed for composite {run.composite_id!r}"
        )
    if saved != master_path:
        master_path.parent.mkdir(parents=True, exist_ok=True)
        master_path.write_bytes(saved.read_bytes())
    return master_path


def ensure_conversation_run_anchors(
    *,
    repo_root: Path,
    output_dir: Path,
    run: ConversationAnchorRun,
    narr: dict[str, Any],
    prompts: list[str],
    openings: list[str | None],
    core_overrides: list[str | None],
    aspect_ratio: str,
    date_id: str = "",
    skip_existing: bool = True,
    force: bool = False,
) -> dict[int, Path]:
    """
    Build master composite i2i (once) and derive per-segment speaker anchors.

    Returns map of 1-based segment index -> saved anchor path for segments in ``run``.
    """
    from video_vendors.fal import FalVendor

    output_dir.mkdir(parents=True, exist_ok=True)
    master_path = master_anchor_path(output_dir, run)
    derived: dict[int, Path] = {}

    if (
        skip_existing
        and not force
        and _run_derived_anchors_complete_for_narr(output_dir, run, narr)
    ):
        for si in run.segment_indices:
            cid = _speaker_for_segment_in_run(output_dir, run, si, narr=narr)
            if cid:
                p = FalVendor._existing_scene_anchor_path(output_dir, si, cid)
                if p is not None:
                    derived[si] = p
        return derived

    master_path = _run_conversation_master_scene_anchor_i2i(
        repo_root=repo_root,
        output_dir=output_dir,
        run=run,
        narr=narr,
        openings=openings,
        core_overrides=core_overrides,
        aspect_ratio=aspect_ratio,
        date_id=date_id,
        force_composite_portrait=force,
    )
    master_bytes = master_path.read_bytes()

    split = split_master_to_speaker_images(
        master_bytes,
        left_speaker_id=run.left_speaker_id,
        right_speaker_id=run.right_speaker_id,
        aspect_ratio=aspect_ratio,
        repo_root=repo_root,
    )
    anchors_dir = output_dir / "anchors"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    rel_by_seg: dict[int, str] = {}
    for si in run.segment_indices:
        cid = _speaker_for_segment_in_run(output_dir, run, si, narr=narr)
        if not cid:
            continue
        body = split.get(cid.lower())
        if not body:
            continue
        from video_vendors.fal_scene_anchor_i2i import _safe_anchor_character_file_stem

        stem = _safe_anchor_character_file_stem(cid)
        dest = anchors_dir / f"{si:02d}_{stem}.png"
        dest.write_bytes(body)
        derived[si] = dest
        try:
            rel_by_seg[si] = str(dest.relative_to(repo_root.resolve())).replace("\\", "/")
        except ValueError:
            rel_by_seg[si] = dest.name

    master_rel = ""
    try:
        master_rel = str(master_path.relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        master_rel = master_path.name
    _write_manifest(output_dir, run, master_rel=master_rel, segment_anchors=rel_by_seg)
    return derived


def regenerate_conversation_master_anchor(
    *,
    repo_root: Path,
    output_dir: Path,
    run: ConversationAnchorRun,
    narr: dict[str, Any],
    prompts: list[str],
    openings: list[str | None],
    core_overrides: list[str | None],
    aspect_ratio: str = "9:16",
    date_id: str = "",
) -> Path:
    """Run composite master scene-anchor i2i only (no per-speaker split)."""
    return _run_conversation_master_scene_anchor_i2i(
        repo_root=repo_root,
        output_dir=output_dir,
        run=run,
        narr=narr,
        openings=openings,
        core_overrides=core_overrides,
        aspect_ratio=aspect_ratio,
        date_id=date_id,
        force_composite_portrait=True,
    )


def regenerate_conversation_master_for_date(
    date_id: str,
    *,
    repo_root: Path | None = None,
    aspect_ratio: str = "9:16",
    run_index: int = 0,
) -> Path:
    """Regenerate the shared master PNG for one conversation run on ``date_id``."""
    from video_vendors import build_prompts, load_fal_scene_anchor_i2i_meta

    root = (repo_root or Path.cwd()).resolve()
    did = _strip(date_id)
    narr_path = root / "narrations" / f"narration{did}.json"
    narr = json.loads(narr_path.read_text(encoding="utf-8-sig"))
    runs = discover_conversation_anchor_runs(narr)
    if not runs:
        raise RuntimeError(f"No conversation anchor runs for date_id={did!r}")
    if run_index < 0 or run_index >= len(runs):
        raise IndexError(f"run_index {run_index} out of range (found {len(runs)} run(s))")
    run = runs[run_index]
    prompts = build_prompts(did, narrations_dir=root / "narrations", vendor="fal")
    openings, _, core_overrides = load_fal_scene_anchor_i2i_meta(
        did, narrations_dir=root / "narrations"
    )
    out_dir = root / "movie-images" / did
    return regenerate_conversation_master_anchor(
        repo_root=root,
        output_dir=out_dir,
        run=run,
        narr=narr,
        prompts=prompts,
        openings=openings,
        core_overrides=core_overrides,
        aspect_ratio=aspect_ratio,
        date_id=did,
    )


def backup_conversation_anchors_for_date(
    date_id: str,
    *,
    repo_root: Path | None = None,
    label: str | None = None,
) -> Path:
    """Copy ``movie-images/<date_id>/anchors/*`` into a timestamped backup subfolder."""
    import shutil
    from datetime import datetime

    root = (repo_root or Path.cwd()).resolve()
    did = _strip(date_id)
    anchors_dir = root / "movie-images" / did / "anchors"
    if not anchors_dir.is_dir():
        raise FileNotFoundError(f"No anchors directory: {anchors_dir}")
    stamp = label or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_dir = anchors_dir / f"_backup_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for child in sorted(anchors_dir.iterdir()):
        if not child.is_file():
            continue
        if child.name.startswith("_backup_"):
            continue
        shutil.copy2(child, backup_dir / child.name)
    return backup_dir


def rederive_conversation_anchors_from_master_for_date(
    date_id: str,
    *,
    repo_root: Path | None = None,
    aspect_ratio: str = "9:16",
    run_index: int = 0,
) -> dict[int, Path]:
    """Re-derive conversation anchors from the on-disk master (no FAL i2i)."""
    root = (repo_root or Path.cwd()).resolve()
    did = _strip(date_id)
    narr_path = root / "narrations" / f"narration{did}.json"
    narr = json.loads(narr_path.read_text(encoding="utf-8-sig"))
    runs = discover_conversation_anchor_runs(narr)
    if not runs:
        raise RuntimeError(f"No conversation anchor runs for date_id={did!r}")
    if run_index < 0 or run_index >= len(runs):
        raise IndexError(f"run_index {run_index} out of range (found {len(runs)} run(s))")
    run = runs[run_index]
    output_dir = root / "movie-images" / did
    master_path = master_anchor_path(output_dir, run)
    if not master_path.is_file():
        raise FileNotFoundError(f"Missing conversation master: {master_path}")

    if conversation_uses_shared_master_bookend():
        from pipeline.conversation_bookend_anchor import derive_bookend_anchors_from_master

        return derive_bookend_anchors_from_master(
            repo_root=root,
            output_dir=output_dir,
            run=run,
            narr=narr,
            master_path=master_path,
            aspect_ratio=aspect_ratio,
        )

    if conversation_uses_shared_master_mask():
        from pipeline.conversation_omnihuman_mask import derive_omnihuman_masks_from_master

        return derive_omnihuman_masks_from_master(
            repo_root=root,
            output_dir=output_dir,
            run=run,
            narr=narr,
            master_path=master_path,
            aspect_ratio=aspect_ratio,
        )

    split = split_master_to_speaker_images(
        master_path.read_bytes(),
        left_speaker_id=run.left_speaker_id,
        right_speaker_id=run.right_speaker_id,
        aspect_ratio=aspect_ratio,
        repo_root=root,
    )
    anchors_dir = output_dir / "anchors"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    derived: dict[int, Path] = {}
    rel_by_seg: dict[int, str] = {}
    for si in run.segment_indices:
        cid = _speaker_for_segment_in_run(output_dir, run, si, narr=narr)
        if not cid:
            continue
        body = split.get(cid.lower())
        if not body:
            continue
        from video_vendors.fal_scene_anchor_i2i import _safe_anchor_character_file_stem

        stem = _safe_anchor_character_file_stem(cid)
        dest = anchors_dir / f"{si:02d}_{stem}.png"
        dest.write_bytes(body)
        derived[si] = dest
        try:
            rel_by_seg[si] = str(dest.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel_by_seg[si] = dest.name

    master_rel = str(master_path.relative_to(root)).replace("\\", "/")
    _write_manifest(output_dir, run, master_rel=master_rel, segment_anchors=rel_by_seg)
    return derived


def resplit_conversation_master_for_date(
    date_id: str,
    *,
    repo_root: Path | None = None,
    aspect_ratio: str = "9:16",
    run_index: int = 0,
) -> dict[int, Path]:
    """Re-derive per-speaker anchors from an existing conversation master (no FAL)."""
    return rederive_conversation_anchors_from_master_for_date(
        date_id,
        repo_root=repo_root,
        aspect_ratio=aspect_ratio,
        run_index=run_index,
    )


def _conversation_segment_is_bookend(run: ConversationAnchorRun, segment_index: int) -> bool:
    from pipeline.conversation_bookend_anchor import conversation_segment_is_bookend

    return conversation_segment_is_bookend(run, segment_index)


def _run_derived_anchors_complete_for_narr(
    output_dir: Path,
    run: ConversationAnchorRun,
    narr: dict[str, Any],
) -> bool:
    from video_vendors.fal import FalVendor

    if not master_anchor_path(output_dir, run).is_file():
        return False
    for si in run.segment_indices:
        cid = _speaker_for_segment_in_run(output_dir, run, si, narr=narr)
        if not cid:
            return False
        if FalVendor._existing_scene_anchor_path(output_dir, si, cid) is None:
            return False
    return True


def resolve_talking_head_scene_anchor(
    *,
    repo_root: Path,
    output_dir: Path,
    segment_index: int,
    anchor_char: str,
    narr: dict[str, Any],
    prompts: list[str],
    openings: list[str | None],
    core_overrides: list[str | None],
    world_prefix: str,
    aspect_ratio: str,
    reuse_scene_anchor_stills: bool,
    sanitized_prompt: str,
    date_id: str = "",
) -> TalkingHeadAnchorResolution:
    """
    Resolve scene anchor for one talking_head segment.

    Uses conversation master+mask or master+split when eligible; otherwise per-segment i2i.
    """
    from video_vendors.fal import FalVendor
    from video_vendors.fal_scene_anchor_i2i import run_scene_anchor_i2i_to_disk

    cid = _strip(anchor_char).lower()
    runs = discover_conversation_anchor_runs(narr)
    run_map = segment_to_conversation_run(runs)
    run = run_map.get(segment_index)

    if reuse_scene_anchor_stills and run is not None and conversation_uses_shared_master_bookend():
        from pipeline.conversation_bookend_anchor import (
            conversation_segment_is_bookend,
            existing_bookend_anchor_for_segment,
        )
        from pipeline.conversation_omnihuman_mask import (
            omnihuman_shorts_master_path,
            resolve_omnihuman_mask_for_segment,
        )

        if conversation_segment_is_bookend(run, segment_index):
            omni = omnihuman_shorts_master_path(output_dir, run)
            mask = resolve_omnihuman_mask_for_segment(output_dir, segment_index, cid)
            if omni.is_file() and mask is not None:
                return TalkingHeadAnchorResolution(image_path=omni, mask_path=mask)
        else:
            existing = existing_bookend_anchor_for_segment(output_dir, segment_index, cid)
            if existing is not None and existing.is_file():
                return TalkingHeadAnchorResolution(image_path=existing)

    if reuse_scene_anchor_stills and run is not None and conversation_uses_shared_master_mask():
        from pipeline.conversation_omnihuman_mask import (
            omnihuman_shorts_master_path,
            omnihuman_speaker_mask_path,
            resolve_omnihuman_mask_for_segment,
        )

        omni = omnihuman_shorts_master_path(output_dir, run)
        mask = resolve_omnihuman_mask_for_segment(output_dir, segment_index, cid)
        if omni.is_file() and mask is not None:
            return TalkingHeadAnchorResolution(image_path=omni, mask_path=mask)

    if reuse_scene_anchor_stills and not (
        run is not None
        and (
            conversation_uses_shared_master_mask()
            or (
                conversation_uses_shared_master_bookend()
                and _conversation_segment_is_bookend(run, segment_index)
            )
        )
    ):
        existing = FalVendor._existing_scene_anchor_path(output_dir, segment_index, cid)
        if existing is not None:
            return TalkingHeadAnchorResolution(image_path=existing)

    if run is not None and conversation_uses_shared_master_mask():
        from pipeline.conversation_omnihuman_mask import (
            ensure_conversation_run_omnihuman_masks,
            omnihuman_speaker_mask_path,
        )

        derived = ensure_conversation_run_omnihuman_masks(
            repo_root=repo_root,
            output_dir=output_dir,
            run=run,
            narr=narr,
            prompts=prompts,
            openings=openings,
            core_overrides=core_overrides,
            aspect_ratio=aspect_ratio,
            date_id=date_id,
            skip_existing=True,
            force=False,
        )
        master = derived.get(segment_index)
        mask_p = omnihuman_speaker_mask_path(output_dir, segment_index, cid)
        if master is not None and master.is_file() and mask_p.is_file():
            return TalkingHeadAnchorResolution(image_path=master, mask_path=mask_p)

    if run is not None and conversation_uses_shared_master_bookend():
        from pipeline.conversation_bookend_anchor import (
            conversation_segment_is_bookend,
            ensure_conversation_run_bookend_anchors,
        )
        from pipeline.conversation_omnihuman_mask import omnihuman_speaker_mask_path

        derived = ensure_conversation_run_bookend_anchors(
            repo_root=repo_root,
            output_dir=output_dir,
            run=run,
            narr=narr,
            prompts=prompts,
            openings=openings,
            core_overrides=core_overrides,
            aspect_ratio=aspect_ratio,
            date_id=date_id,
            skip_existing=True,
            force=False,
        )
        path = derived.get(segment_index)
        if path is not None and path.is_file():
            if conversation_segment_is_bookend(run, segment_index):
                mask_p = omnihuman_speaker_mask_path(output_dir, segment_index, cid)
                if mask_p.is_file():
                    return TalkingHeadAnchorResolution(image_path=path, mask_path=mask_p)
            else:
                return TalkingHeadAnchorResolution(image_path=path)

    if run is not None and conversation_uses_shared_master_split():
        derived = ensure_conversation_run_anchors(
            repo_root=repo_root,
            output_dir=output_dir,
            run=run,
            narr=narr,
            prompts=prompts,
            openings=openings,
            core_overrides=core_overrides,
            aspect_ratio=aspect_ratio,
            date_id=date_id,
            skip_existing=True,
            force=False,
        )
        path = derived.get(segment_index)
        if path is not None and path.is_file():
            return TalkingHeadAnchorResolution(image_path=path)

    edited_url, saved_path = run_scene_anchor_i2i_to_disk(
        repo_root=repo_root,
        segment_index=segment_index,
        prompt_without_markers_sanitized=sanitized_prompt,
        character_id=cid,
        output_dir=output_dir,
        opening_frames=openings,
        world_prefix_for_i2i=world_prefix,
        core_location_override=(
            core_overrides[segment_index - 1] if 0 < segment_index <= len(core_overrides) else None
        ),
        aspect_ratio=aspect_ratio,
        aggressive=False,
    )
    return TalkingHeadAnchorResolution(image_url=edited_url, image_path=saved_path)
