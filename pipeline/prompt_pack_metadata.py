"""Prompt pack versioning and fingerprints for reproducible narration runs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.prompt_pack_paths import packs_root


def pack_dir(repo_root: Path, pack_id: str) -> Path:
    return packs_root(repo_root) / pack_id.strip()


def read_pack_manifest(repo_root: Path, pack_id: str) -> dict[str, Any]:
    """
    Load ``prompt_packs/<pack>/pack.json`` if present.

    Returns at least ``id`` and ``version`` (``unversioned`` when file missing).
    """
    pid = (pack_id or "").strip() or "unknown"
    manifest_path = pack_dir(repo_root, pid) / "pack.json"
    base: dict[str, Any] = {"id": pid, "version": "unversioned"}
    if not manifest_path.is_file():
        return dict(base)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(base)
    if not isinstance(data, dict):
        return dict(base)
    out = {**base, **data}
    out.setdefault("id", pid)
    out.setdefault("version", "unversioned")
    return out


def _pack_text_blobs(repo_root: Path, pack_id: str) -> list[tuple[str, bytes]]:
    """Ordered (label, bytes) for fingerprinting."""
    root = pack_dir(repo_root, (pack_id or "").strip() or "unknown")
    items: list[tuple[str, bytes]] = []
    for name in (
        "phase1_system.txt",
        "phase1_dialogue_system.txt",
        "phase2_system.txt",
        "phase1_user.txt",
    ):
        p = root / name
        if p.is_file():
            items.append((name, p.read_bytes()))
    return items


def fingerprint_phase1_dialogue_system(repo_root: Path, pack_id: str) -> str | None:
    """SHA-256 hex of phase1_dialogue_system.txt when present; else None."""
    root = pack_dir(repo_root, (pack_id or "").strip() or "unknown")
    p = root / "phase1_dialogue_system.txt"
    if not p.is_file():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fingerprint_prompt_pack(repo_root: Path, pack_id: str) -> str:
    """SHA-256 hex of concatenated pack text files (stable order)."""
    blobs = _pack_text_blobs(repo_root, pack_id)
    h = hashlib.sha256()
    for label, raw in blobs:
        h.update(label.encode("utf-8"))
        h.update(b"\0")
        h.update(raw)
        h.update(b"\n")
    return h.hexdigest()


def build_prompt_pack_lineage(
    repo_root: Path,
    *,
    phase1_pack: str,
    phase2_pack: str,
    phase1_dialogue_pack: str | None = None,
) -> dict[str, Any]:
    """Structured metadata stored on merged narration JSON."""
    now = datetime.now(UTC).isoformat()
    out: dict[str, Any] = {
        "generated_at_utc": now,
        "phase1": {
            "pack_id": phase1_pack,
            "manifest": read_pack_manifest(repo_root, phase1_pack),
            "fingerprint_sha256": fingerprint_prompt_pack(repo_root, phase1_pack),
        },
        "phase2": {
            "pack_id": phase2_pack,
            "manifest": read_pack_manifest(repo_root, phase2_pack),
            "fingerprint_sha256": fingerprint_prompt_pack(repo_root, phase2_pack),
        },
    }
    pid = (phase1_dialogue_pack or "").strip()
    if pid:
        fd = fingerprint_phase1_dialogue_system(repo_root, pid)
        if fd:
            out["phase1_dialogue"] = {
                "pack_id": pid,
                "manifest": read_pack_manifest(repo_root, pid),
                "fingerprint_sha256": fd,
            }
    return out
