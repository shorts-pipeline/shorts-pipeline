"""Bookend conversation anchors: two-shot master open/close, solo split crops in the middle."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from pipeline.conversation_omnihuman_mask import (
    build_speaker_mask_png,
    omnihuman_shorts_master_path,
    omnihuman_speaker_mask_path,
    reframe_master_for_omnihuman,
    resolve_omnihuman_mask_for_segment,
)
from pipeline.conversation_scene_anchor import (
    ConversationAnchorRun,
    _estimate_speaker_face_centers,
    _speaker_for_segment_in_run,
    _strip,
    conversation_anchor_face_y_fraction,
    manifest_path,
    split_master_to_speaker_images,
)


def conversation_segment_is_bookend(run: ConversationAnchorRun, segment_index: int) -> bool:
    """True when this turn uses the reframed two-shot master (first or last in the run)."""
    indices = run.segment_indices
    if not indices or segment_index not in indices:
        return False
    if len(indices) <= 2:
        return True
    return segment_index == indices[0] or segment_index == indices[-1]


def bookend_segment_indices(run: ConversationAnchorRun) -> tuple[int, ...]:
    """1-based segment indices that use the two-shot master + mask."""
    if len(run.segment_indices) <= 2:
        return run.segment_indices
    return (run.segment_indices[0], run.segment_indices[-1])


def middle_segment_indices(run: ConversationAnchorRun) -> tuple[int, ...]:
    """1-based segment indices that use per-speaker split crops."""
    bookends = set(bookend_segment_indices(run))
    return tuple(si for si in run.segment_indices if si not in bookends)


def _write_bookend_manifest(
    output_dir: Path,
    run: ConversationAnchorRun,
    *,
    raw_master_rel: str,
    omni_master_rel: str,
    segment_anchors: dict[int, str],
    segment_masks: dict[int, str],
) -> None:
    mp = manifest_path(output_dir, run)
    mp.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "anchor_mode": "bookend",
        "first_segment_index": run.first_segment_index,
        "segment_indices": list(run.segment_indices),
        "bookend_segment_indices": list(bookend_segment_indices(run)),
        "middle_segment_indices": list(middle_segment_indices(run)),
        "composite_id": run.composite_id,
        "left_speaker_id": run.left_speaker_id,
        "right_speaker_id": run.right_speaker_id,
        "shared_setting": run.shared_setting,
        "master_anchor": raw_master_rel,
        "omnihuman_master": omni_master_rel,
        "segment_anchors": {str(k): v for k, v in sorted(segment_anchors.items())},
        "segment_masks": {str(k): v for k, v in sorted(segment_masks.items())},
    }
    mp.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _bookend_run_complete(
    output_dir: Path,
    run: ConversationAnchorRun,
    narr: dict[str, Any],
) -> bool:
    from video_vendors.fal import FalVendor

    if not omnihuman_shorts_master_path(output_dir, run).is_file():
        return False
    for si in bookend_segment_indices(run):
        cid = _speaker_for_segment_in_run(output_dir, run, si, narr=narr)
        if not cid:
            return False
        if resolve_omnihuman_mask_for_segment(output_dir, si, cid) is None:
            return False
    for si in middle_segment_indices(run):
        cid = _speaker_for_segment_in_run(output_dir, run, si, narr=narr)
        if not cid:
            return False
        if FalVendor._existing_scene_anchor_path(output_dir, si, cid) is None:
            return False
    return True


def derive_bookend_anchors_from_master(
    *,
    repo_root: Path,
    output_dir: Path,
    run: ConversationAnchorRun,
    narr: dict[str, Any],
    master_path: Path,
    aspect_ratio: str = "9:16",
) -> dict[int, Path]:
    """
    Rebuild omni bookends, middle split crops, and masks from an on-disk master (no FAL).
    """
    from video_vendors.fal_scene_anchor_i2i import _safe_anchor_character_file_stem

    if not master_path.is_file():
        raise FileNotFoundError(f"Missing conversation master: {master_path}")

    raw_bytes = master_path.read_bytes()
    omni_path = omnihuman_shorts_master_path(output_dir, run)
    omni_bytes, transform = reframe_master_for_omnihuman(raw_bytes, aspect_ratio=aspect_ratio)
    omni_path.parent.mkdir(parents=True, exist_ok=True)
    omni_path.write_bytes(omni_bytes)

    split = split_master_to_speaker_images(
        raw_bytes,
        left_speaker_id=run.left_speaker_id,
        right_speaker_id=run.right_speaker_id,
        aspect_ratio=aspect_ratio,
        repo_root=repo_root,
    )

    from PIL import Image

    raw_img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    faces = _estimate_speaker_face_centers(
        raw_img,
        repo_root=repo_root,
        left_speaker_id=run.left_speaker_id,
        right_speaker_id=run.right_speaker_id,
    )
    if not faces:
        top_y = int(transform.dst_h * conversation_anchor_face_y_fraction())
        faces = {
            run.left_speaker_id.lower(): (transform.dst_w // 4, top_y),
            run.right_speaker_id.lower(): (3 * transform.dst_w // 4, top_y),
        }
    mapped_faces: dict[str, tuple[int, int]] = {}
    for sid, (fx, fy) in faces.items():
        mapped_faces[sid.lower()] = transform.map_point(float(fx), float(fy))

    anchors_dir = output_dir / "anchors"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    derived: dict[int, Path] = {}
    rel_anchors: dict[int, str] = {}
    rel_masks: dict[int, str] = {}

    for si in run.segment_indices:
        cid = _speaker_for_segment_in_run(output_dir, run, si, narr=narr)
        if not cid:
            continue
        if conversation_segment_is_bookend(run, si):
            face = mapped_faces.get(cid.lower())
            if face is None:
                face = (
                    transform.dst_w // 2,
                    int(transform.dst_h * conversation_anchor_face_y_fraction()),
                )
            mask_path = omnihuman_speaker_mask_path(output_dir, si, cid)
            mask_path.write_bytes(
                build_speaker_mask_png(transform.dst_w, transform.dst_h, face[0], face[1])
            )
            derived[si] = omni_path
            try:
                rel_masks[si] = str(mask_path.relative_to(repo_root.resolve())).replace("\\", "/")
            except ValueError:
                rel_masks[si] = mask_path.name
            continue

        body = split.get(cid.lower())
        if not body:
            continue
        stem = _safe_anchor_character_file_stem(cid)
        dest = anchors_dir / f"{si:02d}_{stem}.png"
        dest.write_bytes(body)
        derived[si] = dest
        try:
            rel_anchors[si] = str(dest.relative_to(repo_root.resolve())).replace("\\", "/")
        except ValueError:
            rel_anchors[si] = dest.name

    for si in middle_segment_indices(run):
        cid = _speaker_for_segment_in_run(output_dir, run, si, narr=narr)
        if not cid:
            continue
        stale_mask = omnihuman_speaker_mask_path(output_dir, si, cid)
        if stale_mask.is_file():
            stale_mask.unlink()

    raw_rel = ""
    try:
        raw_rel = str(master_path.relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        raw_rel = master_path.name
    omni_rel = ""
    try:
        omni_rel = str(omni_path.relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        omni_rel = omni_path.name
    _write_bookend_manifest(
        output_dir,
        run,
        raw_master_rel=raw_rel,
        omni_master_rel=omni_rel,
        segment_anchors=rel_anchors,
        segment_masks=rel_masks,
    )
    return derived


def ensure_conversation_run_bookend_anchors(
    *,
    repo_root: Path,
    output_dir: Path,
    run: ConversationAnchorRun,
    narr: dict[str, Any],
    prompts: list[str],
    openings: list[str | None],
    core_overrides: list[str | None],
    aspect_ratio: str,
    date_id: str = "",
    skip_existing: bool = True,
    force: bool = False,
) -> dict[int, Path]:
    """
    One wide master i2i → omni two-shot for bookends + split crops for middle turns.

    Returns segment index -> anchor path (omni master for bookends, solo crop for middle).
    """
    from pipeline.conversation_scene_anchor import _run_conversation_master_scene_anchor_i2i
    from video_vendors.fal import FalVendor

    output_dir.mkdir(parents=True, exist_ok=True)
    omni_path = omnihuman_shorts_master_path(output_dir, run)
    derived: dict[int, Path] = {}

    if skip_existing and not force and _bookend_run_complete(output_dir, run, narr):
        for si in run.segment_indices:
            if conversation_segment_is_bookend(run, si):
                derived[si] = omni_path
            else:
                cid = _speaker_for_segment_in_run(output_dir, run, si, narr=narr)
                if cid:
                    p = FalVendor._existing_scene_anchor_path(output_dir, si, cid)
                    if p is not None:
                        derived[si] = p
        return derived

    raw_master = _run_conversation_master_scene_anchor_i2i(
        repo_root=repo_root,
        output_dir=output_dir,
        run=run,
        narr=narr,
        openings=openings,
        core_overrides=core_overrides,
        aspect_ratio=aspect_ratio,
        date_id=date_id,
        force_composite_portrait=force,
    )
    return derive_bookend_anchors_from_master(
        repo_root=repo_root,
        output_dir=output_dir,
        run=run,
        narr=narr,
        master_path=raw_master,
        aspect_ratio=aspect_ratio,
    )


def existing_bookend_anchor_for_segment(
    output_dir: Path,
    segment_index: int,
    speaker_id: str,
) -> Path | None:
    """On-disk anchor for a bookend-strategy turn (omni master or middle split crop)."""
    from video_vendors.fal import FalVendor

    cid = _strip(speaker_id).lower()
    if not cid:
        return None
    anchors = output_dir / "anchors"
    if not anchors.is_dir():
        return None
    for mp in sorted(anchors.glob("_conv_*_manifest.json")):
        try:
            data = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("anchor_mode") != "bookend":
            continue
        seg_list = data.get("segment_indices") or []
        if segment_index not in seg_list and str(segment_index) not in seg_list:
            continue
        bookends = data.get("bookend_segment_indices") or []
        bookend_set = {int(x) for x in bookends}
        if segment_index in bookend_set:
            if resolve_omnihuman_mask_for_segment(output_dir, segment_index, cid) is None:
                return None
            first = data.get("first_segment_index")
            comp = _strip(data.get("composite_id"))
            if first is not None and comp:
                omni = anchors / f"_conv_{int(first):02d}_{comp}_omni.png"
                if omni.is_file():
                    return omni
        else:
            p = FalVendor._existing_scene_anchor_path(output_dir, segment_index, cid)
            if p is not None:
                return p
    return None
