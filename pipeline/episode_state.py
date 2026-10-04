"""Per-episode pipeline state sidecar (``narrations/narration<DATE>_state.json``)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.narration_utils import (
    load_narration,
    narration_json_expects_dialogue_mode,
    narration_json_expects_long_conversation_mode,
)
from pipeline.output_naming import date_id_from_output_video_stem
from pipeline.segment_plan import build_episode_segment_plans

EPISODE_STATE_VERSION = "1.0"


def episode_state_sidecar_path(repo_root: Path, date_id: str) -> Path:
    return repo_root / "narrations" / f"narration{date_id}_state.json"


def date_id_to_journal_date(date_id: str) -> str | None:
    if not re.fullmatch(r"\d{8}", (date_id or "").strip()):
        return None
    s = date_id.strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _rel_under(root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _output_video_path(repo_root: Path, date_id: str) -> Path | None:
    out = repo_root / "output"
    if not out.is_dir() or not re.fullmatch(r"\d{8}", date_id):
        return None
    for p in sorted(out.glob(f"*_{date_id}_video.mp4")):
        if p.is_file() and date_id_from_output_video_stem(p.stem) == date_id:
            return p.resolve()
    return None


def _manifest_path_for_video(repo_root: Path, video_path: Path) -> Path | None:
    manifest = video_path.with_suffix(".manifest.json")
    return manifest if manifest.is_file() else None


def build_episode_state(
    repo_root: Path,
    date_id: str,
    *,
    narration: dict[str, Any] | None = None,
    source: str = "scan",
) -> dict[str, Any]:
    """
    Compute pipeline readiness from narration JSON + on-disk artifacts.

    Uses ``SegmentPlan`` for per-segment flags and paths.
    """
    root = repo_root.resolve()
    did = (date_id or "").strip()
    journal_date = date_id_to_journal_date(did) or ""

    narr_path = root / "narrations" / f"narration{did}.json"
    narr: dict[str, Any] | None = narration
    if narr is None and narr_path.is_file():
        narr = load_narration(did, narrations_dir=root / "narrations")

    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    voice_sidecar = (root / "narrations" / f"narration{did}_voice.json").is_file()
    visual_sidecar = (root / "narrations" / f"narration{did}_visual.json").is_file()

    if not narr:
        return {
            "episode_state_version": EPISODE_STATE_VERSION,
            "date_id": did,
            "journal_date": journal_date,
            "updated_at": now,
            "source": source,
            "narration": {
                "exists": False,
                "rel": _rel_under(root, narr_path),
                "segment_count": 0,
                "dialogue_mode": False,
                "long_conversation_mode": False,
                "sidecars": {"voice": voice_sidecar, "visual": visual_sidecar},
            },
            "tts": {"ready": False, "error": "no narration JSON"},
            "video": {"clip_count": 0, "missing_clip_indices": []},
            "anchors": {"eligible_count": 0, "on_disk_count": 0, "missing_anchor_indices": []},
            "output": {"final_mp4_exists": False, "final_mp4_rel": "", "manifest_exists": False},
            "segments": [],
            "summary": {
                "complete": False,
                "blocking": ["Missing narration JSON"],
            },
        }

    episode = build_episode_segment_plans(narr, date_id=did, repo_root=root)

    audio_dir = root / "audio" / did
    final_mp3 = audio_dir / "final.mp3"
    durations_path = audio_dir / "durations.json"
    movie_dir = root / "movie-images" / did
    run_report = movie_dir / "run_report.json"

    seg_rows: list[dict[str, Any]] = []
    missing_audio: list[int] = []
    missing_clips: list[int] = []
    missing_anchors: list[int] = []
    anchor_eligible = 0
    anchor_on_disk = 0
    th_count = 0
    broll_count = 0

    for plan in episode:
        if plan.is_talking_head:
            th_count += 1
        else:
            broll_count += 1
        if not plan.audio_mp3_exists:
            missing_audio.append(plan.index)
        if not plan.clip_mp4_exists:
            missing_clips.append(plan.index)
        if plan.scene_anchor_eligible:
            anchor_eligible += 1
            if plan.anchor_exists:
                anchor_on_disk += 1
            elif plan.is_talking_head or plan.reference_character_id or plan.talking_head_subject:
                missing_anchors.append(plan.index)
        seg_rows.append(
            {
                "index": plan.index,
                "visual_mode": plan.visual_mode,
                "cast_speakers": list(plan.cast_speakers),
                "audio_mp3": plan.audio_mp3_exists,
                "talking_head_drive_mp3": plan.talking_head_drive_mp3.is_file(),
                "clip_mp4": plan.clip_mp4_exists,
                "anchor_exists": plan.anchor_exists,
                "scene_anchor_eligible": plan.scene_anchor_eligible,
            }
        )

    from pipeline.automation_gates import audio_segment_count_mismatch

    tts_err = ""
    if missing_audio:
        tts_ok = False
        tts_err = (
            f"Missing segment audio for {len(missing_audio)} segment(s): "
            + ", ".join(str(i) for i in missing_audio[:8])
            + ("…" if len(missing_audio) > 8 else "")
        )
    elif not durations_path.is_file():
        tts_ok = False
        tts_err = (
            f"Missing {_rel_under(root, durations_path) or durations_path.name} (run TTS first)"
        )
    elif not final_mp3.is_file():
        tts_ok = False
        tts_err = f"Missing {_rel_under(root, final_mp3) or final_mp3.name} (run TTS first)"
    else:
        mismatch, detail = audio_segment_count_mismatch(root, did, len(episode))
        if mismatch:
            tts_ok = False
            tts_err = detail or "Narration segment count does not match audio/durations.json"
        else:
            tts_ok = True

    final_video = _output_video_path(root, did)
    manifest_path = _manifest_path_for_video(root, final_video) if final_video else None

    title_raw = narr.get("title")
    title = title_raw.strip() if isinstance(title_raw, str) else ""

    blocking: list[str] = []
    if missing_audio:
        blocking.append(
            f"Missing segment audio for {len(missing_audio)} segment(s): "
            + ", ".join(str(i) for i in missing_audio[:8])
            + ("…" if len(missing_audio) > 8 else "")
        )
    elif not tts_ok and tts_err:
        blocking.append(tts_err)
    if missing_clips:
        blocking.append(
            f"Missing video clips for {len(missing_clips)} segment(s): "
            + ", ".join(str(i) for i in missing_clips[:8])
            + ("…" if len(missing_clips) > 8 else "")
        )
    th_missing_anchor = [
        p.index
        for p in episode
        if p.is_talking_head and not p.anchor_exists and p.scene_anchor_eligible
    ]
    if th_missing_anchor:
        blocking.append(
            "Talking-head segments missing scene anchors: "
            + ", ".join(str(i) for i in th_missing_anchor[:8])
            + ("…" if len(th_missing_anchor) > 8 else "")
        )
    if final_video is None and not missing_clips and tts_ok:
        blocking.append("No assembled output MP4")

    complete = bool(final_video and final_video.is_file() and tts_ok and not missing_clips)

    return {
        "episode_state_version": EPISODE_STATE_VERSION,
        "date_id": did,
        "journal_date": journal_date,
        "updated_at": now,
        "source": source,
        "narration": {
            "exists": True,
            "rel": _rel_under(root, narr_path),
            "mtime": narr_path.stat().st_mtime if narr_path.is_file() else None,
            "title": title,
            "segment_count": len(episode),
            "dialogue_mode": narration_json_expects_dialogue_mode(narr),
            "long_conversation_mode": narration_json_expects_long_conversation_mode(narr),
            "sidecars": {"voice": voice_sidecar, "visual": visual_sidecar},
        },
        "tts": {
            "ready": tts_ok,
            "error": tts_err if not tts_ok else "",
            "final_mp3": final_mp3.is_file(),
            "final_mp3_rel": _rel_under(root, final_mp3)
            if final_mp3.is_file()
            else f"audio/{did}/final.mp3",
            "missing_audio_indices": missing_audio,
        },
        "video": {
            "clip_count": sum(1 for p in episode if p.clip_mp4_exists),
            "missing_clip_indices": missing_clips,
            "talking_head_count": th_count,
            "b_roll_count": broll_count,
            "run_report_exists": run_report.is_file(),
            "run_report_rel": _rel_under(root, run_report) if run_report.is_file() else "",
        },
        "anchors": {
            "eligible_count": anchor_eligible,
            "on_disk_count": anchor_on_disk,
            "missing_anchor_indices": missing_anchors,
        },
        "output": {
            "final_mp4_exists": final_video is not None and final_video.is_file(),
            "final_mp4_rel": _rel_under(root, final_video) if final_video else "",
            "manifest_exists": manifest_path is not None,
            "manifest_rel": _rel_under(root, manifest_path) if manifest_path else "",
        },
        "segments": seg_rows,
        "summary": {
            "complete": complete,
            "tts_ready": tts_ok,
            "clips_ready": not missing_clips,
            "anchors_ready": not th_missing_anchor,
            "blocking": blocking,
        },
    }


def load_episode_state_sidecar(repo_root: Path, date_id: str) -> dict[str, Any] | None:
    path = episode_state_sidecar_path(repo_root, date_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def write_episode_state_sidecar(repo_root: Path, state: dict[str, Any]) -> Path:
    date_id = str(state.get("date_id") or "").strip()
    if not date_id:
        raise ValueError("episode state missing date_id")
    path = episode_state_sidecar_path(repo_root, date_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return path


def refresh_episode_state_sidecar(
    repo_root: Path,
    date_id: str,
    *,
    narration: dict[str, Any] | None = None,
    source: str = "scan",
) -> dict[str, Any]:
    """Rebuild state from disk and write ``narration<DATE>_state.json``."""
    state = build_episode_state(
        repo_root,
        date_id,
        narration=narration,
        source=source,
    )
    write_episode_state_sidecar(repo_root, state)
    return state
