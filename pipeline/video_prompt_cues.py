"""Conditional append rules for merged narration video prompts (post Phase 2 merge)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "video_prompt_cues.json"

_ALLOWED_SCAN_FIELDS = frozenset(
    {"narration", "video_prompt", "opening_frame", "stage_direction", "talking_head_prompt"}
)
_ALLOWED_APPEND_FIELDS = frozenset({"video_prompt", "opening_frame", "talking_head_prompt"})


@dataclass(frozen=True)
class VideoPromptCue:
    id: str
    scan_fields: tuple[str, ...]
    trigger_re: re.Pattern[str]
    skip_if_re: re.Pattern[str] | None
    append_fields: tuple[str, ...]
    append: str


@dataclass(frozen=True)
class VideoPromptCueConfig:
    enabled: bool
    cues: tuple[VideoPromptCue, ...]


def video_prompt_cues_disabled() -> bool:
    return os.environ.get("LEWISCLARK_VIDEO_PROMPT_CUES", "").strip().lower() in (
        "0",
        "false",
        "off",
        "no",
    )


def default_video_prompt_cues_path() -> Path:
    return _DEFAULT_CONFIG_PATH


def _compile_pattern(raw: str, *, label: str, cue_id: str) -> re.Pattern[str]:
    text = (raw or "").strip()
    if not text:
        raise ValueError(f"cue {cue_id!r}: {label} must be non-empty")
    try:
        return re.compile(text)
    except re.error as e:
        raise ValueError(f"cue {cue_id!r}: invalid {label}: {e}") from e


def _normalize_field_list(
    raw: Any,
    *,
    allowed: frozenset[str],
    default: tuple[str, ...],
    cue_id: str,
    key: str,
) -> tuple[str, ...]:
    if raw is None:
        return default
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"cue {cue_id!r}: {key} must be a non-empty array of strings")
    out: list[str] = []
    for j, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"cue {cue_id!r}: {key}[{j}] must be a non-empty string")
        name = item.strip()
        if name not in allowed:
            raise ValueError(f"cue {cue_id!r}: {key}[{j}] {name!r} not in {sorted(allowed)}")
        if name not in out:
            out.append(name)
    return tuple(out)


def load_video_prompt_cues_config(path: Path | None = None) -> VideoPromptCueConfig:
    """Load and validate ``config/video_prompt_cues.json`` (or ``path``)."""
    cfg_path = path or _DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        return VideoPromptCueConfig(enabled=False, cues=())

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Could not read video prompt cues config {cfg_path}: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"{cfg_path}: root must be an object")

    enabled = bool(data.get("enabled", True))
    raw_cues = data.get("cues")
    if raw_cues is None:
        return VideoPromptCueConfig(enabled=enabled, cues=())
    if not isinstance(raw_cues, list):
        raise ValueError(f"{cfg_path}: cues must be an array")

    compiled: list[VideoPromptCue] = []
    for j, raw in enumerate(raw_cues):
        if not isinstance(raw, dict):
            raise ValueError(f"{cfg_path}: cues[{j}] must be an object")
        cue_id = str(raw.get("id") or "").strip() or f"cues[{j}]"
        if raw.get("enabled", True) is False:
            continue

        append = str(raw.get("append") or "").strip()
        if not append:
            raise ValueError(f"cue {cue_id!r}: append must be a non-empty string")

        scan_fields = _normalize_field_list(
            raw.get("scan_fields"),
            allowed=_ALLOWED_SCAN_FIELDS,
            default=("narration", "video_prompt", "opening_frame"),
            cue_id=cue_id,
            key="scan_fields",
        )
        append_fields = _normalize_field_list(
            raw.get("append_fields"),
            allowed=_ALLOWED_APPEND_FIELDS,
            default=("video_prompt",),
            cue_id=cue_id,
            key="append_fields",
        )

        trigger_re = _compile_pattern(
            str(raw.get("trigger_pattern") or ""),
            label="trigger_pattern",
            cue_id=cue_id,
        )
        skip_raw = raw.get("skip_if_pattern")
        skip_if_re: re.Pattern[str] | None = None
        if skip_raw is not None and str(skip_raw).strip():
            skip_if_re = _compile_pattern(str(skip_raw), label="skip_if_pattern", cue_id=cue_id)

        compiled.append(
            VideoPromptCue(
                id=cue_id,
                scan_fields=scan_fields,
                trigger_re=trigger_re,
                skip_if_re=skip_if_re,
                append_fields=append_fields,
                append=append,
            )
        )

    return VideoPromptCueConfig(enabled=enabled, cues=tuple(compiled))


def _segment_field_text(seg: dict[str, Any], field: str) -> str:
    raw = seg.get(field)
    return raw.strip() if isinstance(raw, str) else ""


def _build_scan_blob(seg: dict[str, Any], scan_fields: tuple[str, ...]) -> str:
    parts = [_segment_field_text(seg, f) for f in scan_fields]
    return "\n".join(p for p in parts if p)


def _should_skip_append(text: str, cue: VideoPromptCue) -> bool:
    if not text:
        return True
    if cue.skip_if_re is not None and cue.skip_if_re.search(text):
        return True
    append_lower = cue.append.lower()
    if append_lower and append_lower in text.lower():
        return True
    return False


def _append_fragment(text: str, fragment: str) -> str:
    frag = fragment.strip()
    if not frag:
        return text
    base = (text or "").rstrip()
    if not base:
        return frag
    if base.endswith((".", "!", "?")):
        return f"{base} {frag}"
    return f"{base}. {frag}"


def apply_video_prompt_cues_to_segment(
    seg: dict[str, Any], cues: tuple[VideoPromptCue, ...]
) -> list[str]:
    """
    Apply all matching cues to one ``narration_script`` segment in place.

    Returns cue ids that fired (for logging/tests).
    """
    applied: list[str] = []
    if not cues:
        return applied

    scan_blob = ""
    for cue in cues:
        scan_blob = _build_scan_blob(seg, cue.scan_fields)
        if not scan_blob or not cue.trigger_re.search(scan_blob):
            continue
        if cue.skip_if_re is not None and cue.skip_if_re.search(scan_blob):
            continue

        fired = False
        for field in cue.append_fields:
            current = _segment_field_text(seg, field)
            if not current:
                continue
            if _should_skip_append(current, cue):
                continue
            seg[field] = _append_fragment(current, cue.append)
            fired = True

        if fired and cue.id not in applied:
            applied.append(cue.id)

    return applied


def apply_video_prompt_cues(
    parsed: dict[str, Any],
    config: VideoPromptCueConfig | None = None,
    *,
    config_path: Path | None = None,
) -> dict[str, list[str]]:
    """
    Apply conditional video-prompt cues to merged narration JSON (in place).

    Returns ``{segment_index: [cue_id, ...]}`` for segments that received at least one append.
    """
    if video_prompt_cues_disabled():
        return {}

    cfg = config if config is not None else load_video_prompt_cues_config(config_path)
    if not cfg.enabled or not cfg.cues:
        return {}

    script = parsed.get("narration_script")
    if not isinstance(script, list):
        return {}

    log: dict[str, list[str]] = {}
    for seg in script:
        if not isinstance(seg, dict):
            continue
        applied = apply_video_prompt_cues_to_segment(seg, cfg.cues)
        if not applied:
            continue
        idx = seg.get("segment_index")
        key = str(idx) if idx is not None else "?"
        log[key] = applied

    return log
