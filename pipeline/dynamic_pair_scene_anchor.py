"""
Dynamic two-person scene anchors for b_roll (and any per-segment Wan i2i).

When ``reference_character_id`` is a pair composite (configured or ad-hoc, e.g.
``colter_seaman``), build the scene anchor from **two solo portrait references**
instead of requiring a pre-built side-by-side PNG under ``character-portraits/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline.narration_characters.storage import (
    character_portrait_asset_exists,
    portrait_exists_for_composite,
)


@dataclass(frozen=True)
class DynamicPairReference:
    composite_id: str
    left_member_id: str
    right_member_id: str


def resolve_pair_member_ids_for_composite(composite_id: str) -> tuple[str, str] | None:
    """
    Return ``(left_member_id, right_member_id)`` for a composite id.

    Uses ``pair_portraits`` member order when configured; otherwise tries every
    ``_`` split where both parts have solo portrait assets (ad-hoc ``{a}_{b}`` ids).
    """
    cid = (composite_id or "").strip().lower()
    if not cid:
        return None
    try:
        import json

        from pipeline.narration_characters.paths import CHAR_PATH

        if CHAR_PATH.is_file():
            raw = json.loads(CHAR_PATH.read_text(encoding="utf-8"))
            for row in raw.get("pair_portraits") or []:
                if not isinstance(row, dict):
                    continue
                comp = str(row.get("composite_id") or "").strip().lower()
                mids_raw = row.get("member_ids")
                if comp != cid or not isinstance(mids_raw, list) or len(mids_raw) < 2:
                    continue
                mids = [str(x).strip().lower() for x in mids_raw if str(x).strip()]
                if len(mids) >= 2:
                    return mids[0], mids[1]
    except Exception:
        pass
    parts = cid.split("_")
    if len(parts) < 2:
        return None
    for i in range(1, len(parts)):
        left = "_".join(parts[:i])
        right = "_".join(parts[i:])
        if character_portrait_asset_exists(left) and character_portrait_asset_exists(right):
            return left, right
    return None


def resolve_dynamic_pair_reference(composite_id: str) -> DynamicPairReference | None:
    """Resolve composite id to left/right members when a pair reference is meaningful."""
    cid = (composite_id or "").strip().lower()
    members = resolve_pair_member_ids_for_composite(cid)
    if not members:
        return None
    left, right = members
    return DynamicPairReference(composite_id=cid, left_member_id=left, right_member_id=right)


def pair_reference_ready(composite_id: str) -> bool:
    """True when a composite PNG exists or both solo member portraits are on disk."""
    cid = (composite_id or "").strip()
    if not cid:
        return False
    if portrait_exists_for_composite(cid):
        return True
    members = resolve_pair_member_ids_for_composite(cid)
    if not members:
        return False
    left, right = members
    return character_portrait_asset_exists(left) and character_portrait_asset_exists(right)


def is_dynamic_pair_composite(character_id: str) -> bool:
    """True when ``character_id`` names a two-person pair (configured or ad-hoc)."""
    return resolve_dynamic_pair_reference(character_id) is not None


def dual_portrait_data_uris_for_pair(
    repo_root: Path,
    pair: DynamicPairReference,
) -> list[str]:
    """Solo portrait data URIs for Wan dual-reference i2i (left then right)."""
    from video_vendors.fal import FalVendor

    uris: list[str] = []
    for speaker_id in (pair.left_member_id, pair.right_member_id):
        portrait_path = FalVendor._portrait_path_for_character(repo_root, speaker_id)
        if portrait_path is None:
            raise FileNotFoundError(
                f"No portrait for pair member {speaker_id!r} under character-portraits/"
            )
        uris.append(FalVendor._data_uri_for_image(portrait_path))
    return uris


def broll_dual_portrait_kwargs(
    repo_root: Path,
    character_id: str,
) -> dict[str, Any] | None:
    """
    Kwargs for ``run_segment_anchor_to_disk`` when a b_roll pair should use dual solo i2i.

    Prefers dual portraits when both member solos exist (same approach as conversation
    master ``dual_portrait`` mode). Falls back to None so callers use a composite PNG
    or single-subject portrait when only those exist.
    """
    pair = resolve_dynamic_pair_reference(character_id)
    if pair is None:
        return None
    if not (
        character_portrait_asset_exists(pair.left_member_id)
        and character_portrait_asset_exists(pair.right_member_id)
    ):
        return None
    try:
        uris = dual_portrait_data_uris_for_pair(repo_root, pair)
    except FileNotFoundError:
        return None
    return {
        "portrait_data_uris": uris,
        "dual_reference_speakers": (pair.left_member_id, pair.right_member_id),
    }


def portrait_reference_ready(character_id: str) -> bool:
    """True when any portrait-backed i2i path exists for ``character_id`` (solo or pair)."""
    cid = (character_id or "").strip()
    if not cid:
        return False
    if portrait_exists_for_composite(cid):
        return True
    if character_portrait_asset_exists(cid):
        return True
    return pair_reference_ready(cid)
