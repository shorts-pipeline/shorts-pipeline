#!/usr/bin/env python3
"""
Narration JSON loading and v2/map helpers.
Single place for: load_narration, is_narration_v2, get_mid_episode_map_insertion.
"""

import json
from pathlib import Path
from typing import Any

from pipeline.narration_visual_mode import visual_modes_for_narration_script


def enforce_narrator_only_for_style_policy(
    data: dict[str, Any],
    *,
    narrator_only_style_names: list[str] | None = None,
) -> bool:
    """
    Mutate narration JSON in-place when style policy requires narrator-only output.

    Returns True when policy was applied.
    """
    style_names = {
        str(name).strip().lower()
        for name in (narrator_only_style_names or [])
        if isinstance(name, str) and str(name).strip()
    }
    if not style_names:
        return False
    vs = data.get("visual_style")
    style_name = (vs.get("name") or "").strip().lower() if isinstance(vs, dict) else ""
    if style_name not in style_names:
        return False
    data["dialogue_mode"] = False
    data["fal_scene_anchor_openings"] = False
    script = data.get("narration_script")
    if not isinstance(script, list):
        return True
    for seg in script:
        if not isinstance(seg, dict):
            continue
        if seg.get("visual_mode") == "talking_head":
            seg["visual_mode"] = "b_roll"
        seg.pop("dialogue", None)
        seg.pop("talking_head_subject", None)
        seg.pop("talking_head_prompt", None)
        seg.pop("conversation_tracking", None)
    return True


def sanitize_date_id_for_path(raw: str | None) -> str | None:
    """
    Return ``YYYYMMDD`` for filesystem paths under ``movie-images/<id>/``.

    Strips accidental path segments (e.g. ``18040514/anchors`` or ``18040514\\\\anchors``)
    so callers cannot create ``movie-images/.../anchors/anchors``.
    """
    if raw is None:
        return None
    s = str(raw).strip().replace("\\", "/")
    for part in s.split("/"):
        if not part:
            continue
        digits = "".join(c for c in part if c.isdigit())
        if len(digits) == 8:
            return digits
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) == 8:
        return digits
    return None


def narration_json_expects_dialogue_mode(data: dict[str, Any]) -> bool:
    """
    True when narration JSON was produced for character dialogue / multi-voice TTS.

    Prefer top-level ``dialogue_mode`` when present (written by merge when generating
    with --dialogue). Otherwise treat any segment with a non-empty ``dialogue`` list
    as dialogue-mode (legacy hand-edited files).
    """
    if isinstance(data.get("dialogue_mode"), bool):
        return data["dialogue_mode"]
    for seg in data.get("narration_script") or []:
        if not isinstance(seg, dict):
            continue
        dlg = seg.get("dialogue")
        if isinstance(dlg, list) and len(dlg) > 0:
            return True
    return False


def narration_json_expects_long_conversation_mode(data: dict[str, Any]) -> bool:
    """
    True when narration JSON was produced with --long-conversation (long episode / tracking).

    Prefer top-level ``long_conversation_mode`` when present (written by merge when generating
    with --long-conversation). Used for preflight consistency and for run-daily inference when
    neither ``--long-conversation`` nor ``--no-long-conversation`` is passed (same pattern as
    dialogue mode).
    """
    if isinstance(data.get("long_conversation_mode"), bool):
        return data["long_conversation_mode"]
    return False


def infer_long_conversation_effective_from_cli_and_narration(
    *,
    long_conversation_arg: bool,
    no_long_conversation_arg: bool,
    narration_path: Path,
) -> bool:
    """
    Resolve long-conversation mode for run-daily: explicit ``--long-conversation`` wins;
    ``--no-long-conversation`` forces off; otherwise read ``narrations/narration<date>.json``
    when present (mirrors dialogue inference).
    """
    if no_long_conversation_arg:
        return False
    if long_conversation_arg:
        return True
    if not narration_path.is_file():
        return False
    try:
        raw = narration_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    return narration_json_expects_long_conversation_mode(data)


def narration_path_for_date(date_id: str, narrations_dir: Path | None = None) -> Path:
    """Return Path to canonical narration JSON for date_id."""
    if narrations_dir is None:
        narrations_dir = Path(__file__).resolve().parent.parent / "narrations"
    return narrations_dir / f"narration{date_id}.json"


def load_narration(date_id: str, narrations_dir: Path | None = None) -> dict | None:
    """Load narration JSON for date_id. Returns None if missing or invalid."""
    path = narration_path_for_date(date_id, narrations_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_narration(
    date_id: str,
    data: dict,
    narrations_dir: Path | None = None,
) -> Path:
    """Write canonical narration JSON for date_id (indent=2, UTF-8)."""
    path = narration_path_for_date(date_id, narrations_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def is_narration_v2(data_or_path: dict | Path | str) -> bool:
    """True if narration is v2 (narration_version == '2.0'). Accepts dict, Path, or date_id string."""
    if isinstance(data_or_path, dict):
        return data_or_path.get("narration_version") == "2.0"
    if isinstance(data_or_path, Path):
        if not data_or_path.exists():
            return False
        try:
            data = json.loads(data_or_path.read_text(encoding="utf-8"))
            return data.get("narration_version") == "2.0"
        except (json.JSONDecodeError, OSError):
            return False
    if isinstance(data_or_path, str) and len(data_or_path) == 8:
        data = load_narration(data_or_path)
        return data is not None and data.get("narration_version") == "2.0"
    return False


def narration_script_row_1based(data: dict, segment_index: int) -> dict | None:
    """Return narration_script[] row for 1-based segment index."""
    script = data.get("narration_script") or []
    if not isinstance(script, list):
        return None
    for seg in script:
        if not isinstance(seg, dict):
            continue
        raw_idx = seg.get("segment_index")
        if raw_idx is None:
            continue
        try:
            if int(raw_idx) == segment_index:
                return seg
        except (TypeError, ValueError):
            continue
    if 1 <= segment_index <= len(script):
        return script[segment_index - 1] if isinstance(script[segment_index - 1], dict) else None
    return None


def visual_modes_for_date_id(date_id: str, narrations_dir: Path | None = None) -> list[str] | None:
    """Return per-segment visual_mode strings aligned with narration_script, or None if missing."""
    data = load_narration(date_id, narrations_dir)
    if not data:
        return None
    script = data.get("narration_script")
    if not isinstance(script, list) or not script:
        return None
    return visual_modes_for_narration_script(data)


def get_mid_episode_map_insertion(data: dict | None) -> dict | None:
    """
    If narration is v2 and has exactly one mid-episode map_insertion with segment_index,
    return it: {"segment_index": int, "visual_style": str, "duration_seconds": float}.
    Otherwise return None.
    """
    if not data or data.get("narration_version") != "2.0":
        return None
    insertions = data.get("map_insertions") or []
    if len(insertions) != 1:
        return None
    one = insertions[0]
    if one.get("placement") != "after_segment_index":
        return None
    seg_idx = one.get("segment_index")
    style = one.get("visual_style")
    if seg_idx is None or style != "parchment_overlay":
        return None
    dur_sec = one.get("duration_seconds")
    if dur_sec is None or dur_sec <= 0:
        return None
    return {
        "segment_index": int(seg_idx),
        "visual_style": style,
        "duration_seconds": float(dur_sec),
    }
