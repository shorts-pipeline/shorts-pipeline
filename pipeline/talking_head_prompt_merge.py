"""Merge-time assembly of ``talking_head_prompt`` for FAL OmniHuman v1.5."""

from __future__ import annotations

import re
from typing import Any

from pipeline.conversation_visual_spine import find_talking_head_conversation_runs

_BACKDROP_SUFFIX_RE = re.compile(
    r"\s*Same fixed backdrop throughout this conversation:.*$",
    re.IGNORECASE | re.DOTALL,
)
_EYELINE_HINT_RE = re.compile(
    r"(eye[- ]?line|gaze|toward|off[- ]camera|listener|three[- ]quarter|facing|looks at)",
    re.IGNORECASE,
)
_PING_PONG_DEFAULT_SHOT_ID = "ots_listener_shoulders"

_MOTION_STYLE_PROMPTS: dict[str, str] = {
    "static": "Camera holds steady with minimal drift.",
    "slow_push": "Camera slowly pushes in slightly.",
    "slow push": "Camera slowly pushes in slightly.",
    "drift": "Camera drifts subtly with the speaker.",
    "tracking": "Camera tracks gently with restrained movement.",
}


def ping_pong_talking_head_indices(segments: list[Any]) -> set[int]:
    """1-based segment indices in alternating-speaker talking_head conversation runs."""
    out: set[int] = set()
    for run in find_talking_head_conversation_runs(segments):
        for idx in run:
            out.add(idx + 1)
    return out


def _strip(v: Any) -> str:
    return str(v or "").strip()


def _strip_backdrop_suffix(opening_frame: str) -> str:
    return _BACKDROP_SUFFIX_RE.sub("", _strip(opening_frame)).strip()


def extract_eyeline_snippet(opening_frame: str, *, max_len: int = 220) -> str:
    """
    Pull eyeline / gaze prose from ``opening_frame`` for OmniHuman ``prompt``.

    Skips long conversation-backdrop suffixes; prefers the shortest sentence that
    mentions gaze or listener eyeline.
    """
    frame = _strip_backdrop_suffix(opening_frame)
    if not frame or not _EYELINE_HINT_RE.search(frame):
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", frame)
    candidates = [s.strip() for s in sentences if s.strip() and _EYELINE_HINT_RE.search(s)]
    if not candidates:
        snippet = frame
    else:
        snippet = min(candidates, key=len)
    if len(snippet) > max_len:
        snippet = snippet[: max_len - 1].rstrip() + "…"
    return snippet


def motion_style_avatar_hint(motion_style: str) -> str:
    """Map Phase 2 ``motion_style`` to a short OmniHuman camera line."""
    key = _strip(motion_style).lower().replace("_", " ")
    if not key:
        return ""
    return _MOTION_STYLE_PROMPTS.get(key) or _MOTION_STYLE_PROMPTS.get(key.replace(" ", "_"), "")


def _part_present(parts: list[str], needle: str) -> bool:
    n = _strip(needle).lower()
    if not n:
        return True
    blob = " ".join(parts).lower()
    return n in blob


def build_merged_talking_head_prompt(
    seg_p1: dict[str, Any],
    *,
    opening_frame: str = "",
    motion_style: str = "",
    in_ping_pong_run: bool = False,
    repo_root: Any = None,
) -> str | None:
    """
    Assemble final ``talking_head_prompt`` for merged narration JSON.

    Order: Phase 1 beat → motion/camera → director note → eyeline from opening_frame
    → shot-library snippet (explicit archetype/shot_id, or ping-pong default).
    """
    th_parts: list[str] = []
    thp = seg_p1.get("talking_head_prompt")
    if isinstance(thp, str) and thp.strip():
        th_parts.append(thp.strip())

    cam = motion_style_avatar_hint(motion_style)
    if cam and not _part_present(th_parts, cam):
        th_parts.append(cam)

    ct = seg_p1.get("conversation_tracking")
    if isinstance(ct, dict):
        dn = _strip(ct.get("director_note"))
        if dn and not _part_present(th_parts, dn):
            th_parts.append(dn)

    eyeline = extract_eyeline_snippet(opening_frame)
    if eyeline and not _part_present(th_parts, eyeline):
        th_parts.append(eyeline)

    shot_extra: str | None = None
    if repo_root is not None:
        try:
            from pipeline.talking_head_shots import resolve_shot_snippet

            seg_for_shot = dict(seg_p1)
            has_explicit = bool(
                _strip(seg_p1.get("talking_head_shot_id"))
                or _strip(seg_p1.get("talking_head_archetype"))
            )
            if in_ping_pong_run and not has_explicit:
                seg_for_shot = {**seg_p1, "talking_head_shot_id": _PING_PONG_DEFAULT_SHOT_ID}
            shot_extra = resolve_shot_snippet(repo_root, seg_for_shot)
        except Exception:
            shot_extra = None
    if shot_extra and not _part_present(th_parts, shot_extra):
        th_parts.append(shot_extra)

    if not th_parts:
        return None
    return "\n\n".join(th_parts)


def talking_head_prompt_missing_warning(segment_index: int, model_id: str) -> str | None:
    """Return a warning message when OmniHuman v1.5 would run without a text prompt."""
    mid = (model_id or "").lower()
    if "omnihuman" not in mid or "/v1.5" not in mid:
        return None
    return (
        f"Segment {segment_index}: talking_head has no talking_head_prompt — "
        f"OmniHuman v1.5 will run image+audio only (weaker motion control)."
    )
