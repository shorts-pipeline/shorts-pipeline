"""Talking-head shot templates (config-driven snippets merged into talking_head_prompt)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


def _default_config_path(repo_root: Path) -> Path:
    return repo_root / "config" / "talking_head_shots.json"


@lru_cache(maxsize=8)
def _load_raw(path_str: str) -> dict[str, Any]:
    p = Path(path_str)
    if not p.is_file():
        return {"templates": {}, "archetypes": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"templates": {}, "archetypes": {}}
    return data if isinstance(data, dict) else {"templates": {}, "archetypes": {}}


def load_shot_library(repo_root: Path) -> dict[str, Any]:
    return dict(_load_raw(str(_default_config_path(repo_root).resolve())))


def resolve_shot_snippet(repo_root: Path, seg_p1: dict[str, Any]) -> str | None:
    """
    Return extra talking-head guidance from ``talking_head_shot_id`` or ``talking_head_archetype``.

    Phase 1 may set:
    - ``talking_head_shot_id``: key in ``templates``
    - ``talking_head_archetype``: key in ``archetypes`` → resolved template id
    """
    lib = load_shot_library(repo_root)
    templates: dict[str, str] = {}
    arch: dict[str, str] = {}
    if isinstance(lib.get("templates"), dict):
        templates = {str(k): str(v) for k, v in lib["templates"].items() if str(v).strip()}
    if isinstance(lib.get("archetypes"), dict):
        arch = {
            str(k).strip().lower(): str(v).strip()
            for k, v in lib["archetypes"].items()
            if str(v).strip()
        }

    shot_id = (seg_p1.get("talking_head_shot_id") or "").strip()
    if shot_id and shot_id in templates:
        return templates[shot_id].strip()

    arch_key = (seg_p1.get("talking_head_archetype") or "").strip().lower()
    if arch_key:
        tid = arch.get(arch_key) or arch.get("default")
        if tid and tid in templates:
            return templates[tid].strip()
    return None
