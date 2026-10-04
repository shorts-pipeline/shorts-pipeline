"""
Cheap anchor-still + TTS preview before OmniHuman / full FAL video.

Builds scene anchors under ``movie-images/<date_id>/anchors/`` (shared with
``narration-to-video --reuse-scene-anchor-stills``), renders per-segment clips under
``anchor_preview/``, and assembles ``output/lewis_clark_anchor_preview_<date>_video.mp4``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pipeline.automation_gates import narration_audio_segment_mismatch
from pipeline.narration_utils import (
    load_narration,
    sanitize_date_id_for_path,
)
from pipeline.output_naming import (
    ANCHOR_PREVIEW_OUTPUT_PREFIX,
    anchor_preview_video_filename,
)
from pipeline.segment_plan import build_episode_segment_plans

PreviewSource = Literal["anchor", "clip", "slate", "missing_th", "intro_copy"]

ANCHOR_PREVIEW_CLIPS_SUBDIR = "anchor_preview"
REPORT_FILENAME = "anchor_preview_report.json"


@dataclass
class SceneAnchorEligibleSegment:
    segment_index: int
    character_id: str
    anchor_exists: bool
    anchor_method: str = "t2i"  # "i2i" when portrait-backed; else Wan text-to-image


@dataclass
class PreviewClipPlan:
    segment_index: int
    visual_mode: str
    source: PreviewSource
    reason: str
    anchor_path: Path | None = None
    segment_clip_path: Path | None = None
    duration_sec: float = 0.0


@dataclass
class AnchorBuildRow:
    segment_index: int
    character_id: str
    action: str  # generated | skipped_existing | failed
    message: str = ""
    relative_path: str = ""


@dataclass
class AnchorPreviewReport:
    date_id: str
    eligible_count: int = 0
    anchor_build: list[AnchorBuildRow] = field(default_factory=list)
    clip_plans: list[dict[str, Any]] = field(default_factory=list)
    preview_video_rel: str = ""
    talking_head_missing_anchor: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date_id": self.date_id,
            "eligible_count": self.eligible_count,
            "anchor_build": [
                {
                    "segment_index": r.segment_index,
                    "character_id": r.character_id,
                    "action": r.action,
                    "message": r.message,
                    "relative_path": r.relative_path,
                }
                for r in self.anchor_build
            ],
            "clip_plans": self.clip_plans,
            "preview_video_rel": self.preview_video_rel,
            "talking_head_missing_anchor": self.talking_head_missing_anchor,
        }


def _repo_root(repo_root: Path | None) -> Path:
    return (repo_root or Path.cwd()).resolve()


def movie_images_dir(repo_root: Path, date_id: str) -> Path:
    did = sanitize_date_id_for_path(date_id) or date_id
    return _repo_root(repo_root) / "movie-images" / did


def anchor_preview_clips_dir(repo_root: Path, date_id: str) -> Path:
    return movie_images_dir(repo_root, date_id) / ANCHOR_PREVIEW_CLIPS_SUBDIR


def report_path(repo_root: Path, date_id: str) -> Path:
    return movie_images_dir(repo_root, date_id) / REPORT_FILENAME


def preview_output_path(repo_root: Path, date_id: str) -> Path:
    did = sanitize_date_id_for_path(date_id) or date_id
    name = anchor_preview_video_filename(ANCHOR_PREVIEW_OUTPUT_PREFIX, did)
    return _repo_root(repo_root) / "output" / name


def _existing_scene_anchor_path(
    movie_dir: Path, segment_index: int, character_id: str = ""
) -> Path | None:
    from video_vendors.fal import FalVendor

    if character_id:
        return FalVendor._existing_scene_anchor_path(movie_dir, segment_index, character_id)
    return FalVendor._existing_segment_anchor_path(movie_dir, segment_index)


def _plan_anchor_character_id(plan, marker_character_id: str) -> str:
    cid = (marker_character_id or "").strip()
    if cid:
        return cid
    return (plan.reference_character_id or plan.talking_head_subject or "").strip()


def _segment_anchor_method(repo_root: Path, character_id: str) -> str:
    from video_vendors.fal import FalVendor

    cid = (character_id or "").strip()
    if cid and FalVendor._portrait_path_for_character(repo_root, cid) is not None:
        return "i2i"
    return "t2i"


def scene_anchor_eligible_segments(
    date_id: str,
    *,
    repo_root: Path | None = None,
    narrations_dir: Path | None = None,
) -> list[SceneAnchorEligibleSegment]:
    """Every narration segment that can receive an opening still for Wan i2v."""
    from video_vendors import build_prompts
    from video_vendors.fal import FalVendor

    root = _repo_root(repo_root)
    did = sanitize_date_id_for_path(date_id) or date_id
    narr_dir = narrations_dir or root / "narrations"
    narr = load_narration(did, narrations_dir=narr_dir) or {}

    prompts = build_prompts(did, narrations_dir=narr_dir, vendor="fal")
    episode = build_episode_segment_plans(narr, date_id=did, repo_root=root)
    out: list[SceneAnchorEligibleSegment] = []
    for plan in episode.scene_anchor_eligible():
        raw = prompts[plan.index - 1] if 0 < plan.index <= len(prompts) else ""
        _rest, marker_cid, _scene_anchor = FalVendor._parse_markers(raw)
        cid = _plan_anchor_character_id(plan, marker_cid)
        out.append(
            SceneAnchorEligibleSegment(
                segment_index=plan.index,
                character_id=cid,
                anchor_exists=plan.anchor_exists,
                anchor_method=_segment_anchor_method(root, cid),
            )
        )
    return out


def plan_preview_clip(
    *,
    repo_root: Path,
    date_id: str,
    segment_index: int,
    duration_sec: float,
    narr: dict[str, Any] | None = None,
) -> PreviewClipPlan:
    """Choose anchor still, existing NN.mp4, or slate for one preview segment."""
    root = _repo_root(repo_root)
    did = sanitize_date_id_for_path(date_id) or date_id
    if narr is None:
        narr = load_narration(did, narrations_dir=root / "narrations") or {}
    episode = build_episode_segment_plans(narr, date_id=did, repo_root=root)
    plan_seg = episode.get(segment_index)
    if plan_seg is None:
        return PreviewClipPlan(
            segment_index=segment_index,
            visual_mode="b_roll",
            source="slate",
            reason="unknown_segment",
            duration_sec=duration_sec,
        )

    if plan_seg.anchor_exists and plan_seg.anchor_path is not None:
        return PreviewClipPlan(
            segment_index=segment_index,
            visual_mode=plan_seg.visual_mode,
            source="anchor",
            reason="scene_anchor_still",
            anchor_path=plan_seg.anchor_path,
            duration_sec=duration_sec,
        )
    if plan_seg.clip_mp4_exists:
        return PreviewClipPlan(
            segment_index=segment_index,
            visual_mode=plan_seg.visual_mode,
            source="clip",
            reason="existing_segment_mp4",
            segment_clip_path=plan_seg.clip_mp4,
            duration_sec=duration_sec,
        )
    if plan_seg.is_talking_head:
        return PreviewClipPlan(
            segment_index=segment_index,
            visual_mode=plan_seg.visual_mode,
            source="missing_th",
            reason="talking_head_requires_anchor",
            duration_sec=duration_sec,
        )
    reason = "not_eligible" if not plan_seg.scene_anchor_eligible else "no_anchor"
    return PreviewClipPlan(
        segment_index=segment_index,
        visual_mode=plan_seg.visual_mode,
        source="slate",
        reason=reason,
        duration_sec=duration_sec,
    )


def _rel_under_repo(repo_root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def check_tts_prerequisites(repo_root: Path, date_id: str) -> tuple[bool, str]:
    """Return (ok, error_message)."""
    root = _repo_root(repo_root)
    did = sanitize_date_id_for_path(date_id) or date_id
    narr_path = root / "narrations" / f"narration{did}.json"
    if not narr_path.is_file():
        return False, f"Missing {narr_path.relative_to(root)}"
    durations_path = root / "audio" / did / "durations.json"
    final_audio = root / "audio" / did / "final.mp3"
    if not durations_path.is_file():
        return False, f"Missing {durations_path.relative_to(root)} (run TTS first)"
    if not final_audio.is_file():
        return False, f"Missing {final_audio.relative_to(root)} (run TTS first)"
    if narration_audio_segment_mismatch(did, narr_path):
        return False, "Narration segment count does not match audio/durations.json"
    return True, ""


def build_scene_anchors_batch(
    date_id: str,
    *,
    repo_root: Path | None = None,
    skip_existing: bool = True,
    force: bool = False,
    segment_indices: set[int] | None = None,
    aspect_ratio: str = "9:16",
) -> AnchorPreviewReport:
    """Run scene-anchor i2i for each eligible segment (sequential)."""
    from video_vendors import (
        build_prompts,
        load_fal_scene_anchor_i2i_meta,
        opening_period_line_for_narration_segment,
    )
    from video_vendors.fal import FalVendor, _sanitize_fal_prompt
    from video_vendors.fal_scene_anchor_i2i import run_segment_anchor_to_disk

    root = _repo_root(repo_root)
    did = sanitize_date_id_for_path(date_id) or date_id
    report = AnchorPreviewReport(date_id=did)
    eligible = scene_anchor_eligible_segments(did, repo_root=root)
    report.eligible_count = len(eligible)
    if not eligible:
        _write_report(root, report)
        return report

    narr_dir = root / "narrations"
    narr_path = narr_dir / f"narration{did}.json"
    narr_data = json.loads(narr_path.read_text(encoding="utf-8-sig"))
    prompts = build_prompts(did, narrations_dir=narr_dir, vendor="fal")
    openings, _worlds, core_overrides = load_fal_scene_anchor_i2i_meta(did, narrations_dir=narr_dir)
    out_dir = movie_images_dir(root, did)
    out_dir.mkdir(parents=True, exist_ok=True)
    episode = build_episode_segment_plans(narr_data, date_id=did, repo_root=root)

    from pipeline.conversation_scene_anchor import (
        conversation_uses_shared_conversation_master,
        conversation_uses_shared_master_bookend,
        conversation_uses_shared_master_mask,
        discover_conversation_anchor_runs,
        ensure_conversation_run_anchors,
        segment_to_conversation_run,
    )

    conv_runs = (
        discover_conversation_anchor_runs(narr_data)
        if conversation_uses_shared_conversation_master()
        else []
    )
    conv_seg_map = segment_to_conversation_run(conv_runs)
    conv_runs_done: set[int] = set()

    for run in conv_runs:
        if run.first_segment_index in conv_runs_done:
            continue
        need_any = False
        for si in run.segment_indices:
            if segment_indices is not None and si not in segment_indices:
                continue
            seg_eligible = next((e for e in eligible if e.segment_index == si), None)
            if seg_eligible is None:
                continue
            if force or not seg_eligible.anchor_exists:
                need_any = True
                break
        if not need_any and skip_existing and not force:
            for si in run.segment_indices:
                seg_eligible = next((e for e in eligible if e.segment_index == si), None)
                if seg_eligible is None:
                    continue
                if skip_existing and seg_eligible.anchor_exists:
                    report.anchor_build.append(
                        AnchorBuildRow(
                            segment_index=si,
                            character_id=seg_eligible.character_id,
                            action="skipped_existing",
                            message="Conversation anchor already on disk",
                            relative_path=_rel_under_repo(
                                root,
                                _existing_scene_anchor_path(out_dir, si, seg_eligible.character_id),
                            ),
                        )
                    )
            conv_runs_done.add(run.first_segment_index)
            continue
        try:
            if conversation_uses_shared_master_bookend():
                from pipeline.conversation_bookend_anchor import (
                    ensure_conversation_run_bookend_anchors,
                )

                derived = ensure_conversation_run_bookend_anchors(
                    repo_root=root,
                    output_dir=out_dir,
                    run=run,
                    narr=narr_data,
                    prompts=prompts,
                    openings=openings,
                    core_overrides=core_overrides,
                    aspect_ratio=aspect_ratio,
                    date_id=did,
                    skip_existing=skip_existing and not force,
                    force=force,
                )
                build_msg = "conversation_master_bookend"
            elif conversation_uses_shared_master_mask():
                from pipeline.conversation_omnihuman_mask import (
                    ensure_conversation_run_omnihuman_masks,
                )

                derived = ensure_conversation_run_omnihuman_masks(
                    repo_root=root,
                    output_dir=out_dir,
                    run=run,
                    narr=narr_data,
                    prompts=prompts,
                    openings=openings,
                    core_overrides=core_overrides,
                    aspect_ratio=aspect_ratio,
                    date_id=did,
                    skip_existing=skip_existing and not force,
                    force=force,
                )
                build_msg = "conversation_master_mask"
            else:
                derived = ensure_conversation_run_anchors(
                    repo_root=root,
                    output_dir=out_dir,
                    run=run,
                    narr=narr_data,
                    prompts=prompts,
                    openings=openings,
                    core_overrides=core_overrides,
                    aspect_ratio=aspect_ratio,
                    date_id=did,
                    skip_existing=skip_existing and not force,
                    force=force,
                )
                build_msg = "conversation_master_split"
            for si in run.segment_indices:
                if segment_indices is not None and si not in segment_indices:
                    continue
                seg_eligible = next((e for e in eligible if e.segment_index == si), None)
                if seg_eligible is None:
                    continue
                path = derived.get(si)
                if path is not None and path.is_file():
                    report.anchor_build.append(
                        AnchorBuildRow(
                            segment_index=si,
                            character_id=seg_eligible.character_id,
                            action="generated",
                            message=build_msg,
                            relative_path=_rel_under_repo(root, path),
                        )
                    )
                else:
                    report.anchor_build.append(
                        AnchorBuildRow(
                            segment_index=si,
                            character_id=seg_eligible.character_id,
                            action="failed",
                            message="conversation anchor derive missing",
                        )
                    )
        except Exception as e:
            for si in run.segment_indices:
                seg_eligible = next((e for e in eligible if e.segment_index == si), None)
                if seg_eligible is None:
                    continue
                if segment_indices is not None and si not in segment_indices:
                    continue
                report.anchor_build.append(
                    AnchorBuildRow(
                        segment_index=si,
                        character_id=seg_eligible.character_id,
                        action="failed",
                        message=str(e)[:500],
                    )
                )
        conv_runs_done.add(run.first_segment_index)

    for seg in eligible:
        si = seg.segment_index
        if segment_indices is not None and si not in segment_indices:
            continue
        if si in conv_seg_map:
            continue
        cid = seg.character_id
        if skip_existing and not force and seg.anchor_exists:
            report.anchor_build.append(
                AnchorBuildRow(
                    segment_index=si,
                    character_id=cid,
                    action="skipped_existing",
                    message="Anchor already on disk",
                    relative_path=_rel_under_repo(
                        root, _existing_scene_anchor_path(out_dir, si, cid)
                    ),
                )
            )
            continue

        raw = prompts[si - 1] if 0 < si <= len(prompts) else ""
        rest, marker_cid, _scene_anchor = FalVendor._parse_markers(raw)
        plan_row = episode.get(si)
        if plan_row is not None:
            cid = _plan_anchor_character_id(plan_row, marker_cid)
        else:
            cid = (marker_cid or "").strip()

        sanitized = _sanitize_fal_prompt(rest, aggressive=False)
        world = opening_period_line_for_narration_segment(narr_data, si, date_id=did)
        clo = core_overrides[si - 1] if 0 < si <= len(core_overrides) else None
        try:
            _url, path = run_segment_anchor_to_disk(
                repo_root=root,
                segment_index=si,
                prompt_without_markers_sanitized=sanitized,
                character_id=cid or None,
                output_dir=out_dir,
                opening_frames=openings,
                world_prefix_for_i2i=world,
                core_location_override=clo,
                aspect_ratio=aspect_ratio,
                aggressive=False,
            )
            if path is None or not path.is_file():
                report.anchor_build.append(
                    AnchorBuildRow(
                        segment_index=si,
                        character_id=cid,
                        action="failed",
                        message="fal returned no saved image",
                    )
                )
            else:
                report.anchor_build.append(
                    AnchorBuildRow(
                        segment_index=si,
                        character_id=cid,
                        action="generated",
                        relative_path=_rel_under_repo(root, path),
                    )
                )
        except Exception as e:
            report.anchor_build.append(
                AnchorBuildRow(
                    segment_index=si,
                    character_id=cid,
                    action="failed",
                    message=str(e)[:500],
                )
            )

    _write_report(root, report)
    return report


def _write_report(repo_root: Path, report: AnchorPreviewReport) -> None:
    p = report_path(repo_root, report.date_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")


def _render_still_clip(
    *,
    image_path: Path,
    duration_sec: float,
    output_mp4: Path,
    target_w: int,
    target_h: int,
    target_fps: int = 24,
) -> None:
    import ffmpeg

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.1, float(duration_sec))
    stream = (
        ffmpeg.input(str(image_path), loop=1, t=dur, framerate=target_fps)
        .filter("scale", target_w, target_h, force_original_aspect_ratio="decrease")
        .filter("pad", target_w, target_h, "(ow-iw)/2", "(oh-ih)/2")
        .filter("format", pix_fmts="yuv420p")
    )
    (
        ffmpeg.output(
            stream,
            str(output_mp4),
            vcodec="libx264",
            pix_fmt="yuv420p",
            r=target_fps,
            an=None,
        )
        .overwrite_output()
        .run(quiet=True)
    )


def _system_font_path() -> Path | None:
    """Return a TrueType font path for ffmpeg drawtext (Windows often crashes without this)."""
    candidates: list[Path] = []
    if sys.platform == "win32":
        windir = Path(os.environ.get("WINDIR", "C:/Windows"))
        candidates.extend(
            windir / "Fonts" / name
            for name in ("arial.ttf", "segoeui.ttf", "calibri.ttf", "tahoma.ttf")
        )
    elif sys.platform == "darwin":
        candidates.extend(
            Path(p)
            for p in (
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/Library/Fonts/Arial.ttf",
            )
        )
    else:
        candidates.extend(
            Path(p)
            for p in (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            )
        )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _drawtext_fontfile_filter_opt() -> str:
    fp = _system_font_path()
    if not fp:
        return ""
    escaped = str(fp.resolve()).replace("\\", "/").replace(":", "\\:")
    return f":fontfile={escaped}"


def _escape_drawtext_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _render_slate_clip_plain(
    *,
    output_mp4: Path,
    duration_sec: float,
    target_w: int,
    target_h: int,
    target_fps: int = 24,
    bg_color: str = "0x1a1a1a",
) -> None:
    import ffmpeg

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.1, float(duration_sec))
    stream = ffmpeg.input(
        f"color=c={bg_color}:s={target_w}x{target_h}:r={target_fps}:d={dur}",
        f="lavfi",
    ).filter("format", pix_fmts="yuv420p")
    (
        ffmpeg.output(
            stream,
            str(output_mp4),
            vcodec="libx264",
            pix_fmt="yuv420p",
            r=target_fps,
            t=dur,
            an=None,
        )
        .overwrite_output()
        .run(quiet=True)
    )


def _render_slate_clip(
    *,
    output_mp4: Path,
    duration_sec: float,
    label_lines: list[str],
    target_w: int,
    target_h: int,
    target_fps: int = 24,
    bg_color: str = "0x1a1a1a",
) -> None:
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.1, float(duration_sec))
    safe_lines = [_escape_drawtext_text(ln) for ln in label_lines if ln]
    if not safe_lines:
        safe_lines = ["Segment"]
    font_opt = _drawtext_fontfile_filter_opt()
    y_start = max(40, target_h // 2 - 20 * len(safe_lines))
    filters = []
    for i, line in enumerate(safe_lines):
        y = y_start + i * 36
        filters.append(
            f"drawtext=text='{line}'{font_opt}:fontsize=28:fontcolor=white:x=(w-text_w)/2:y={y}"
        )
    vf = ",".join(filters)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={bg_color}:s={target_w}x{target_h}:r={target_fps}:d={dur}",
        "-vf",
        vf,
        "-t",
        str(dur),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output_mp4),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        return
    # Windows builds often crash drawtext without fontfile; fall back to a plain color slate.
    _render_slate_clip_plain(
        output_mp4=output_mp4,
        duration_sec=dur,
        target_w=target_w,
        target_h=target_h,
        target_fps=target_fps,
        bg_color=bg_color,
    )


def shorten_render_error_message(reason: str, *, max_len: int = 400) -> str:
    """Human-readable clip/assembly error for UI (not full ffmpeg argv)."""
    text = str(reason or "").strip()
    if text.startswith("render_failed:"):
        text = text[len("render_failed:") :].strip()
    m = re.search(r"returned non-zero exit status (\d+)", text)
    if m:
        code = m.group(1)
        if code == "3221225477":
            return (
                "ffmpeg drawtext failed (Windows font/config). "
                "Retry assemble — slates should fall back to plain color."
            )
        return f"ffmpeg failed (exit {code})"
    if "videos-mp3-to-movie failed" in text:
        return text.split("videos-mp3-to-movie failed:", 1)[-1].strip()[:max_len]
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def summarize_anchor_preview_issues(
    report: AnchorPreviewReport | dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract warnings/errors from ``anchor_preview_report.json`` for UI display."""
    if report is None:
        return []
    data = report.to_dict() if isinstance(report, AnchorPreviewReport) else report
    if not isinstance(data, dict):
        return []
    issues: list[dict[str, Any]] = []
    for row in data.get("anchor_build") or []:
        if not isinstance(row, dict):
            continue
        if row.get("action") == "failed":
            issues.append(
                {
                    "kind": "anchor_build",
                    "segment_index": row.get("segment_index"),
                    "message": shorten_render_error_message(
                        str(row.get("message") or "scene-anchor i2i failed")
                    ),
                }
            )
    clip_render_failed: list[int] = []
    for row in data.get("clip_plans") or []:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "")
        seg = row.get("segment_index")
        if reason.startswith("render_failed"):
            clip_render_failed.append(int(seg) if seg is not None else 0)
            issues.append(
                {
                    "kind": "clip_render",
                    "segment_index": seg,
                    "visual_mode": row.get("visual_mode"),
                    "source": row.get("source"),
                    "message": shorten_render_error_message(reason),
                }
            )
        elif row.get("source") == "missing_th":
            issues.append(
                {
                    "kind": "missing_anchor",
                    "segment_index": seg,
                    "visual_mode": row.get("visual_mode"),
                    "message": "Talking-head segment has no scene anchor still",
                }
            )
    for seg in data.get("talking_head_missing_anchor") or []:
        if int(seg) not in clip_render_failed:
            issues.append(
                {
                    "kind": "missing_anchor",
                    "segment_index": int(seg),
                    "message": "Talking-head segment has no scene anchor still",
                }
            )
    if not data.get("preview_video_rel") and clip_render_failed:
        segs = ", ".join(str(s) for s in sorted(set(clip_render_failed)))
        issues.append(
            {
                "kind": "assembly",
                "segment_index": None,
                "message": (
                    f"Preview MP4 not assembled — failed to render clip(s) for segment(s) {segs}"
                ),
            }
        )
    return issues


def _stretch_clip_to_duration(
    *,
    input_mp4: Path,
    duration_sec: float,
    output_mp4: Path,
    target_w: int,
    target_h: int,
    target_fps: int = 24,
) -> None:
    import ffmpeg

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    target_dur = max(0.1, float(duration_sec))
    info = ffmpeg.probe(str(input_mp4))
    orig_dur = float(next(s for s in info["streams"] if s["codec_type"] == "video")["duration"])
    speed = orig_dur / target_dur if target_dur > 0 else 1.0
    stream = (
        ffmpeg.input(str(input_mp4))
        .video.filter("scale", target_w, target_h, force_original_aspect_ratio="decrease")
        .filter("pad", target_w, target_h, "(ow-iw)/2", "(oh-ih)/2")
        .filter("setpts", f"PTS/{speed}")
    )
    (
        ffmpeg.output(
            stream,
            str(output_mp4),
            vcodec="libx264",
            pix_fmt="yuv420p",
            r=target_fps,
            an=None,
        )
        .overwrite_output()
        .run(quiet=True)
    )


def render_anchor_preview_clips(
    date_id: str,
    *,
    repo_root: Path | None = None,
    shorts: bool = True,
) -> AnchorPreviewReport:
    """Build ``anchor_preview/NN.mp4`` from anchors, existing clips, or slates."""
    root = _repo_root(repo_root)
    did = sanitize_date_id_for_path(date_id) or date_id
    ok, err = check_tts_prerequisites(root, did)
    if not ok:
        raise RuntimeError(err)

    durations_path = root / "audio" / did / "durations.json"
    durations = json.loads(durations_path.read_text(encoding="utf-8"))
    narr = load_narration(did, narrations_dir=root / "narrations") or {}

    if shorts:
        target_w, target_h = 720, 1280
    else:
        target_w, target_h = 1280, 720

    clips_dir = anchor_preview_clips_dir(root, did)
    clips_dir.mkdir(parents=True, exist_ok=True)
    movie_dir = movie_images_dir(root, did)

    report = load_report(root, did) or AnchorPreviewReport(date_id=did)
    report.date_id = did
    report.clip_plans = []
    report.talking_head_missing_anchor = []
    report.eligible_count = len(scene_anchor_eligible_segments(did, repo_root=root))

    # Intro: copy from main movie-images if present
    intro_src = movie_dir / "00_intro.mp4"
    intro_dst = clips_dir / "00_intro.mp4"
    n_intro = 1 if durations and durations[0].get("file") == "intro" else 0
    if intro_src.is_file() and n_intro:
        shutil.copy2(intro_src, intro_dst)
        report.clip_plans.append(
            {
                "segment_index": 0,
                "source": "intro_copy",
                "reason": "00_intro.mp4",
                "output_rel": _rel_under_repo(root, intro_dst),
            }
        )

    seg_offset = n_intro
    for j, entry in enumerate(durations):
        if entry.get("file") == "intro":
            continue
        seg_i = j - seg_offset + 1
        dur = float(entry.get("duration") or 0.0)
        plan = plan_preview_clip(
            repo_root=root,
            date_id=did,
            segment_index=seg_i,
            duration_sec=dur,
            narr=narr,
        )
        out_mp4 = clips_dir / f"{seg_i:02d}.mp4"
        try:
            if plan.source == "anchor" and plan.anchor_path:
                _render_still_clip(
                    image_path=plan.anchor_path,
                    duration_sec=dur,
                    output_mp4=out_mp4,
                    target_w=target_w,
                    target_h=target_h,
                )
            elif plan.source == "clip" and plan.segment_clip_path:
                _stretch_clip_to_duration(
                    input_mp4=plan.segment_clip_path,
                    duration_sec=dur,
                    output_mp4=out_mp4,
                    target_w=target_w,
                    target_h=target_h,
                )
            else:
                bg = "0x4a2020" if plan.source == "missing_th" else "0x1a1a1a"
                lines = [
                    f"Seg {seg_i} — {plan.visual_mode}",
                    plan.reason.replace("_", " "),
                ]
                if plan.source == "missing_th":
                    lines.insert(0, "ANCHOR MISSING")
                _render_slate_clip(
                    output_mp4=out_mp4,
                    duration_sec=dur,
                    label_lines=lines,
                    target_w=target_w,
                    target_h=target_h,
                    bg_color=bg,
                )
        except Exception as e:
            plan_dict = {
                "segment_index": seg_i,
                "visual_mode": plan.visual_mode,
                "source": plan.source,
                "reason": f"render_failed: {shorten_render_error_message(str(e))}",
            }
            report.clip_plans.append(plan_dict)
            continue

        if plan.source == "missing_th":
            report.talking_head_missing_anchor.append(seg_i)

        report.clip_plans.append(
            {
                "segment_index": seg_i,
                "visual_mode": plan.visual_mode,
                "source": plan.source,
                "reason": plan.reason,
                "output_rel": _rel_under_repo(root, out_mp4),
            }
        )

    _write_report(root, report)
    return report


def assemble_anchor_preview_with_missing_anchors(
    date_id: str,
    *,
    repo_root: Path | None = None,
    skip_existing: bool = True,
    force: bool = False,
    shorts: bool = True,
    ambient: bool = False,
    aspect_ratio: str = "9:16",
) -> tuple[AnchorPreviewReport, Path]:
    """Build missing scene anchors, render preview clips, then mux preview MP4."""
    root = _repo_root(repo_root)
    did = sanitize_date_id_for_path(date_id) or date_id
    ok, err = check_tts_prerequisites(root, did)
    if not ok:
        raise RuntimeError(err)

    build_scene_anchors_batch(
        did,
        repo_root=root,
        skip_existing=skip_existing,
        force=force,
        aspect_ratio=aspect_ratio,
    )
    render_anchor_preview_clips(did, repo_root=root, shorts=shorts)
    out = assemble_anchor_preview_video(did, repo_root=root, shorts=shorts, ambient=ambient)
    report = load_report(root, did) or AnchorPreviewReport(date_id=did)
    return report, out


def assemble_anchor_preview_video(
    date_id: str,
    *,
    repo_root: Path | None = None,
    shorts: bool = True,
    ambient: bool = False,
) -> Path:
    """Mux preview clips + ``final.mp3`` via ``videos-mp3-to-movie.py``."""
    root = _repo_root(repo_root)
    did = sanitize_date_id_for_path(date_id) or date_id
    ok, err = check_tts_prerequisites(root, did)
    if not ok:
        raise RuntimeError(err)

    clips_dir = anchor_preview_clips_dir(root, did)
    if not clips_dir.is_dir() or not any(clips_dir.glob("*.mp4")):
        raise RuntimeError(f"No preview clips in {clips_dir.relative_to(root)} — run render first")

    script = root / "videos-mp3-to-movie.py"
    if not script.is_file():
        raise RuntimeError(f"Missing {script}")

    cmd = [
        sys.executable,
        str(script),
        did,
        "--clips-subdir",
        ANCHOR_PREVIEW_CLIPS_SUBDIR,
        "--output-prefix",
        ANCHOR_PREVIEW_OUTPUT_PREFIX,
        "--no-opening-cover",
    ]
    if shorts:
        cmd.append("--shorts")
    else:
        cmd.append("--wide-screen")
    if ambient:
        cmd.append("--ambient")

    proc = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        raise RuntimeError(f"videos-mp3-to-movie failed: {tail}")

    out_path = preview_output_path(root, did)
    report = load_report(root, did) or AnchorPreviewReport(date_id=did)
    report.preview_video_rel = _rel_under_repo(root, out_path)
    _write_report(root, report)
    return out_path


def load_report(repo_root: Path, date_id: str) -> AnchorPreviewReport | None:
    p = report_path(repo_root, date_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    rows = []
    for raw in data.get("anchor_build") or []:
        if isinstance(raw, dict):
            rows.append(
                AnchorBuildRow(
                    segment_index=int(raw.get("segment_index") or 0),
                    character_id=str(raw.get("character_id") or ""),
                    action=str(raw.get("action") or ""),
                    message=str(raw.get("message") or ""),
                    relative_path=str(raw.get("relative_path") or ""),
                )
            )
    return AnchorPreviewReport(
        date_id=str(data.get("date_id") or date_id),
        eligible_count=int(data.get("eligible_count") or 0),
        anchor_build=rows,
        clip_plans=list(data.get("clip_plans") or []),
        preview_video_rel=str(data.get("preview_video_rel") or ""),
        talking_head_missing_anchor=list(data.get("talking_head_missing_anchor") or []),
    )


def status_payload(
    date_id: str,
    *,
    repo_root: Path | None = None,
    journal_date: str = "",
) -> dict[str, Any]:
    """Filesystem snapshot for UI / API."""
    root = _repo_root(repo_root)
    did = sanitize_date_id_for_path(date_id) or date_id
    tts_ok, tts_err = check_tts_prerequisites(root, did)
    eligible = scene_anchor_eligible_segments(did, repo_root=root)
    anchors_on_disk = sum(1 for e in eligible if e.anchor_exists)
    narr = load_narration(did, narrations_dir=root / "narrations") or {}
    clip_plans: list[dict[str, Any]] = []
    th_missing: list[int] = []

    if tts_ok:
        durations_path = root / "audio" / did / "durations.json"
        durations = json.loads(durations_path.read_text(encoding="utf-8"))
        n_intro = 1 if durations and durations[0].get("file") == "intro" else 0
        for j, entry in enumerate(durations):
            if entry.get("file") == "intro":
                continue
            seg_i = j - n_intro + 1
            dur = float(entry.get("duration") or 0.0)
            plan = plan_preview_clip(
                repo_root=root,
                date_id=did,
                segment_index=seg_i,
                duration_sec=dur,
                narr=narr,
            )
            clip_plans.append(
                {
                    "segment_index": seg_i,
                    "visual_mode": plan.visual_mode,
                    "source": plan.source,
                    "reason": plan.reason,
                }
            )
            if plan.source == "missing_th":
                th_missing.append(seg_i)

    preview_mp4 = preview_output_path(root, did)
    clips_dir = anchor_preview_clips_dir(root, did)
    n_preview_clips = len(list(clips_dir.glob("*.mp4"))) if clips_dir.is_dir() else 0
    saved_report = load_report(root, did)
    report_dict = saved_report.to_dict() if saved_report else None
    issues = summarize_anchor_preview_issues(saved_report)

    return {
        "ok": True,
        "date_id": did,
        "journal_date": journal_date,
        "tts_ready": tts_ok,
        "tts_error": tts_err if not tts_ok else "",
        "eligible_count": len(eligible),
        "anchors_on_disk": anchors_on_disk,
        "eligible_segments": [
            {
                "segment_index": e.segment_index,
                "character_id": e.character_id,
                "anchor_exists": e.anchor_exists,
                "anchor_method": e.anchor_method,
            }
            for e in eligible
        ],
        "clip_plans": clip_plans,
        "talking_head_missing_anchor": th_missing,
        "preview_clips_count": n_preview_clips,
        "preview_video_exists": preview_mp4.is_file(),
        "preview_video_rel": _rel_under_repo(root, preview_mp4) if preview_mp4.is_file() else "",
        "preview_video_mtime": preview_mp4.stat().st_mtime if preview_mp4.is_file() else None,
        "report": report_dict,
        "issues": issues,
        "has_warnings": bool(issues),
    }
