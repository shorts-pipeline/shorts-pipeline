"""Phase 1 dialogue polish pass (phase1-dialogue): reshape draft dialogue[].text after main Phase 1."""

from __future__ import annotations

import copy
import json
import sys
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from pipeline.narration_characters.voice_prompt import dialogue_profile_prompt_section
from pipeline.narration_common import clean_json_reply
from pipeline.narration_phase1 import (
    DEFAULT_MAX_TOKENS,
    MAX_RETRIES,
    _call_api,
    _strip_deprecated_phase1_fields,
    _validate_phase1,
)
from pipeline.prompt_pack_paths import packs_root


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def build_phase1_dialogue_system_prompt(
    repo_root: Path | None = None, prompt_pack: str = "lewis_clark_dialogue"
) -> str:
    root = repo_root or _default_repo_root()
    path = packs_root(root) / prompt_pack / "phase1_dialogue_system.txt"
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing phase1-dialogue system prompt for pack {prompt_pack!r}: {path}"
        )
    return path.read_text(encoding="utf-8").strip()


def _conversation_micro_arc_polish_hint(phase1: dict[str, Any]) -> str:
    arc = phase1.get("conversation_micro_arc")
    if not isinstance(arc, dict):
        return ""
    speakers = arc.get("speakers")
    indices = arc.get("segment_indices")
    if not isinstance(speakers, list) or len(speakers) != 2:
        return ""
    if not isinstance(indices, list) or not indices:
        return ""
    a, b = (str(speakers[0] or "").strip().lower(), str(speakers[1] or "").strip().lower())
    if not a or not b or a == b:
        return ""
    idx_text = ", ".join(str(int(x)) for x in indices if str(x).strip().isdigit())
    if not idx_text:
        return ""
    return (
        "CONVERSATION MICRO-ARC (mandatory dyad polish):\n"
        f"- Segments {idx_text}: two-person exchange between `{a}` and `{b}`.\n"
        "- Rewrite dialogue in those segments so each speaker addresses the **other** roster member.\n"
        "- Convert third-party vocatives and corps-wide orders into interpersonal negotiation; "
        "each turn must respond to the previous segment's speaker.\n"
        "- Prefer **you/your** over first names; use a vocative at most once or twice in the run."
    )


def build_phase1_dialogue_user_prompt(phase1: dict[str, Any], date_id: str) -> str:
    voice = dialogue_profile_prompt_section()
    parts = [
        f"date_id: {date_id}",
        "",
    ]
    arc_hint = _conversation_micro_arc_polish_hint(phase1)
    if arc_hint:
        parts.extend([arc_hint, ""])
    parts.extend(
        [
            "Phase 1 JSON to polish (rewrite only dialogue line texts as instructed):",
            "",
            json.dumps(phase1, indent=2, ensure_ascii=False),
        ]
    )
    if voice.strip():
        parts.extend(["", voice])
    return "\n".join(parts)


def merge_phase1_dialogue_texts_only(
    base: dict[str, Any], model_segments: list[Any]
) -> dict[str, Any]:
    """Apply dialogue[].text strings from model output; preserve everything else from base."""
    out = copy.deepcopy(base)
    base_segments = out.get("segments")
    if not isinstance(base_segments, list):
        raise ValueError("base segments missing")
    if not isinstance(model_segments, list):
        raise ValueError("model segments must be a list")
    if len(model_segments) != len(base_segments):
        raise ValueError(
            f"phase1-dialogue segment count mismatch: model {len(model_segments)} vs base {len(base_segments)}"
        )
    for i, (bseg, mseg) in enumerate(zip(base_segments, model_segments, strict=True)):
        if not isinstance(bseg, dict) or not isinstance(mseg, dict):
            raise ValueError(f"segment {i} must be objects")
        bd = bseg.get("dialogue")
        md = mseg.get("dialogue")
        if bd is None:
            bd = []
        if md is None:
            md = []
        if not isinstance(bd, list) or not isinstance(md, list):
            raise ValueError(f"segment {i} dialogue must be lists")
        if len(md) != len(bd):
            raise ValueError(
                f"segment {i} dialogue row count mismatch: model {len(md)} vs base {len(bd)}"
            )
        for j, (bline, mline) in enumerate(zip(bd, md, strict=True)):
            if not isinstance(bline, dict) or not isinstance(mline, dict):
                raise ValueError(f"segment {i} dialogue[{j}] must be objects")
            if bline.get("speaker_id") != mline.get("speaker_id"):
                raise ValueError(
                    f"segment {i} dialogue[{j}] speaker_id changed "
                    f"({bline.get('speaker_id')!r} -> {mline.get('speaker_id')!r})"
                )
            mt = mline.get("text")
            if not isinstance(mt, str):
                raise ValueError(f"segment {i} dialogue[{j}].text must be string")
            bline["text"] = mt
    return out


def run_phase1_dialogue(
    phase1: dict[str, Any],
    date_id: str,
    *,
    model: str = "gpt-4o",
    max_retries: int = MAX_RETRIES,
    repo_root: Path | None = None,
    prompt_pack: str = "lewis_clark_dialogue",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    conversation_mode: dict[str, Any] | None = None,
    provider: str = "openai",
) -> dict[str, Any]:
    """
    Rewrite dialogue[].text using phase1_dialogue_system.txt. Returns unchanged phase1 if prompt file missing.
    """
    root = repo_root or _default_repo_root()
    ppath = packs_root(root) / prompt_pack / "phase1_dialogue_system.txt"
    if not ppath.is_file():
        print(
            f"[WARN] phase1-dialogue skipped: missing {ppath}",
            file=sys.stderr,
        )
        return phase1

    system = build_phase1_dialogue_system_prompt(repo_root=root, prompt_pack=prompt_pack)
    user = build_phase1_dialogue_user_prompt(phase1, date_id)
    base_user = user
    for attempt in range(max_retries + 1):
        raw = _call_api(system, user, model, max_tokens=max_tokens, provider=provider)
        cleaned = clean_json_reply(raw)
        try:
            parsed = json.loads(cleaned)
            merged = merge_phase1_dialogue_texts_only(phase1, parsed.get("segments") or [])
            _strip_deprecated_phase1_fields(merged)
            _validate_phase1(
                merged,
                dialogue_mode=True,
                prompt_pack=prompt_pack,
                conversation_mode=conversation_mode,
            )
            return merged
        except (JSONDecodeError, ValueError, TypeError) as e:
            if attempt >= max_retries:
                print(f"[ERROR] phase1-dialogue failed: {e}", file=sys.stderr)
                break
            user = (
                f"{base_user}\n\n"
                "----------------------------------------------------------------\n"
                "RETRY: The previous response was invalid or broke the dialogue merge rules.\n"
                f"Validation error: {e}\n"
                "Return ONLY valid JSON with the SAME structure as the input above, and ONLY change "
                "segments[].dialogue[].text strings. Preserve every speaker_id and segment count. "
                "If spoken word limits failed, lengthen short cast lines without changing facts.\n\n"
                f"Previous (invalid) reply:\n{cleaned}"
            )
    raise RuntimeError(
        f"phase1-dialogue failed after {max_retries + 1} attempts for date {date_id}"
    )
