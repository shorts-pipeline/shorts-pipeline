"""OmniHuman v1.5: shared conversation master + per-speaker speech masks (no crop split)."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline.conversation_scene_anchor import (
    ConversationAnchorRun,
    _estimate_speaker_face_centers,
    _strip,
    manifest_path,
)


@dataclass(frozen=True)
class MasterReframe:
    """Map face coordinates from raw master pixels into reframed Shorts canvas."""

    scale: float
    paste_x: int
    paste_y: int
    src_w: int
    src_h: int
    dst_w: int
    dst_h: int

    def map_point(self, x: float, y: float) -> tuple[int, int]:
        mx = int(round(x * self.scale + self.paste_x))
        my = int(round(y * self.scale + self.paste_y))
        mx = max(0, min(mx, self.dst_w - 1))
        my = max(0, min(my, self.dst_h - 1))
        return mx, my


def _target_anchor_size(aspect_ratio: str) -> tuple[int, int]:
    if str(aspect_ratio).strip() == "16:9":
        return 1280, 720
    return 720, 1280


def _mask_config() -> dict[str, Any]:
    try:
        from pipeline.narration_common import load_narration_config

        block = (load_narration_config() or {}).get("fal_conversation_scene_anchor")
        if isinstance(block, dict):
            return block
    except Exception:
        pass
    return {}


def mask_ellipse_width_fraction() -> float:
    raw = _mask_config().get("mask_ellipse_width_fraction", 0.26)
    try:
        frac = float(raw)
        return frac if 0.08 <= frac <= 0.5 else 0.26
    except (TypeError, ValueError):
        return 0.26


def mask_ellipse_height_fraction() -> float:
    raw = _mask_config().get("mask_ellipse_height_fraction", 0.34)
    try:
        frac = float(raw)
        return frac if 0.1 <= frac <= 0.55 else 0.34
    except (TypeError, ValueError):
        return 0.34


def omnihuman_shorts_master_path(output_dir: Path, run: ConversationAnchorRun) -> Path:
    return (
        output_dir / "anchors" / f"_conv_{run.first_segment_index:02d}_{run.composite_id}_omni.png"
    )


def omnihuman_speaker_mask_path(output_dir: Path, segment_index: int, speaker_id: str) -> Path:
    from video_vendors.fal_scene_anchor_i2i import _safe_anchor_character_file_stem

    stem = _safe_anchor_character_file_stem(speaker_id)
    return output_dir / "anchors" / f"{segment_index:02d}_{stem}_mask.png"


def reframe_master_for_omnihuman(
    master_bytes: bytes,
    *,
    aspect_ratio: str = "9:16",
    letterbox_rgb: tuple[int, int, int] = (28, 26, 24),
) -> tuple[bytes, MasterReframe]:
    """
    Fit the wide conversation master into Shorts canvas (contain + letterbox).

    Preserves both speakers; face coordinates from the raw master map through
    ``MasterReframe.map_point``.
    """
    from PIL import Image

    dst_w, dst_h = _target_anchor_size(aspect_ratio)
    img = Image.open(io.BytesIO(master_bytes)).convert("RGB")
    src_w, src_h = img.size
    if src_w == dst_w and src_h == dst_h:
        return master_bytes, MasterReframe(1.0, 0, 0, src_w, src_h, dst_w, dst_h)

    scale = min(dst_w / src_w, dst_h / src_h)
    nw = max(1, int(round(src_w * scale)))
    nh = max(1, int(round(src_h * scale)))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (dst_w, dst_h), letterbox_rgb)
    paste_x = (dst_w - nw) // 2
    paste_y = (dst_h - nh) // 2
    canvas.paste(resized, (paste_x, paste_y))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    transform = MasterReframe(scale, paste_x, paste_y, src_w, src_h, dst_w, dst_h)
    return buf.getvalue(), transform


def build_speaker_mask_png(
    width: int,
    height: int,
    face_x: int,
    face_y: int,
    *,
    width_fraction: float | None = None,
    height_fraction: float | None = None,
) -> bytes:
    """Black image with white ellipse over the active speaker (OmniHuman mask_url)."""
    from PIL import Image, ImageDraw

    wf = width_fraction if width_fraction is not None else mask_ellipse_width_fraction()
    hf = height_fraction if height_fraction is not None else mask_ellipse_height_fraction()
    rx = max(8, int(round(width * wf)))
    ry = max(10, int(round(height * hf)))
    x0 = max(0, face_x - rx)
    y0 = max(0, face_y - ry)
    x1 = min(width - 1, face_x + rx)
    y1 = min(height - 1, face_y + ry)
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((x0, y0, x1, y1), fill=255)
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    return buf.getvalue()


def _write_omnihuman_manifest(
    output_dir: Path,
    run: ConversationAnchorRun,
    *,
    raw_master_rel: str,
    omni_master_rel: str,
    segment_masks: dict[int, str],
) -> None:
    mp = manifest_path(output_dir, run)
    mp.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "anchor_mode": "omnihuman_mask",
        "first_segment_index": run.first_segment_index,
        "segment_indices": list(run.segment_indices),
        "composite_id": run.composite_id,
        "left_speaker_id": run.left_speaker_id,
        "right_speaker_id": run.right_speaker_id,
        "shared_setting": run.shared_setting,
        "master_anchor": raw_master_rel,
        "omnihuman_master": omni_master_rel,
        "segment_masks": {str(k): v for k, v in sorted(segment_masks.items())},
    }
    mp.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _omnihuman_run_complete(
    output_dir: Path,
    run: ConversationAnchorRun,
    narr: dict[str, Any],
) -> bool:
    omni = omnihuman_shorts_master_path(output_dir, run)
    if not omni.is_file():
        return False
    from pipeline.conversation_scene_anchor import _speaker_for_segment_in_run

    for si in run.segment_indices:
        cid = _speaker_for_segment_in_run(output_dir, run, si, narr=narr)
        if not cid:
            return False
        mask_p = omnihuman_speaker_mask_path(output_dir, si, cid)
        if not mask_p.is_file():
            return False
    return True


def ensure_conversation_run_omnihuman_masks(
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
    Build shared master (once), reframe to Shorts size, write per-segment speaker masks.

    Returns map of segment index -> reframed omni master path (same file for every turn).
    """
    from pipeline.conversation_scene_anchor import (
        _run_conversation_master_scene_anchor_i2i,
        _speaker_for_segment_in_run,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    omni_path = omnihuman_shorts_master_path(output_dir, run)
    derived: dict[int, Path] = {}

    if skip_existing and not force and _omnihuman_run_complete(output_dir, run, narr):
        for si in run.segment_indices:
            derived[si] = omni_path
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
    raw_bytes = raw_master.read_bytes()
    omni_bytes, transform = reframe_master_for_omnihuman(raw_bytes, aspect_ratio=aspect_ratio)
    omni_path.parent.mkdir(parents=True, exist_ok=True)
    omni_path.write_bytes(omni_bytes)

    from PIL import Image

    raw_img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    faces = _estimate_speaker_face_centers(
        raw_img,
        repo_root=repo_root,
        left_speaker_id=run.left_speaker_id,
        right_speaker_id=run.right_speaker_id,
    )
    if not faces:
        top_y = int(transform.dst_h * 0.22)
        faces = {
            run.left_speaker_id.lower(): (transform.dst_w // 4, top_y),
            run.right_speaker_id.lower(): (3 * transform.dst_w // 4, top_y),
        }
    mapped_faces: dict[str, tuple[int, int]] = {}
    for sid, (fx, fy) in faces.items():
        mapped_faces[sid.lower()] = transform.map_point(float(fx), float(fy))

    anchors_dir = output_dir / "anchors"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    rel_masks: dict[int, str] = {}
    for si in run.segment_indices:
        cid = _speaker_for_segment_in_run(output_dir, run, si, narr=narr)
        if not cid:
            continue
        face = mapped_faces.get(cid.lower())
        if face is None:
            face = (transform.dst_w // 2, int(transform.dst_h * 0.22))
        mask_bytes = build_speaker_mask_png(
            transform.dst_w,
            transform.dst_h,
            face[0],
            face[1],
        )
        mask_path = omnihuman_speaker_mask_path(output_dir, si, cid)
        mask_path.write_bytes(mask_bytes)
        derived[si] = omni_path
        try:
            rel_masks[si] = str(mask_path.relative_to(repo_root.resolve())).replace("\\", "/")
        except ValueError:
            rel_masks[si] = mask_path.name

    raw_rel = ""
    try:
        raw_rel = str(raw_master.relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        raw_rel = raw_master.name
    omni_rel = ""
    try:
        omni_rel = str(omni_path.relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        omni_rel = omni_path.name
    _write_omnihuman_manifest(
        output_dir,
        run,
        raw_master_rel=raw_rel,
        omni_master_rel=omni_rel,
        segment_masks=rel_masks,
    )
    return derived


def derive_omnihuman_masks_from_master(
    *,
    repo_root: Path,
    output_dir: Path,
    run: ConversationAnchorRun,
    narr: dict[str, Any],
    master_path: Path,
    aspect_ratio: str = "9:16",
) -> dict[int, Path]:
    """Rebuild omni master and per-turn masks from an on-disk master (no FAL)."""
    from pipeline.conversation_scene_anchor import (
        _estimate_speaker_face_centers,
        _speaker_for_segment_in_run,
        conversation_anchor_face_y_fraction,
    )

    if not master_path.is_file():
        raise FileNotFoundError(f"Missing conversation master: {master_path}")

    raw_bytes = master_path.read_bytes()
    omni_path = omnihuman_shorts_master_path(output_dir, run)
    omni_bytes, transform = reframe_master_for_omnihuman(raw_bytes, aspect_ratio=aspect_ratio)
    omni_path.parent.mkdir(parents=True, exist_ok=True)
    omni_path.write_bytes(omni_bytes)

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
    derived: dict[int, Path] = {si: omni_path for si in run.segment_indices}
    rel_masks: dict[int, str] = {}
    for si in run.segment_indices:
        cid = _speaker_for_segment_in_run(output_dir, run, si, narr=narr)
        if not cid:
            continue
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
        try:
            rel_masks[si] = str(mask_path.relative_to(repo_root.resolve())).replace("\\", "/")
        except ValueError:
            rel_masks[si] = mask_path.name

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
    _write_omnihuman_manifest(
        output_dir,
        run,
        raw_master_rel=raw_rel,
        omni_master_rel=omni_rel,
        segment_masks=rel_masks,
    )
    return derived


def resolve_omnihuman_mask_for_segment(
    output_dir: Path,
    segment_index: int,
    speaker_id: str,
) -> Path | None:
    """Return on-disk speaker mask for a conversation turn, if present."""
    p = omnihuman_speaker_mask_path(output_dir, segment_index, speaker_id)
    return p if p.is_file() else None


def existing_omnihuman_anchor_for_segment(
    output_dir: Path,
    segment_index: int,
    speaker_id: str,
) -> Path | None:
    """Return reframed omni master path when this turn's speaker mask exists."""
    if resolve_omnihuman_mask_for_segment(output_dir, segment_index, speaker_id) is None:
        return None
    anchors = output_dir / "anchors"
    if not anchors.is_dir():
        return None
    for mp in sorted(anchors.glob("_conv_*_manifest.json")):
        try:
            data = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("anchor_mode") != "omnihuman_mask":
            continue
        seg_masks = data.get("segment_masks") or {}
        if str(segment_index) not in seg_masks:
            continue
        first = data.get("first_segment_index")
        comp = _strip(data.get("composite_id"))
        if first is not None and comp:
            omni = anchors / f"_conv_{int(first):02d}_{comp}_omni.png"
            if omni.is_file():
                return omni
    for p in sorted(anchors.glob("_conv_*_omni.png")):
        if p.is_file():
            return p
    return None
