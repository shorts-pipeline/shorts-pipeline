"""
Conversation mini-spine: shared backdrop for long-conversation talking_head runs.

Phase 1 may emit ``conversation_micro_arc`` plus per-segment ``conversation_tracking``.
Merge uses this module to force the same vicinity/background into every ``opening_frame``
in a ping-pong talking_head exchange.
"""

from __future__ import annotations

from typing import Any

from pipeline.narration_visual_mode import (
    VISUAL_MODE_TALKING_HEAD,
    _talking_head_same_subject_adjoins,
    normalize_visual_mode,
)


def _strip(v: Any) -> str:
    return str(v or "").strip()


def find_talking_head_conversation_runs(segments: list[Any]) -> list[list[int]]:
    """
    Maximal consecutive ``talking_head`` runs (0-based indices) with length >= 2
    where adjacent clips do not repeat the same ``talking_head_subject``.
    """
    if not isinstance(segments, list):
        return []
    runs: list[list[int]] = []
    current: list[int] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict) or normalize_visual_mode(seg) != VISUAL_MODE_TALKING_HEAD:
            if len(current) >= 2:
                runs.append(current)
            current = []
            continue
        if current and _talking_head_same_subject_adjoins(segments[current[-1]], seg):
            if len(current) >= 2:
                runs.append(current)
            current = [i]
            continue
        current.append(i)
    if len(current) >= 2:
        runs.append(current)
    return runs


def _setting_from_tracking(seg: dict[str, Any]) -> str:
    ct = seg.get("conversation_tracking")
    if not isinstance(ct, dict):
        return ""
    return _strip(ct.get("setting_state"))


def resolve_conversation_shared_setting(
    phase1: dict[str, Any],
    segments: list[Any],
    run_indices: list[int],
) -> str:
    """Canonical backdrop text for a talking_head conversation run."""
    arc = phase1.get("conversation_micro_arc")
    if isinstance(arc, dict):
        shared = _strip(arc.get("shared_setting"))
        if shared:
            props = arc.get("persistent_props")
            if isinstance(props, list):
                kept = [_strip(p) for p in props if _strip(p)][:3]
                if kept:
                    shared = f"{shared} Visible: {'; '.join(kept)}."
            return shared

    for idx in run_indices:
        if 0 <= idx < len(segments) and isinstance(segments[idx], dict):
            s = _setting_from_tracking(segments[idx])
            if s:
                return s

    ep_meta = phase1.get("episode_metadata")
    if isinstance(ep_meta, dict):
        loc = _strip(ep_meta.get("location_summary"))
        if loc:
            return f"Same expedition set throughout the exchange: {loc}."
    return "Same windbound camp or riverbank set throughout the exchange; earth tones, period materials."


def raw_conversation_shared_setting(phase1: dict[str, Any]) -> str:
    """``conversation_micro_arc.shared_setting`` only — no ``persistent_props`` suffix."""
    arc = phase1.get("conversation_micro_arc")
    if isinstance(arc, dict):
        return _strip(arc.get("shared_setting"))
    return ""


def sync_conversation_place_in_scene_spine(
    phase1: dict[str, Any],
    segment_indices: list[int],
    shared_setting: str,
) -> bool:
    """
    Align ``scene_spine.visual_blocks`` place_label with a manual shared backdrop edit.

    Prevents conversation master i2i ``world_prefix`` from still citing an old keelboat deck.
    """
    setting = _strip(shared_setting)
    if not setting:
        return False
    spine = phase1.get("scene_spine")
    if not isinstance(spine, dict):
        return False
    blocks = spine.get("visual_blocks")
    if not isinstance(blocks, list):
        return False
    seg_set: set[int] = set()
    for si in segment_indices:
        try:
            seg_set.add(int(si))
        except (TypeError, ValueError):
            continue
    if not seg_set:
        return False
    place = setting.split(".")[0].strip() or setting[:120]
    updated = False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        raw_idx = block.get("segment_indices")
        if not isinstance(raw_idx, list):
            continue
        block_segs: set[int] = set()
        for x in raw_idx:
            try:
                block_segs.add(int(x))
            except (TypeError, ValueError):
                continue
        if not seg_set.intersection(block_segs):
            continue
        block["place_label"] = place
        updated = True
    return updated


def resolve_conversation_master_expression(
    phase1: dict[str, Any],
    segments: list[Any],
    run_indices: list[int],
) -> str:
    """
    Shared facial mood for the conversation master two-shot.

    Authoritative source is Phase 1 ``conversation_micro_arc.master_expression``.
    Legacy narrations fall back to ``tone_register``, then a neutral pre-speech default.
    """
    arc = phase1.get("conversation_micro_arc")
    if isinstance(arc, dict):
        expr = _strip(arc.get("master_expression"))
        if expr:
            return expr
    tone = _strip(phase1.get("tone_register"))
    if tone:
        return f"{tone} mood; calm pre-speech faces appropriate to the exchange"
    return "Calm pre-speech expressions, listening, about to speak."


def _arc_segment_indices(phase1: dict[str, Any]) -> list[int] | None:
    arc = phase1.get("conversation_micro_arc")
    if not isinstance(arc, dict):
        return None
    raw = arc.get("segment_indices")
    if not isinstance(raw, list) or not raw:
        return None
    out: list[int] = []
    for x in raw:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n >= 1:
            out.append(n - 1)
    return out if out else None


def conversation_run_setting_by_segment_index(
    phase1: dict[str, Any],
    segments: list[Any],
    *,
    long_conversation_mode: bool,
) -> dict[int, str]:
    """Map 0-based segment index -> shared backdrop for merge/i2i."""
    if not long_conversation_mode:
        return {}
    runs = find_talking_head_conversation_runs(segments)
    if not runs:
        return {}
    arc_indices = _arc_segment_indices(phase1)
    chosen = runs[0]
    if arc_indices is not None:
        arc_set = set(arc_indices)
        for run in runs:
            if arc_set.intersection(run):
                chosen = run
                break
    setting = resolve_conversation_shared_setting(phase1, segments, chosen)
    if not setting:
        return {}
    return {idx: setting for idx in chosen}


def inject_conversation_backdrop(
    opening_frame: str,
    *,
    subject_id: str,
    shared_setting: str,
) -> str:
    """
    Ensure ``opening_frame`` carries the conversation's fixed backdrop for scene-anchor i2i.

    Preserves Phase 2 pose/eyeline prose when present; appends or weaves in ``shared_setting``.
    """
    setting = _strip(shared_setting)
    sub = _strip(subject_id).lower()
    if not setting:
        return _strip(opening_frame)
    frame = _strip(opening_frame)
    if setting.lower() in frame.lower():
        return frame
    if not frame:
        return (
            f"Head-and-shoulders period portrait, {sub}, relaxed neutral mouth before speech, "
            f"steady eyes; {setting}—single t=0 frozen instant only, not a plain white void."
        )
    # Keep pose/eyeline from Phase 2; lock background via explicit suffix models weight at the end.
    return f"{frame} Same fixed backdrop throughout this conversation: {setting}"


def validate_conversation_micro_arc(parsed: dict[str, Any], segment_count: int) -> None:
    """Raise ValueError when ``conversation_micro_arc`` is present but malformed."""
    arc = parsed.get("conversation_micro_arc")
    if arc is None:
        return
    if not isinstance(arc, dict):
        raise ValueError("conversation_micro_arc must be an object when present")
    shared = _strip(arc.get("shared_setting"))
    if not shared:
        raise ValueError("conversation_micro_arc.shared_setting must be a non-empty string")
    speakers = arc.get("speakers")
    if not isinstance(speakers, list) or len(speakers) != 2:
        raise ValueError(
            "conversation_micro_arc.speakers must be an array of exactly two speaker_id strings"
        )
    for j, sp in enumerate(speakers):
        if not _strip(sp):
            raise ValueError(f"conversation_micro_arc.speakers[{j}] must be non-empty")
    indices = arc.get("segment_indices")
    if not isinstance(indices, list) or not indices:
        raise ValueError(
            "conversation_micro_arc.segment_indices must be a non-empty array of 1-based ints"
        )
    for j, raw in enumerate(indices):
        try:
            n = int(raw)
        except (TypeError, ValueError) as e:
            raise ValueError(f"conversation_micro_arc.segment_indices[{j}] must be int") from e
        if n < 1 or n > segment_count:
            raise ValueError(
                f"conversation_micro_arc.segment_indices[{j}]={n} out of range 1..{segment_count}"
            )


def normalize_setting_state_drift(
    segments: list[Any],
    run_indices: list[int],
    canonical_setting: str,
) -> None:
    """In-place: align ``conversation_tracking.setting_state`` within a run (voice sidecar hygiene)."""
    setting = _strip(canonical_setting)
    if not setting:
        return
    for idx in run_indices:
        if idx < 0 or idx >= len(segments) or not isinstance(segments[idx], dict):
            continue
        ct = segments[idx].get("conversation_tracking")
        if not isinstance(ct, dict):
            ct = {}
            segments[idx]["conversation_tracking"] = ct
        ct["setting_state"] = setting
