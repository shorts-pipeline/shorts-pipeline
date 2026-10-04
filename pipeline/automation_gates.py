"""Preflight/postflight checks for run-daily and similar automation."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline.fal_minimax_tts import fal_tts_credentials_available, load_fal_custom_voice_ids
from pipeline.narration_common import load_narration_config
from pipeline.narration_utils import (
    get_mid_episode_map_insertion,
    narration_json_expects_dialogue_mode,
    narration_json_expects_long_conversation_mode,
)
from pipeline.narration_visual_mode import _is_animal_subject
from pipeline.phase1_speaker_dialogue_rules import _tts_one_speaker_segment_errors
from pipeline.segment_plan import build_episode_segment_plans
from pipeline.tts_post_narration_silence import post_narration_silence_review_warnings
from pipeline.tts_speaker_voice import talking_head_subject_has_dedicated_voice
from video_vendors.fal_avatar import portrait_path_for_talking_head


@dataclass
class GateResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: GateResult) -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


def narration_audio_segment_mismatch(date_id: str, narration_path: Path) -> bool:
    """
    True when narration segment count does not match audio/durations.json (excluding map intro).
    Mirrors run-daily.py logic.
    """
    if not narration_path.exists():
        return False
    try:
        data = json.loads(narration_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    script = data.get("narration_script") or []
    if not isinstance(script, list) or len(script) == 0:
        return False
    n_narr = len(script)
    durations_path = Path("audio") / date_id / "durations.json"
    if not durations_path.exists():
        return True
    try:
        durations = json.loads(durations_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    if not isinstance(durations, list) or not durations:
        return True
    n_intro = 1 if durations[0].get("file") == "intro" else 0
    n_dur = len(durations) - n_intro
    return n_narr != n_dur


def non_empty_open_questions(nar: dict[str, Any]) -> list[str]:
    """Return stripped Phase 2 open_questions strings."""
    oq = nar.get("open_questions")
    if not isinstance(oq, list):
        return []
    return [str(x).strip() for x in oq if isinstance(x, str) and str(x).strip()]


def map_usage_consistency_warnings(nar: dict[str, Any]) -> list[str]:
    """
    Warn when video_metadata.map_usage disagrees with map_insertions / mid-episode helper.
    """
    vm = nar.get("video_metadata")
    if not isinstance(vm, dict):
        vm = {}
    map_usage = str(vm.get("map_usage") or "none").strip().lower()
    insertions = nar.get("map_insertions")
    has_any_insertion = isinstance(insertions, list) and len(insertions) > 0
    has_mid = get_mid_episode_map_insertion(nar) is not None
    warnings: list[str] = []
    if map_usage in ("", "none") and has_any_insertion:
        warnings.append("video_metadata.map_usage is 'none' but map_insertions is non-empty")
    if map_usage in ("mid-episode", "minimal_overlay") and not has_mid:
        warnings.append(
            f"video_metadata.map_usage is {map_usage!r} but no valid mid-episode "
            "parchment_overlay map_insertion"
        )
    if has_mid and map_usage in ("", "none"):
        warnings.append(
            "Valid mid-episode map_insertion present but video_metadata.map_usage is 'none'"
        )
    return warnings


def preflight_narration_review_warnings(
    nar: dict[str, Any],
    *,
    narration_path: Path | None = None,
) -> list[str]:
    """Non-blocking human-review and metadata consistency warnings."""
    prefix = f"{narration_path}: " if narration_path else ""
    warnings: list[str] = []
    oqs = non_empty_open_questions(nar)
    if oqs:
        preview = "; ".join(oqs[:3])
        if len(oqs) > 3:
            preview += f" (+{len(oqs) - 3} more)"
        warnings.append(
            f"{prefix}Phase 2 open_questions ({len(oqs)}): review before publish — {preview}"
        )
    notes = str(nar.get("editor_notes") or "").strip()
    if notes:
        short = notes if len(notes) <= 120 else notes[:117] + "…"
        warnings.append(f"{prefix}editor_notes: {short}")
    for msg in map_usage_consistency_warnings(nar):
        warnings.append(f"{prefix}{msg}" if prefix else msg)
    for msg in post_narration_silence_review_warnings(nar):
        warnings.append(f"{prefix}{msg}" if prefix else msg)
    return warnings


def postflight_estimated_duration_warnings(
    actual_s: float,
    narration_data: dict[str, Any] | None,
    *,
    tolerance_ratio: float = 0.25,
) -> list[str]:
    """Warn when assembled MP4 duration diverges from video_metadata.estimated_duration_seconds."""
    if not narration_data or actual_s <= 0:
        return []
    vm = narration_data.get("video_metadata")
    if not isinstance(vm, dict):
        return []
    raw = vm.get("estimated_duration_seconds")
    try:
        est_f = float(raw)
    except (TypeError, ValueError):
        return []
    if est_f <= 0:
        return []
    delta = abs(actual_s - est_f)
    if delta > est_f * tolerance_ratio:
        pct = int(tolerance_ratio * 100)
        return [
            f"Output duration {actual_s:.1f}s differs from narration "
            f"estimated_duration_seconds {est_f:.0f}s by more than {pct}%"
        ]
    return []


def load_narration_for_gates(narration_path: Path) -> tuple[dict[str, Any] | None, GateResult]:
    """Load narration JSON or return ``(None, GateResult with errors)``."""
    out = GateResult()
    if not narration_path.exists():
        out.errors.append(f"Narration file not found: {narration_path}")
        return None, out
    try:
        nar: dict[str, Any] = json.loads(narration_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        out.errors.append(f"Invalid JSON in {narration_path}: {e}")
        return None, out
    except OSError as e:
        out.errors.append(f"Could not read {narration_path}: {e}")
        return None, out
    script = nar.get("narration_script")
    if script is None:
        out.errors.append(f"{narration_path}: missing narration_script")
        return None, out
    if not isinstance(script, list):
        out.errors.append(f"{narration_path}: narration_script must be a list")
        return None, out
    return nar, out


def validate_tts_one_speaker_policy(
    nar: dict[str, Any],
    *,
    date_id: str = "00000000",
    repo_root: Path | None = None,
    segment_indices: set[int] | None = None,
) -> None:
    """Raise ValueError when segments violate one-speaker TTS layout rules."""
    episode = build_episode_segment_plans(
        nar,
        date_id=date_id,
        repo_root=repo_root,
        resolve_disk_paths=False,
    )
    errors: list[str] = []
    for plan in episode.in_scope(segment_indices):
        if not plan.expects_one_cast_speaker:
            continue
        errors.extend(_tts_one_speaker_segment_errors(plan.index, plan.row))
    if errors:
        raise ValueError("One-speaker-per-segment TTS policy failed:\n- " + "\n- ".join(errors))


def audio_segment_count_mismatch(
    repo_root: Path,
    date_id: str,
    n_narration: int,
) -> tuple[bool, str]:
    """
    True when narration segment count disagrees with durations.json and on-disk segment MP3s.
    Returns (mismatch, detail_message).
    """
    durations_path = repo_root / "audio" / date_id / "durations.json"
    if not durations_path.exists():
        return False, ""
    try:
        durations = json.loads(durations_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True, "could not read durations.json"
    if not isinstance(durations, list) or not durations:
        return True, "durations.json empty or invalid"
    n_intro = 1 if durations[0].get("file") == "intro" else 0
    n_durations = len(durations) - n_intro
    segments_dir = repo_root / "audio" / date_id / "segments"
    expected = {f"{i:02d}.mp3" for i in range(1, n_narration + 1)}
    n_audio = (
        sum(1 for p in segments_dir.iterdir() if p.is_file() and p.name in expected)
        if segments_dir.is_dir()
        else 0
    )
    if n_narration != n_durations or n_durations != n_audio:
        return (
            True,
            f"narration={n_narration}, durations={n_durations}, audio={n_audio}",
        )
    return False, ""


def _preflight_fal_talking_head_segments(
    *,
    repo_root: Path,
    date_id: str,
    nar: dict[str, Any],
    vendor: str,
    check_portraits: bool,
    segment_indices: set[int] | None = None,
) -> GateResult:
    """Portrait / voice checks for ``talking_head`` rows (optionally limited to ``segment_indices``)."""
    out = GateResult()
    episode = build_episode_segment_plans(nar, date_id=date_id, repo_root=repo_root)

    vbs_gate: dict[str, Any] = {}
    fal_ids_gate: dict[str, str] = {}
    narrator_ov_gate = "onyx"
    fal_ok_gate = False
    if check_portraits:
        cfg_gate = load_narration_config(repo_root / "config" / "narration_config.json")
        raw_vbs = cfg_gate.get("voice_by_speaker") or {}
        vbs_gate = raw_vbs if isinstance(raw_vbs, dict) else {}
        fal_ids_gate = load_fal_custom_voice_ids(
            repo_root / "character-portraits" / "voice_prompts.json"
        )
        narrator_ov_gate = "nova" if bool(nar.get("focus_topic")) else "onyx"
        fal_ok_gate = fal_tts_credentials_available()

    for plan in episode.in_scope(segment_indices):
        if not plan.is_talking_head:
            continue

        subj = plan.talking_head_subject
        ref = plan.reference_character_id or None

        if check_portraits:
            if not subj:
                out.errors.append(
                    f"Segment {plan.index}: talking_head requires talking_head_subject"
                )
                continue
            if not plan.has_dialogue:
                out.warnings.append(
                    f"Segment {plan.index}: talking_head has no dialogue lines; "
                    "video/audio will use B-roll + narrator-only."
                )
                continue
            if not talking_head_subject_has_dedicated_voice(
                subj,
                fal_voice_ids=fal_ids_gate,
                voice_by_speaker=vbs_gate,
                narrator_openai_voice=narrator_ov_gate,
                use_openai_tts=True,
                fal_credentials_ok=fal_ok_gate,
            ):
                out.warnings.append(
                    f"Segment {plan.index}: talking_head subject {subj!r} has no dedicated TTS voice; "
                    "video/audio will use B-roll with narrator voice only (no character clone)."
                )
                continue
            if _is_animal_subject(subj):
                out.errors.append(
                    f"Segment {plan.index}: talking_head_subject {subj!r} is not supported for FAL talking-head"
                )
                continue
            try:
                portrait_path_for_talking_head(repo_root, subj, ref)
            except (ValueError, FileNotFoundError) as e:
                out.errors.append(f"Segment {plan.index}: talking_head portrait: {e}")
        elif vendor in ("google", "sora"):
            out.warnings.append(
                f"Segment {plan.index}: visual_mode talking_head — vendor {vendor!r} uses B-roll for "
                "those segments (FAL only implements talking-head video)."
            )
    return out


def preflight_narration_to_mp3(
    *,
    repo_root: Path,
    date_id: str,
    segment_indices: set[int] | None = None,
    strict: bool = False,
) -> GateResult:
    """Checks before ``narration-to-mp3.py`` (including partial ``--segments`` regenerates)."""
    out = GateResult()
    narration_path = repo_root / "narrations" / f"narration{date_id}.json"
    nar, load_result = load_narration_for_gates(narration_path)
    out.merge(load_result)
    if nar is None:
        return out

    try:
        validate_tts_one_speaker_policy(
            nar,
            date_id=date_id,
            repo_root=repo_root,
            segment_indices=segment_indices,
        )
    except ValueError as e:
        out.errors.append(str(e))

    script = nar.get("narration_script") or []
    segments_dir = repo_root / "audio" / date_id / "segments"
    if segment_indices is not None:
        for idx in range(1, len(script) + 1):
            if idx in segment_indices:
                continue
            mp3 = segments_dir / f"{idx:02d}.mp3"
            if not mp3.is_file():
                out.errors.append(
                    f"Partial TTS: segment {idx} missing {mp3} "
                    f"(--segments requires existing segment files)"
                )

    mismatch = narration_audio_segment_mismatch(date_id, narration_path)
    if mismatch:
        msg = (
            f"Narration segment count differs from {Path('audio') / date_id / 'durations.json'}; "
            "re-run full TTS or regenerate all segments."
        )
        if strict:
            out.errors.append(msg)
        else:
            out.warnings.append(msg)

    return out


def preflight_narration_to_video(
    *,
    repo_root: Path,
    date_id: str,
    vendor: str,
    segment_indices: set[int] | None = None,
    strict: bool = False,
) -> GateResult:
    """Checks before ``narration-to-video.py`` (including partial ``--segments`` runs)."""
    out = GateResult()
    narration_path = repo_root / "narrations" / f"narration{date_id}.json"
    nar, load_result = load_narration_for_gates(narration_path)
    out.merge(load_result)
    if nar is None:
        return out

    script = nar.get("narration_script") or []
    mismatch, detail = audio_segment_count_mismatch(repo_root, date_id, len(script))
    if mismatch:
        msg = (
            f"Segment count mismatch ({detail}). "
            "Re-run narration-to-mp3 for this date_id before video generation."
        )
        if strict or segment_indices is None:
            out.errors.append(msg)
        else:
            out.warnings.append(msg)

    check_portraits = vendor == "fal"
    th_result = _preflight_fal_talking_head_segments(
        repo_root=repo_root,
        date_id=date_id,
        nar=nar,
        vendor=vendor,
        check_portraits=check_portraits,
        segment_indices=segment_indices,
    )
    out.merge(th_result)
    return out


def emit_gate_result(result: GateResult, *, strict: bool = False) -> bool:
    """
    Print gate warnings/errors to stderr. Returns True when the caller should abort.
    """
    for w in result.warnings:
        print(f"[WARN] preflight: {w}", file=sys.stderr)
    for e in result.errors:
        print(f"[ERROR] preflight: {e}", file=sys.stderr)
    if result.errors:
        return True
    if strict and result.warnings:
        for w in result.warnings:
            print(f"[ERROR] strict preflight: {w}", file=sys.stderr)
        return True
    return False


def preflight_run_daily(
    *,
    repo_root: Path,
    date_id: str,
    narration_path: Path,
    audio_final: Path,
    vendor: str,
    narration_only: bool,
    skip_existing: bool,
    dialogue_effective: bool,
    long_conversation_effective: bool = False,
    strict: bool = False,
) -> GateResult:
    """
    Fail-fast checks before a full run-daily execution.

    When ``vendor`` is ``fal`` and the run includes video, ``talking_head`` segments must
    resolve to portrait assets (same rules as FAL talking-head generation).
    """
    out = GateResult()
    if not narration_path.exists():
        return out

    try:
        raw = narration_path.read_text(encoding="utf-8")
        nar: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as e:
        out.errors.append(f"Invalid JSON in {narration_path}: {e}")
        return out
    except OSError as e:
        out.errors.append(f"Could not read {narration_path}: {e}")
        return out

    script = nar.get("narration_script")
    if script is None:
        out.errors.append(f"{narration_path}: missing narration_script")
        return out
    if not isinstance(script, list):
        out.errors.append(f"{narration_path}: narration_script must be a list")
        return out

    for w in preflight_narration_review_warnings(nar, narration_path=narration_path):
        out.warnings.append(w)

    # Mismatch between saved JSON and CLI flags only matters when narration will not be
    # regenerated (--skip-existing). With --no-skip-existing, Phase 1/2 overwrite the file.
    will_replace_narration = not skip_existing
    if narration_json_expects_dialogue_mode(nar) and not dialogue_effective:
        msg = (
            f"{narration_path} uses dialogue mode but dialogue is disabled (--no-dialogue). "
            "Re-run with dialogue inferred or pass --dialogue."
        )
        if will_replace_narration:
            out.warnings.append(
                f"{narration_path} currently uses dialogue mode; this run will regenerate without dialogue."
            )
        else:
            out.errors.append(msg)
    if narration_json_expects_long_conversation_mode(nar) and not long_conversation_effective:
        msg = (
            f"{narration_path} uses long-conversation mode but long-conversation is disabled "
            "(--no-long-conversation). Re-run with flags inferred from the narration file or "
            "pass --long-conversation."
        )
        if will_replace_narration:
            out.warnings.append(
                f"{narration_path} currently uses long-conversation mode; this run will regenerate "
                "without long-conversation."
            )
        else:
            out.errors.append(msg)

    stale_audio = (
        skip_existing
        and audio_final.exists()
        and narration_audio_segment_mismatch(date_id, narration_path)
    )
    if stale_audio:
        msg = (
            f"Narration segment count differs from {Path('audio') / date_id / 'durations.json'}; "
            "audio will be re-synced when the pipeline runs."
        )
        if strict and not narration_only:
            out.errors.append(msg)
        else:
            out.warnings.append(msg)

    check_portraits = vendor == "fal" and not narration_only
    th_result = _preflight_fal_talking_head_segments(
        repo_root=repo_root,
        date_id=date_id,
        nar=nar,
        vendor=vendor,
        check_portraits=check_portraits,
        segment_indices=None,
    )
    out.merge(th_result)

    return out


def postflight_output_video(
    path: Path,
    *,
    min_duration_s: float = 0.5,
    narration_data: dict[str, Any] | None = None,
    duration_tolerance_ratio: float = 0.25,
) -> GateResult:
    """Sanity-check assembled or vendor output MP4."""
    out = GateResult()
    if not path.exists():
        out.errors.append(f"Postflight: output file missing: {path}")
        return out
    try:
        size = path.stat().st_size
    except OSError as e:
        out.errors.append(f"Postflight: cannot stat {path}: {e}")
        return out
    if size < 1024:
        out.errors.append(f"Postflight: output suspiciously small ({size} bytes): {path}")

    try:
        import ffmpeg

        info = ffmpeg.probe(str(path))
    except Exception as e:
        out.errors.append(f"Postflight: ffprobe failed for {path}: {e}")
        return out

    try:
        dur = float(info.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    if dur < min_duration_s:
        out.errors.append(
            f"Postflight: duration {dur:.3f}s is below minimum {min_duration_s}s: {path}"
        )
    else:
        for w in postflight_estimated_duration_warnings(
            dur,
            narration_data,
            tolerance_ratio=duration_tolerance_ratio,
        ):
            out.warnings.append(f"Postflight: {w}")

    streams = info.get("streams") or []
    if not any(s.get("codec_type") == "video" for s in streams):
        out.errors.append(f"Postflight: no video stream in {path}")

    return out
