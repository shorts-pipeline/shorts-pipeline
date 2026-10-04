"""Load Phase 2 and shared vendor prompt text from ``prompt_packs/``."""

from __future__ import annotations

from pathlib import Path

from pipeline.prompt_pack_paths import packs_root

DEFAULT_PHASE2_FALLBACK_PACK = "lewis_clark"

_PHASE2_REQUIRED = (
    "__ANCHOR_HINTS_BLOCK__",
    "__AMBIENT_TAG_ENUM__",
    "__AMBIENT_TAG_CSV__",
    "__RUNTIME_MAP_POLICY_BLOCK__",
    "{focus_tone_relax}",
)


def load_phase2_system_template(repo_root: Path, prompt_pack: str) -> str:
    """
    Load ``prompt_packs/<pack>/phase2_system.txt``; fall back to ``lewis_clark`` if missing.
    Validates required placeholders for substitution in ``build_phase2_system_prompt``.
    """
    pack = (prompt_pack or "").strip() or DEFAULT_PHASE2_FALLBACK_PACK
    primary = packs_root(repo_root) / pack / "phase2_system.txt"
    if primary.is_file():
        text = primary.read_text(encoding="utf-8")
        _validate_phase2_system_template(text, primary)
        return text
    if pack != DEFAULT_PHASE2_FALLBACK_PACK:
        fb = packs_root(repo_root) / DEFAULT_PHASE2_FALLBACK_PACK / "phase2_system.txt"
        if fb.is_file():
            text = fb.read_text(encoding="utf-8")
            _validate_phase2_system_template(text, fb)
            return text
    raise FileNotFoundError(
        f"Missing Phase 2 system prompt for pack {pack!r}: {primary} "
        f"(no fallback {DEFAULT_PHASE2_FALLBACK_PACK}/phase2_system.txt)"
    )


def _validate_phase2_system_template(text: str, path: Path) -> None:
    missing = [x for x in _PHASE2_REQUIRED if x not in text]
    if missing:
        raise ValueError(f"phase2_system.txt {path} missing placeholders: {missing}")


def load_shared_prompt_text(repo_root: Path, filename: str) -> str:
    """Load ``prompt_packs/_shared/<filename>`` (UTF-8)."""
    p = packs_root(repo_root) / "_shared" / filename
    if not p.is_file():
        raise FileNotFoundError(f"Missing shared prompt file: {p}")
    return p.read_text(encoding="utf-8")
