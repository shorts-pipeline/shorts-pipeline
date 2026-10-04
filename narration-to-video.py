#!/usr/bin/env python3
"""Generate AI video clips from narration JSON. Vendor is configurable: sora, google, fal."""

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import argparse
import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

import ffmpeg

from pipeline.automation_gates import emit_gate_result, preflight_narration_to_video
from pipeline.conversation_scene_anchor import resolve_talking_head_scene_anchor
from pipeline.fal_minimax_tts import fal_tts_credentials_available, load_fal_custom_voice_ids
from pipeline.narration_common import load_narration_config
from pipeline.narration_utils import load_narration, narration_script_row_1based
from pipeline.narration_visual_mode import (
    VISUAL_MODE_B_ROLL,
    VISUAL_MODE_TALKING_HEAD,
    visual_modes_for_narration_script,
)
from pipeline.run_report import ClipReportRow, RunReportDocument, write_run_report
from pipeline.segment_plan import build_episode_segment_plans
from pipeline.talking_head_prompt_merge import talking_head_prompt_missing_warning
from pipeline.tts_speaker_voice import apply_talking_head_voice_downgrades_to_modes
from video_vendors import (
    build_prompts,
    get_vendor,
    load_fal_negative_extras,
    load_fal_scene_anchor_i2i_meta,
)
from video_vendors.fal import FalVendor, _sanitize_fal_prompt
from video_vendors.fal_avatar import generate_talking_head_clip

# Cap requested clip duration so we don't blow up cost; assembly still stretches to match audio.
MAX_VIDEO_DURATION_SECONDS = 10.0  # legacy default / Wan B-roll cap

# Parallel clip API jobs for google/fal; on failure, retry serially (concurrency 1).
DEFAULT_CLIP_CONCURRENCY = 10


def _load_segment_durations(
    date_id: str,
    n_segments: int,
    max_seconds: float = MAX_VIDEO_DURATION_SECONDS,
) -> list[float] | None:
    """Load segment durations from audio/{date_id}/durations.json, skip intro, cap at max_seconds.
    Returns list of floats (one per segment) or None if missing/mismatch."""
    durations_path = Path("audio") / date_id / "durations.json"
    if not durations_path.exists():
        return None
    try:
        data = json.loads(durations_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, list) or not data:
        return None
    # Skip intro entry if present
    start = 1 if (data[0].get("file") == "intro") else 0
    segment_entries = data[start:]
    if len(segment_entries) != n_segments:
        return None
    out = []
    for entry in segment_entries:
        d = entry.get("duration")
        if d is None or not isinstance(d, (int, float)):
            return None
        out.append(min(float(d), max_seconds))
    return out


def _broll_clip_max_seconds(
    vendor: str,
    cfg: dict,
    *,
    fal_broll_engine: str | None = None,
    fal_broll_i2v_engine: str | None = None,
    fal_broll_t2v_engine: str | None = None,
) -> float:
    """Max generated clip length passed to the video vendor from TTS durations.json."""
    from video_vendors.kling import (
        KLING_BROLL_ENGINE,
        kling_broll_duration_limits,
        resolve_broll_route_engines,
    )

    try:
        wan_max = float(cfg.get("fal_wan_broll_max_seconds") or MAX_VIDEO_DURATION_SECONDS)
    except (TypeError, ValueError):
        wan_max = MAX_VIDEO_DURATION_SECONDS
    if vendor != "fal":
        return wan_max
    i2v_engine, t2v_engine = resolve_broll_route_engines(
        cfg,
        fal_broll_engine=fal_broll_engine,
        fal_broll_i2v_engine=fal_broll_i2v_engine,
        fal_broll_t2v_engine=fal_broll_t2v_engine,
    )
    caps = [wan_max]
    if i2v_engine == KLING_BROLL_ENGINE or t2v_engine == KLING_BROLL_ENGINE:
        _lo, hi = kling_broll_duration_limits(cfg)
        caps.append(hi)
    return max(caps)


# Vendors that make paid API calls; require explicit confirmation
PAID_API_VENDORS = ("google", "fal")


def _validate_segment_counts(date_id: str) -> None:
    """Verify narration, audio segments, and durations.json have matching counts. Exit on mismatch."""
    narration_path = Path("narrations") / f"narration{date_id}.json"
    durations_path = Path("audio") / date_id / "durations.json"
    if not narration_path.exists():
        return  # No narration yet; skip validation
    data = json.loads(narration_path.read_text(encoding="utf-8"))
    n_narration = len(data.get("narration_script", []))
    if not durations_path.exists():
        return  # No audio yet; skip
    durations = json.loads(durations_path.read_text(encoding="utf-8"))
    # First entry may be map intro (file="intro"); exclude from narration segment count
    n_intro = 1 if durations and durations[0].get("file") == "intro" else 0
    n_durations = len(durations) - n_intro
    segments_dir = Path("audio") / date_id / "segments"
    # Count only 01.mp3, 02.mp3, ... (ignore stray files like 00.mp3 or leftovers from segment count changes)
    expected = {f"{i:02d}.mp3" for i in range(1, n_narration + 1)}
    n_audio = (
        sum(1 for p in segments_dir.iterdir() if p.is_file() and p.name in expected)
        if segments_dir.exists()
        else 0
    )
    if n_narration != n_durations or n_durations != n_audio:
        print(
            f"[ERROR] Segment count mismatch: narration={n_narration}, durations={n_durations}, audio={n_audio}. "
            "Re-run narration-to-mp3 for this date_id (or run-daily without stale audio). "
            "Usually the narration JSON was regenerated with a different number of segments than existing audio/.",
            file=sys.stderr,
        )
        sys.exit(1)


def _count_new_clips(
    prompts: list,
    output_dir: Path,
    vendor: str,
    date_id: str = "",
    segment_indices: set[int] | None = None,
    broll_output_suffix: str | None = None,
) -> tuple[int, list[int]]:
    """Return (count of clips that would be generated, list of 1-based segment indices)."""
    indices = segment_indices or set(range(1, len(prompts) + 1))
    suffix = (broll_output_suffix or "").strip()

    def _clip_path(idx: int) -> Path:
        if suffix:
            return output_dir / f"{idx:02d}_{suffix}.mp4"
        return output_dir / f"{idx:02d}.mp4"

    if vendor == "sora":
        out_path = output_dir / f"video_{date_id}.mp4"
        if out_path.exists():
            return 0, []
        return 1, [1]
    # google, fal: per-segment clips
    new_indices = [
        idx for idx in indices if 1 <= idx <= len(prompts) and not _clip_path(idx).exists()
    ]
    return len(new_indices), new_indices


def _visual_modes_for_prompts(narr_data: dict | None, n_prompts: int) -> list[str]:
    if not narr_data or n_prompts <= 0:
        return [VISUAL_MODE_B_ROLL] * max(1, n_prompts)
    modes = visual_modes_for_narration_script(narr_data)
    if len(modes) != n_prompts:
        return [VISUAL_MODE_B_ROLL] * n_prompts
    return modes


def _fal_wan_segment_indices(
    modes: list[str],
    segment_indices: set[int] | None,
    n_prompts: int,
) -> set[int]:
    """Indices that use Wan (exclude talking_head)."""
    req = segment_indices or set(range(1, n_prompts + 1))
    return {i for i in req if 1 <= i <= n_prompts and modes[i - 1] != VISUAL_MODE_TALKING_HEAD}


def _fal_talking_head_indices(
    modes: list[str],
    segment_indices: set[int] | None,
    n_prompts: int,
) -> set[int]:
    req = segment_indices or set(range(1, n_prompts + 1))
    return {i for i in req if 1 <= i <= n_prompts and modes[i - 1] == VISUAL_MODE_TALKING_HEAD}


def _talking_head_opening_frames_for_i2i(
    row: dict,
    *,
    segment_index: int,
    subject_id: str,
    fal_openings: list[str | None],
    world_prefix: str,
) -> list[str | None]:
    """
    Ensure scene-anchor i2i has an opening beat for this segment.

    Older narrations may omit ``opening_frame`` on talking_head rows; without it the pipeline
    skipped i2i and SadTalker used the transparent portrait (white/black void).
    """
    slots: list[str | None] = list(fal_openings) if fal_openings else []
    while len(slots) < segment_index:
        slots.append(None)
    text = ""
    prior = slots[segment_index - 1]
    if isinstance(prior, str):
        text = prior.strip()
    if not text:
        text = (row.get("opening_frame") or "").strip()
    if not text:
        thp = (row.get("talking_head_prompt") or "").strip()
        if thp:
            m = re.match(r"^([^.!?]+[.!?]?)", thp)
            text = (m.group(1) if m else thp[:240]).strip()
    if not text:
        loc = (world_prefix or "").strip() or "Missouri River expedition bank, soft natural light"
        text = (
            f"Head-and-shoulders period portrait, {subject_id}, relaxed neutral mouth before speech, "
            f"steady eyes; {loc}, earth tones—single t=0 frozen instant only, not a plain white void."
        )
    slots[segment_index - 1] = text
    return slots


def _require_talking_head_scene_anchor_source(
    segment_index: int,
    character_id: str,
    *,
    portrait_path: Path | None,
    source_image_url: str | None,
    source_image_path: Path | None,
) -> None:
    """Fail the run if a portrait-backed talking_head segment has no scene-anchor still."""
    cid = (character_id or "").strip().lower()
    if not cid:
        raise RuntimeError(
            f"Segment {segment_index}: talking_head missing character id for scene anchor"
        )
    if portrait_path is None:
        raise RuntimeError(
            f"Segment {segment_index}: no portrait for {cid!r} under character-portraits/ "
            f"(scene anchor required for talking_head)"
        )
    has_anchor = bool((source_image_url or "").strip()) or (
        source_image_path is not None and source_image_path.is_file()
    )
    if not has_anchor:
        raise RuntimeError(
            f"Segment {segment_index}: scene-anchor i2i did not produce an image for {cid!r} "
            f"(refusing raw-portrait fallback). Delete {segment_index:02d}.mp4 and regenerate, "
            f"or run scene-anchor i2i from the Pipeline UI."
        )


def _generate_fal_talking_head_clips(
    *,
    date_id: str,
    output_dir: Path,
    modes: list[str],
    segment_indices: set[int] | None,
    repo_root: Path,
    prompts: list[str],
    fal_openings: list[str | None],
    fal_world_segments: list[str],
    fal_core_overrides: list[str | None],
    aspect_ratio: str,
    reuse_scene_anchor_stills: bool,
    run_report: RunReportDocument | None = None,
    talking_head_model_override: str | None = None,
    talking_head_output_suffix: str | None = None,
) -> list[Path]:
    """Generate silent talking-head MP4s for segments marked talking_head (fal only)."""
    narr = load_narration(date_id)
    if not narr:
        return []
    episode = build_episode_segment_plans(narr, date_id=date_id, repo_root=repo_root)
    cfg = load_narration_config()
    model_id = str(
        talking_head_model_override or cfg.get("fal_talking_head_model") or "fal-ai/sadtalker"
    ).strip()
    fb_model = str(cfg.get("fal_talking_head_fallback_model") or "").strip() or None
    sadtalker_still_mode = bool(cfg.get("fal_sadtalker_still_mode", True))
    output_suffix = (talking_head_output_suffix or "").strip()
    th_idx = episode.talking_head_indices(segment_indices)
    out_paths: list[Path] = []
    audio_root = Path("audio") / date_id / "segments"
    force_regen = segment_indices is not None and not output_suffix
    spike_regen = bool(output_suffix)
    for idx in sorted(th_idx):
        dest = (
            output_dir / f"{idx:02d}_{output_suffix}.mp4"
            if output_suffix
            else output_dir / f"{idx:02d}.mp4"
        )
        if dest.exists():
            if spike_regen:
                try:
                    dest.unlink()
                    print(
                        f"   Talking-head segment {idx}: removed {dest.name} for spike regen",
                        file=sys.stderr,
                    )
                except OSError as e:
                    print(
                        f"   [WARN] Could not remove {dest.name} for spike regen: {e}",
                        file=sys.stderr,
                    )
            elif force_regen and idx in (segment_indices or set()):
                try:
                    dest.unlink()
                    print(
                        f"   Talking-head segment {idx}: removed {dest.name} for regen",
                        file=sys.stderr,
                    )
                except OSError as e:
                    print(
                        f"   [WARN] Could not remove {dest.name} for regen: {e}",
                        file=sys.stderr,
                    )
            else:
                print(
                    f"   Talking-head segment {idx}: reuse — {dest.name} already exists "
                    f"(delete to regenerate)",
                    file=sys.stderr,
                )
                if run_report is not None:
                    run_report.talking_head_rows.append(
                        ClipReportRow(
                            segment=idx,
                            kind="talking_head",
                            action="reused",
                            detail=dest.name,
                            model=model_id,
                            reason_code="file_exists",
                        )
                    )
                continue
        row = narration_script_row_1based(narr, idx)
        if not row:
            raise RuntimeError(f"No narration_script row for segment {idx}")
        subj = (row.get("talking_head_subject") or "").strip().lower()
        if not subj:
            raise RuntimeError(f"Segment {idx}: talking_head missing talking_head_subject")
        talking_head_prompt = (row.get("talking_head_prompt") or "").strip() or None
        th_warn = talking_head_prompt_missing_warning(idx, model_id)
        if th_warn:
            print(f"   [WARN] {th_warn}", file=sys.stderr)
        ref_char = (row.get("reference_character_id") or "").strip().lower() or None
        full_segment_mp3 = audio_root / f"{idx:02d}.mp3"
        drive_mp3 = audio_root / f"{idx:02d}_talking_head_drive.mp3"
        audio_mp3 = full_segment_mp3
        pad_lead = 0.0
        if drive_mp3.is_file() and full_segment_mp3.is_file():
            try:
                full_dur = float(ffmpeg.probe(str(full_segment_mp3))["format"]["duration"])
                drive_dur = float(ffmpeg.probe(str(drive_mp3))["format"]["duration"])
                pad_lead = max(0.0, round(full_dur - drive_dur, 3))
                audio_mp3 = drive_mp3
                if pad_lead > 0.02:
                    print(
                        f"   [INFO] Talking-head: FAL audio={drive_mp3.name}; "
                        f"clone-pad {pad_lead:.2f}s to match {full_segment_mp3.name}",
                        file=sys.stderr,
                    )
            except Exception as e:
                print(
                    f"[WARN] segment {idx}: talking-head drive probe failed ({e}); "
                    f"using full segment audio.",
                    file=sys.stderr,
                )
                audio_mp3 = full_segment_mp3
                pad_lead = 0.0
        ref_note = f", ref={ref_char}" if ref_char else ""
        print(f"fal.ai talking-head segment {idx}/{len(episode)} (subject={subj}{ref_note})...")

        source_image_url: str | None = None
        source_image_path: Path | None = None
        source_mask_path: Path | None = None

        prompt_raw = prompts[idx - 1] if 1 <= idx <= len(prompts) else ""
        rest, cid_marker, _ = FalVendor._parse_markers(prompt_raw)
        sanitized = _sanitize_fal_prompt(rest, aggressive=False)
        anchor_char = (cid_marker or ref_char or subj or "").strip().lower()

        world_prefix = fal_world_segments[idx - 1] if idx - 1 < len(fal_world_segments) else ""
        portrait_path = (
            FalVendor._portrait_path_for_character(repo_root, anchor_char) if anchor_char else None
        )
        if portrait_path is not None:
            openings_for_i2i = _talking_head_opening_frames_for_i2i(
                row,
                segment_index=idx,
                subject_id=anchor_char,
                fal_openings=fal_openings,
                world_prefix=world_prefix,
            )
            if source_image_path is None and source_image_url is None:
                resolved = resolve_talking_head_scene_anchor(
                    repo_root=repo_root,
                    output_dir=output_dir,
                    segment_index=idx,
                    anchor_char=anchor_char,
                    narr=narr,
                    prompts=prompts,
                    openings=openings_for_i2i,
                    core_overrides=fal_core_overrides,
                    world_prefix=world_prefix,
                    aspect_ratio=aspect_ratio,
                    reuse_scene_anchor_stills=reuse_scene_anchor_stills,
                    sanitized_prompt=sanitized,
                    date_id=date_id,
                )
                if resolved.image_url:
                    source_image_url = resolved.image_url
                elif resolved.image_path is not None and resolved.image_path.is_file():
                    source_image_path = resolved.image_path
                if resolved.mask_path is not None and resolved.mask_path.is_file():
                    source_mask_path = resolved.mask_path
        _require_talking_head_scene_anchor_source(
            idx,
            anchor_char,
            portrait_path=portrait_path,
            source_image_url=source_image_url,
            source_image_path=source_image_path,
        )

        generate_talking_head_clip(
            repo_root=repo_root,
            segment_index=idx,
            audio_mp3=audio_mp3,
            talking_head_subject=subj,
            talking_head_prompt=talking_head_prompt,
            reference_character_id=ref_char,
            output_mp4=dest,
            model_id=model_id,
            source_image_url=source_image_url,
            source_image_path=source_image_path,
            source_mask_path=source_mask_path,
            pad_lead_seconds=pad_lead,
            aspect_ratio=aspect_ratio,
            fallback_model_id=fb_model,
            sadtalker_still_mode=sadtalker_still_mode,
            require_scene_anchor=True,
        )
        out_paths.append(dest)
        if run_report is not None:
            run_report.talking_head_rows.append(
                ClipReportRow(
                    segment=idx,
                    kind="talking_head",
                    action="generated",
                    detail=dest.name,
                    model=model_id,
                    reason_code=None,
                )
            )
    return out_paths


def main():
    parser = argparse.ArgumentParser(
        description="Generate AI video from narration JSON. "
        "Supports sora (Playwright), google (Veo), fal (Wan 2.5)."
    )
    parser.add_argument("date", help="Date identifier, e.g. 18030830")
    parser.add_argument(
        "--vendor",
        choices=["sora", "google", "fal"],
        default="sora",
        help="Video vendor: sora (ChatGPT web), google (Veo API), fal (Wan B-roll + optional FAL talking-head)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: movie-images/<date> for google/fal, output/ for sora)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making any API calls. No video generation.",
    )
    parser.add_argument(
        "--segments",
        type=str,
        default=None,
        help="Comma-separated segment indices to generate (e.g. 3,7). Default: all. Use to regenerate only failed clips.",
    )
    parser.add_argument(
        "--reuse-scene-anchor-stills",
        action="store_true",
        help="FAL only: kept for CLI compatibility. Wan always uses movie-images/<date>/anchors/NN_* "
        "when present (image-to-video); segments without an anchor use text-to-video.",
    )
    parser.add_argument(
        "--shorts",
        action="store_true",
        default=True,
        help="Generate 9:16 vertical clips for YouTube Shorts (google/fal). Default: on.",
    )
    parser.add_argument(
        "--wide-screen",
        action="store_false",
        dest="shorts",
        help="Generate 16:9 landscape clips (google/fal).",
    )
    # Sora-specific
    parser.add_argument(
        "--profile",
        default="sora-profile",
        help="Chrome profile dir for Sora (sora vendor only)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (sora vendor only)",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip preflight checks (segment counts, FAL talking-head portraits).",
    )
    parser.add_argument(
        "--strict-preflight",
        action="store_true",
        help="Treat preflight warnings (e.g. segment count drift on partial runs) as errors.",
    )
    parser.add_argument(
        "--talking-head-model",
        default=None,
        help="FAL only: override fal_talking_head_model for this run "
        "(e.g. fal-ai/heygen/avatar4/image-to-video or fal-ai/bytedance/omnihuman/v1.5).",
    )
    parser.add_argument(
        "--talking-head-output-suffix",
        default=None,
        help="FAL only: write talking-head clips as NN_<suffix>.mp4 instead of NN.mp4 (spike/A-B; keeps originals).",
    )
    parser.add_argument(
        "--fal-broll-engine",
        choices=["wan", "kling-v3-standard"],
        default=None,
        help="FAL only: legacy override for both i2v and t2v when per-route flags unset.",
    )
    parser.add_argument(
        "--fal-broll-i2v-engine",
        choices=["wan", "kling-v3-standard"],
        default=None,
        help="FAL only: B-roll image-to-video engine (default fal_broll_i2v_engine or fal_broll_engine).",
    )
    parser.add_argument(
        "--fal-broll-t2v-engine",
        choices=["wan", "kling-v3-standard"],
        default=None,
        help="FAL only: B-roll text-to-video engine (default fal_broll_t2v_engine or fal_broll_engine).",
    )
    parser.add_argument(
        "--broll-output-suffix",
        default=None,
        help="FAL only: write B-roll clips as NN_<suffix>.mp4 instead of NN.mp4 (spike/A-B; keeps originals).",
    )
    args = parser.parse_args()

    date_id = args.date
    prompts = build_prompts(date_id, vendor=args.vendor)
    repo_root = Path(__file__).resolve().parent
    narration_cfg = load_narration_config(repo_root / "config" / "narration_config.json")

    narr_data = load_narration(date_id)
    episode = (
        build_episode_segment_plans(narr_data, date_id=date_id, repo_root=repo_root)
        if narr_data
        else None
    )
    if episode and len(episode) == len(prompts):
        modes = episode.visual_modes()
    else:
        modes = _visual_modes_for_prompts(narr_data, len(prompts))
    if narr_data and prompts and len(modes) == len(prompts):
        cfg_vp = narration_cfg
        vbs = cfg_vp.get("voice_by_speaker") or {}
        if not isinstance(vbs, dict):
            vbs = {}
        fal_ids = load_fal_custom_voice_ids(
            repo_root / "character-portraits" / "voice_prompts.json"
        )
        narrator_ov = "nova" if bool(narr_data.get("focus_topic")) else "onyx"
        modes = apply_talking_head_voice_downgrades_to_modes(
            narr_data,
            modes,
            fal_voice_ids=fal_ids,
            voice_by_speaker=vbs,
            narrator_openai_voice=narrator_ov,
            use_openai_tts=True,
            fal_credentials_ok=fal_tts_credentials_available(),
        )
    if args.vendor != "fal" and any(m == VISUAL_MODE_TALKING_HEAD for m in modes):
        print(
            "[WARN] visual_mode talking_head is only implemented for vendor fal; "
            "using B-roll generation for those segments.",
            file=sys.stderr,
        )
        modes = [VISUAL_MODE_B_ROLL if m == VISUAL_MODE_TALKING_HEAD else m for m in modes]

    fal_openings: list[str | None] | None = None
    fal_world_segments: list[str] = []
    if args.vendor == "fal":
        fal_openings, fal_world_segments, fal_core_overrides = load_fal_scene_anchor_i2i_meta(
            date_id
        )

    segment_indices: set[int] | None = None
    if args.segments:
        try:
            segment_indices = {int(x.strip()) for x in args.segments.split(",") if x.strip()}
        except ValueError:
            print("[ERROR] --segments must be comma-separated integers (e.g. 3,7)", file=sys.stderr)
            sys.exit(1)
        if args.vendor == "sora":
            print(
                "[ERROR] --segments is not supported for Sora (produces one combined video)",
                file=sys.stderr,
            )
            sys.exit(1)

    if args.output_dir is None:
        args.output_dir = (
            Path("output") if args.vendor == "sora" else Path("movie-images") / date_id
        )

    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Validation: fail fast if segment counts mismatch (google/fal only)
    if args.vendor in ("google", "fal") and not args.dry_run:
        if args.no_preflight:
            _validate_segment_counts(date_id)
        else:
            pf = preflight_narration_to_video(
                repo_root=repo_root,
                date_id=date_id,
                vendor=args.vendor,
                segment_indices=segment_indices,
                strict=args.strict_preflight,
            )
            if emit_gate_result(pf, strict=args.strict_preflight):
                sys.exit(1)

    new_count, new_indices = _count_new_clips(
        prompts,
        args.output_dir,
        args.vendor,
        date_id,
        segment_indices,
        broll_output_suffix=args.broll_output_suffix,
    )

    # ---- DRY RUN: never touch APIs ----
    if args.dry_run:
        print("[DRY-RUN] No API calls will be made.")
        print(f"  Date: {date_id}")
        print(f"  Vendor: {args.vendor}")
        print(f"  Prompts: {len(prompts)}")
        print(f"  Output: {args.output_dir}")
        if segment_indices:
            print(f"  --segments: {sorted(segment_indices)}")
        print(
            f"  New clips to generate: {new_count}"
            + (f" (segments {new_indices})" if new_indices else " (none; all exist)")
        )
        if args.vendor in ("google", "fal"):
            scope = segment_indices if segment_indices else set(range(1, len(prompts) + 1))
            existing_sorted = sorted(
                i for i in scope if (args.output_dir / f"{i:02d}.mp4").exists()
            )
            missing_sorted = sorted(
                i for i in scope if not (args.output_dir / f"{i:02d}.mp4").exists()
            )
            print(
                f"  Clips in scope: existing {existing_sorted or 'none'}; missing {missing_sorted or 'none'}"
            )
            print(
                f"  Concurrency: {DEFAULT_CLIP_CONCURRENCY} parallel clip jobs "
                f"(retries with 1 if that fails)"
            )
        if args.vendor in PAID_API_VENDORS and new_count > 0:
            print(
                f"  [CAUTION] {args.vendor} uses paid API (~${new_count * 5:.0f}-{new_count * 10:.0f} est. for {new_count} clips)"
            )
        if args.vendor == "fal" and fal_openings:
            n_of = sum(1 for x in fal_openings if x)
            if n_of:
                print(f"  FAL i2i: {n_of} segment(s) with per-segment opening_frame")
        if args.vendor == "fal":
            narr_dry = narr_data or {}
            dry_episode = build_episode_segment_plans(
                narr_dry,
                date_id=date_id,
                repo_root=Path.cwd(),
            )
            dry_scope = dry_episode.in_scope(segment_indices)
            n_anchors = 0
            for plan in dry_scope:
                if not plan.scene_anchor_eligible:
                    continue
                anchor_char = plan.talking_head_subject or plan.reference_character_id
                existing = FalVendor._existing_scene_anchor_path(
                    args.output_dir, plan.index, anchor_char
                )
                if existing is None:
                    existing = FalVendor._existing_segment_anchor_path(args.output_dir, plan.index)
                if existing is not None:
                    n_anchors += 1
            if n_anchors:
                print(
                    f"  FAL: {n_anchors} segment(s) with on-disk anchors -> image-to-video; "
                    "others -> text-to-video"
                )
            n_broll_no_char = sum(
                1 for plan in dry_scope if plan.is_b_roll and not plan.scene_anchor_eligible
            )
            if n_broll_no_char:
                print(
                    f"  FAL: {n_broll_no_char} B-roll segment(s) without portrait-backed "
                    "characters use text-to-video (others may use anchors/ stills).",
                    file=sys.stderr,
                )
        if args.vendor == "fal" and args.reuse_scene_anchor_stills:
            print("  FAL: --reuse-scene-anchor-stills (anchors are always used when present)")
        if args.vendor == "fal" and any(m == VISUAL_MODE_TALKING_HEAD for m in modes):
            if episode is not None:
                wan_scope = sorted(episode.wan_indices(segment_indices))
                th_scope = sorted(episode.talking_head_indices(segment_indices))
            else:
                wan_scope = sorted(_fal_wan_segment_indices(modes, segment_indices, len(prompts)))
                th_scope = sorted(_fal_talking_head_indices(modes, segment_indices, len(prompts)))
            print(
                f"  FAL visual_mode: Wan segment indices {wan_scope or '[]'}; talking_head {th_scope or '[]'}"
            )
        return

    vendor = get_vendor(
        args.vendor,
        profile_dir=args.profile,
        headless=args.headless,
    )

    wan_only = (
        episode.wan_indices(segment_indices)
        if args.vendor == "fal" and episode is not None
        else _fal_wan_segment_indices(modes, segment_indices, len(prompts))
        if args.vendor == "fal"
        else (segment_indices or set(range(1, len(prompts) + 1)))
    )
    kwargs = {"segment_indices": wan_only if args.vendor == "fal" else segment_indices}
    kwargs["aspect_ratio"] = "9:16" if args.shorts else "16:9"
    # Pass audio-based durations so clips match narration length (engine-specific cap).
    if args.vendor in ("google", "fal"):
        broll_cap = _broll_clip_max_seconds(
            args.vendor,
            narration_cfg,
            fal_broll_engine=args.fal_broll_engine,
            fal_broll_i2v_engine=args.fal_broll_i2v_engine,
            fal_broll_t2v_engine=args.fal_broll_t2v_engine,
        )
        target_durations = _load_segment_durations(date_id, len(prompts), max_seconds=broll_cap)
        if target_durations is not None:
            kwargs["target_durations"] = target_durations
    if args.vendor == "fal" and fal_openings is not None:
        kwargs["opening_frames"] = fal_openings
        kwargs["world_prefix_for_i2i_segments"] = fal_world_segments
        kwargs["core_location_overrides"] = fal_core_overrides
    if args.vendor == "fal":
        kwargs["negative_extras"] = load_fal_negative_extras(date_id)
    if args.vendor == "fal" and args.reuse_scene_anchor_stills:
        kwargs["reuse_scene_anchor_stills"] = True
    if args.vendor == "fal":
        kwargs["visual_modes"] = modes
    if args.vendor == "fal" and args.fal_broll_engine:
        kwargs["broll_engine"] = args.fal_broll_engine
    if args.vendor == "fal" and args.fal_broll_i2v_engine:
        kwargs["broll_i2v_engine"] = args.fal_broll_i2v_engine
    if args.vendor == "fal" and args.fal_broll_t2v_engine:
        kwargs["broll_t2v_engine"] = args.fal_broll_t2v_engine
    if args.vendor == "fal" and args.broll_output_suffix:
        kwargs["broll_output_suffix"] = args.broll_output_suffix

    cfg_vp = narration_cfg
    from video_vendors.kling import KLING_BROLL_ENGINE, resolve_broll_route_engines

    i2v_engine, t2v_engine = resolve_broll_route_engines(
        cfg_vp,
        fal_broll_engine=args.fal_broll_engine,
        fal_broll_i2v_engine=args.fal_broll_i2v_engine,
        fal_broll_t2v_engine=args.fal_broll_t2v_engine,
    )
    kling_i2v_model = str(cfg_vp.get("fal_kling_broll_i2v_model") or "").strip()
    if i2v_engine == KLING_BROLL_ENGINE and t2v_engine == KLING_BROLL_ENGINE:
        broll_run_model = str(
            cfg_vp.get("fal_kling_broll_t2v_model")
            or "fal-ai/kling-video/v3/standard/text-to-video"
        )
    elif i2v_engine == KLING_BROLL_ENGINE and t2v_engine != KLING_BROLL_ENGINE:
        broll_run_model = f"i2v:{kling_i2v_model or 'kling-v3-standard'};t2v:fal_wan"
    elif i2v_engine != KLING_BROLL_ENGINE and t2v_engine == KLING_BROLL_ENGINE:
        broll_run_model = (
            f"i2v:fal_wan;t2v:{cfg_vp.get('fal_kling_broll_t2v_model') or 'kling-v3-standard'}"
        )
    else:
        broll_run_model = "fal_wan"

    run_report: RunReportDocument | None = (
        RunReportDocument(date_id=date_id, vendor="fal") if args.vendor == "fal" else None
    )

    fal_th_kwargs = {
        "date_id": date_id,
        "output_dir": args.output_dir,
        "modes": modes,
        "segment_indices": segment_indices,
        "repo_root": repo_root,
        "prompts": prompts,
        "fal_openings": fal_openings if fal_openings is not None else [],
        "fal_world_segments": fal_world_segments,
        "fal_core_overrides": fal_core_overrides,
        "aspect_ratio": kwargs["aspect_ratio"],
        "reuse_scene_anchor_stills": args.reuse_scene_anchor_stills,
        "run_report": run_report,
        "talking_head_model_override": args.talking_head_model,
        "talking_head_output_suffix": args.talking_head_output_suffix,
    }

    def _generate_with_concurrency(concurrency: int):
        kw = dict(kwargs)
        kw["concurrency"] = concurrency
        return vendor.generate(
            date_id=date_id,
            prompts=prompts,
            output_dir=args.output_dir,
            **kw,
        )

    if args.vendor in ("google", "fal"):
        try:
            # FAL: --segments may list only talking_head rows → no Wan work; do not call FalVendor
            # with an empty index set (that would scan and relabel every existing b_roll clip as "generated").
            if args.vendor == "fal" and not wan_only and segment_indices is not None:
                paths = []
                print(
                    "[INFO] FAL: --segments includes no b_roll (Wan) clips; skipping Wan generation.",
                    file=sys.stderr,
                )
            else:
                paths = _generate_with_concurrency(DEFAULT_CLIP_CONCURRENCY)
        except Exception as e:
            print(
                f"[ERROR] Video generation failed with concurrency={DEFAULT_CLIP_CONCURRENCY}: {e}",
                file=sys.stderr,
            )
            print("[INFO] Retrying with concurrency=1.", file=sys.stderr)
            try:
                if args.vendor == "fal" and not wan_only and segment_indices is not None:
                    paths = []
                else:
                    paths = _generate_with_concurrency(1)
            except Exception as e2:
                print(f"[ERROR] {e2}", file=sys.stderr)
                sys.exit(1)
        if paths:
            print(f"[OK] Generated {len(paths)} video(s)")
            for p in paths:
                print(f"   {p}")
        if run_report is not None and paths:
            spike_suffix = (args.broll_output_suffix or "").strip()
            for p in paths:
                m = re.match(r"^(\d+)(?:_([^.]+))?\.mp4$", p.name)
                if not m:
                    continue
                seg = int(m.group(1))
                if segment_indices is not None and seg not in segment_indices:
                    continue
                if spike_suffix and m.group(2) != spike_suffix:
                    continue
                run_report.b_roll_rows.append(
                    ClipReportRow(
                        segment=seg,
                        kind="b_roll",
                        action="generated",
                        detail=p.name,
                        model=broll_run_model,
                        reason_code=None,
                    )
                )
        if args.vendor == "fal":
            th_paths = _generate_fal_talking_head_clips(**fal_th_kwargs)
            for p in th_paths:
                print(f"   {p}")
            if run_report is not None:
                try:
                    rp_path = write_run_report(args.output_dir, run_report)
                    print(f"[OK] Run report: {rp_path}", file=sys.stderr)
                except OSError as e:
                    print(f"[WARN] Could not write run_report.json: {e}", file=sys.stderr)
    else:
        try:
            paths = vendor.generate(
                date_id=date_id,
                prompts=prompts,
                output_dir=args.output_dir,
                **kwargs,
            )
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)
        print(f"[OK] Generated {len(paths)} video(s)")
        for p in paths:
            print(f"   {p}")

    try:
        from pipeline.episode_state import refresh_episode_state_sidecar

        refresh_episode_state_sidecar(repo_root, date_id, source="narration-to-video")
    except Exception as e:
        print(f"[WARN] episode state sidecar: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
