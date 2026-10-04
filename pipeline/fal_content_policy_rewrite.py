"""Rewrite FAL-flagged visual prompts via OpenAI and persist them on narration JSON."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.narration_common import clean_json_reply
from pipeline.narration_phase2 import _validate_scene_plan_visual_safety
from pipeline.narration_utils import (
    load_narration,
    narration_path_for_date,
    narration_script_row_1based,
    save_narration,
)

_WRITE_LOCK = threading.Lock()

_REWRITE_SYSTEM = """You rewrite visual prompts for a family-friendly Lewis & Clark documentary so a
remote image/video safety checker will accept them.

Return ONLY JSON with keys:
  opening_frame: one still (t=0) sentence
  video_prompt: 2–4 sentences of clip motion (no camera jargon required)

Rules:
- Do not change spoken voiceover. Do not include narration text.
- Keep the same character name and camp/river setting when given.
- Kid-safe hunt language only. When the journal mentions hunting or meat, pick one:
  (a) hunter walking timber/prairie with a longarm, reading sign, no animal body in frame;
  (b) aiming or using a spyglass on a distant live deer/elk (animal small; no wound, blood, flash);
  (c) camp meat without a carcass: cooking a steak, spit, or carrying an opaque sack/bundle;
  (d) hunters returning to camp empty-handed except provisions sack, gesturing, talking.
- Never: carcass, freshly hunted/killed animal, kneeling on a dead animal, hand on flank,
  gripping antlers, dragging/shouldering game, blood, gore, taking down animals, weapons aimed at people.
- Natural human proportions; person and any animal must be fully separate.
"""


def content_policy_rewrite_enabled() -> bool:
    return os.environ.get("FAL_SKIP_CONTENT_POLICY_REWRITE", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    )


def parse_rewrite_reply(raw: str) -> dict[str, str]:
    parsed = json.loads(clean_json_reply(raw or ""))
    if not isinstance(parsed, dict):
        raise ValueError("rewrite reply must be a JSON object")
    opening = (parsed.get("opening_frame") or "").strip()
    video = (parsed.get("video_prompt") or "").strip()
    if not opening or not video:
        raise ValueError("rewrite reply must include non-empty opening_frame and video_prompt")
    _validate_scene_plan_visual_safety(
        {"opening_frame": opening, "primary_visual": video, "secondary_elements": ""},
        0,
    )
    return {"opening_frame": opening, "video_prompt": video}


def apply_visual_rewrite_to_narration(
    data: dict[str, Any],
    segment_index: int,
    *,
    opening_frame: str,
    video_prompt: str,
    reason: str = "content_policy_violation",
) -> dict[str, Any]:
    """Mutate canonical narration dict; return the updated script row."""
    row = narration_script_row_1based(data, segment_index)
    if not isinstance(row, dict):
        raise ValueError(f"no narration_script row for segment {segment_index}")
    row["opening_frame"] = opening_frame.strip()
    row["video_prompt"] = video_prompt.strip()
    row["fal_policy_rewrite"] = {
        "at_utc": datetime.now(UTC).isoformat(),
        "reason": reason,
    }
    return row


def _apply_visual_sidecar(
    sidecar: dict[str, Any],
    segment_index: int,
    *,
    opening_frame: str,
    video_prompt: str,
) -> None:
    plan = sidecar.get("scene_plan")
    if not isinstance(plan, list):
        return
    for item in plan:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("segment_index"))
        except (TypeError, ValueError):
            continue
        if idx != segment_index:
            continue
        vs = item.get("visual_strategy")
        if not isinstance(vs, dict):
            vs = {}
            item["visual_strategy"] = vs
        vs["opening_frame"] = opening_frame
        vs["primary_visual"] = video_prompt
        return


def persist_segment_visual_rewrite(
    date_id: str,
    segment_index: int,
    *,
    opening_frame: str,
    video_prompt: str,
    reason: str = "content_policy_violation",
    narrations_dir: Path | None = None,
) -> Path:
    """Write opening_frame + video_prompt onto merged narration (and visual sidecar if present)."""
    with _WRITE_LOCK:
        data = load_narration(date_id, narrations_dir)
        if not data:
            path = narration_path_for_date(date_id, narrations_dir)
            raise FileNotFoundError(f"narration JSON missing: {path}")
        apply_visual_rewrite_to_narration(
            data,
            segment_index,
            opening_frame=opening_frame,
            video_prompt=video_prompt,
            reason=reason,
        )
        out = save_narration(date_id, data, narrations_dir)
        vis = out.parent / f"narration{date_id}_visual.json"
        if vis.is_file():
            try:
                sidecar = json.loads(vis.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                sidecar = None
            if isinstance(sidecar, dict):
                _apply_visual_sidecar(
                    sidecar,
                    segment_index,
                    opening_frame=opening_frame.strip(),
                    video_prompt=video_prompt.strip(),
                )
                vis.write_text(
                    json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
        return out


def rewrite_segment_visuals_via_openai(
    *,
    spoken_narration: str,
    opening_frame: str,
    video_prompt: str,
    flagged_prompt: str,
    error_text: str,
    character_id: str = "",
    client: Any = None,
    model: str = "gpt-4o-mini",
) -> dict[str, str]:
    from openai import OpenAI

    from pipeline_logging import log_api_call_with_bodies

    oai = client or OpenAI()
    user = {
        "spoken_narration_do_not_rewrite": spoken_narration,
        "current_opening_frame": opening_frame,
        "current_video_prompt": video_prompt,
        "reference_character_id": character_id,
        "flagged_fal_prompt_excerpt": (flagged_prompt or "")[:2500],
        "fal_error": (error_text or "")[:1500],
    }
    messages = [
        {"role": "system", "content": _REWRITE_SYSTEM},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]
    response = oai.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.4,
        max_tokens=700,
    )
    raw = response.choices[0].message.content or ""
    log_api_call_with_bodies(
        "openai",
        "chat.completions.create",
        request_body=messages,
        response_body=raw,
        model=model,
        extra={"purpose": "fal_content_policy_rewrite"},
    )
    return parse_rewrite_reply(raw)


def rewrite_and_persist_after_fal_policy(
    date_id: str,
    segment_index: int,
    *,
    flagged_prompt: str,
    error_text: str,
    character_id: str = "",
    narrations_dir: Path | None = None,
    client: Any = None,
    model: str = "gpt-4o-mini",
) -> dict[str, str] | None:
    """OpenAI-rewrite visuals for one segment, save JSON, return new fields (or None if skipped)."""
    if not content_policy_rewrite_enabled():
        return None
    data = load_narration(date_id, narrations_dir)
    if not data:
        return None
    row = narration_script_row_1based(data, segment_index)
    if not isinstance(row, dict):
        return None
    spoken = str(row.get("narration") or "")
    opening = str(row.get("opening_frame") or "")
    video = str(row.get("video_prompt") or "")
    rewritten = rewrite_segment_visuals_via_openai(
        spoken_narration=spoken,
        opening_frame=opening,
        video_prompt=video,
        flagged_prompt=flagged_prompt,
        error_text=error_text,
        character_id=character_id or str(row.get("reference_character_id") or ""),
        client=client,
        model=model,
    )
    persist_segment_visual_rewrite(
        date_id,
        segment_index,
        opening_frame=rewritten["opening_frame"],
        video_prompt=rewritten["video_prompt"],
        reason="content_policy_violation",
        narrations_dir=narrations_dir,
    )
    return rewritten
