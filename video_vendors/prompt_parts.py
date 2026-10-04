"""Shared prompt fragments appended by build_prompts (imported by fal without cycles)."""

from __future__ import annotations

from pathlib import Path

from pipeline.prompt_pack_text import load_shared_prompt_text

_REPO_ROOT = Path(__file__).resolve().parent.parent

SAFETY_SUFFIX = load_shared_prompt_text(_REPO_ROOT, "video_safety_suffix.txt")
NO_ON_SCREEN_TEXT_PREFIX = load_shared_prompt_text(_REPO_ROOT, "video_no_on_screen_text_prefix.txt")
