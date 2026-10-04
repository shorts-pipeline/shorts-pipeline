#!/usr/bin/env python3
"""
Lightweight local web UI for run-daily.py.

Usage (from repo root, with venv activated):
  python pipeline_ui/server.py
  python pipeline_ui/server.py --reload   # dev: restart child when UI files change

Open http://127.0.0.1:8765/ — options load from pipeline_ui/options.json on each request.

Reload mode watches pipeline_ui/ app files (index.html, static/, server.py, options.json, VERSION;
stdlib polling; no extra packages). POST /api/ui/restart also triggers a reload when --reload is active.
Environment: PIPELINE_UI_RELOAD=1 enables the same behavior. Bump pipeline_ui/VERSION when the UI changes.
Restarting stops the HTTP server process; interrupt an in-flight Run from the UI before reloading
if you need a clean stop.

Security: binds to localhost only. Do not expose to the internet.
"""

from __future__ import annotations

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

_UI_DIR = Path(__file__).resolve().parent
_THUMB_CACHE = _UI_DIR / ".thumb_cache"
_STATIC_DIR = _UI_DIR / "static"
_REPO_ROOT = _UI_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import video_manifest
from pipeline.day_reset import archive_day_artifacts, revert_last_run_date_if_matches
from pipeline.narration_characters.voice_prompt import dialogue_profile_prompt_section
from pipeline.narration_common import load_narration_config
from pipeline.narration_phase1 import build_phase1_system_prompt, build_phase1_user_prompt
from pipeline.narration_phase1_dialogue import (
    build_phase1_dialogue_system_prompt,
    build_phase1_dialogue_user_prompt,
)
from pipeline.narration_utils import (
    narration_json_expects_dialogue_mode,
    narration_json_expects_long_conversation_mode,
    sanitize_date_id_for_path,
)
from pipeline.output_naming import date_id_from_output_video_stem
from pipeline.phase1_prompt_prepare import prepare_phase1_prompt_context
from pipeline.recent_episode_diversity import build_episode_diversity_bundle
from pipeline.run_report import load_run_report
from pipeline.ui_argv import boolish as _boolish
from pipeline.ui_argv import build_argv
from pipeline.youtube_metadata import youtube_title_and_description
from pipeline_ui.broll_view import (
    _ensure_b_roll_thumbnail_jpeg,
    _safe_repo_video_rel,
    b_roll_append_from_preview,
    b_roll_materialize_http,
    b_roll_suggest_http_payload,
)
from pipeline_ui.handler import Handler
from pipeline_ui.jobs import JobRegistry
from pipeline_ui.library_view import (
    _STATIC_CONTENT_TYPES,
    _safe_portrait_filename,
    _safe_static_path,
    _safe_voice_preview_filename,
    library_diversity_payload,
    library_pipeline_portraits_payload,
    library_portraits_payload,
    library_prompt_packs_payload,
    library_week_arcs_payload,
    load_latest_links,
    refresh_diversity_audit_http,
    regenerate_diversity_checker_http,
)
from pipeline_ui.narration_edit import (
    _narration_segment_for_index,
    _write_narration_json,
    load_options,
    narration_text_requests_segment_delete,
    narration_update_narration_http,
    narration_update_opening_frame_http,
    narration_update_raw_json_http,
    narration_update_talking_head_prompt_http,
    narration_update_video_prompt_http,
)
from pipeline_ui.narration_view import (
    _segment_cue_fields_from_row,
    narration_segments_payload,
    video_preview_cues_payload,
)
from pipeline_ui.paths import (
    _date_id_from_output_video,
    _file_href_basename,
    _journal_date_to_date_id,
    _narrative_segment_num_from_duration_file,
    _normalize_journal_date_input,
    _path_from_file_href,
    _resolve_python,
    _safe_journal_date,
    _url_from_internet_shortcut,
    episode_state_http_payload,
    journal_entry_payload,
    latest_narration_path,
    latest_video_path,
    run_report_http_payload,
    suggested_journal_date_for_ui,
)
from pipeline_ui.prompt_preview import phase1_prompt_preview_payload
from pipeline_ui.run_actions import (
    assemble_video_run,
    generate_tts_run,
    generate_video_reuse_anchors_run,
    regenerate_video_segment_run,
    reset_day_artifacts_run,
)
from pipeline_ui.youtube_view import (
    _output_video_path_for_date_id,
    youtube_upload_from_preview,
    youtube_upload_status_for_ui,
)
from scripts.b_roll_suggest_for_narration import (
    DEFAULT_MIN_BASE_SIMILARITY,
    build_b_roll_suggest_payload,
)
from video_vendors.fal_avatar import portrait_path_for_talking_head

_OPTIONS_PATH = _UI_DIR / "options.json"
_INDEX_HTML = _UI_DIR / "index.html"
_UI_VERSION_PATH = _UI_DIR / "VERSION"
_UI_RESTART_TRIGGER = _UI_DIR / ".restart_request"
_SERVER_STARTED_UI: dict[str, Any] | None = None
_LATEST_NARRATION_URL = _REPO_ROOT / "latest_narration.url"
_LATEST_VIDEO_URL = _REPO_ROOT / "latest_video.url"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8765
_LOG_DIR = _REPO_ROOT / "logs"
_PIPELINE_UI_LAST_LOG = _LOG_DIR / "pipeline_ui_last.json"
_PIPELINE_UI_RUN_LOCK = _REPO_ROOT / "state" / "pipeline_ui_run.lock"
# Cap captured stdout/stderr so a huge pipeline run does not create an enormous file.
_MAX_CAPTURE_CHARS = 400_000
# Serialize ffmpeg thumbnail extraction (ThreadingHTTPServer + shared .tmp name).
_THUMB_GEN_LOCK = threading.Lock()


def _ffmpeg_executable() -> str | None:
    return shutil.which("ffmpeg")


def _truncate_log_text(s: str, max_len: int = _MAX_CAPTURE_CHARS) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"\n\n... [truncated; original length {len(s)} characters]\n"


_RUN_LOCK = threading.Lock()
_CURRENT_PROC: subprocess.Popen | None = None
_CANCELLED_BY_USER = False

_SCENE_ANCHOR_JOBS = JobRegistry(max_age_sec=3600)
_CONV_MASTER_JOBS = JobRegistry(max_age_sec=3600)
_ANCHOR_PREVIEW_JOBS = JobRegistry(max_age_sec=3600)


def _terminate_pipeline_subprocess(proc: subprocess.Popen) -> None:
    """End run-daily and (on Windows) its child processes (e.g. narration-to-video)."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            kw: dict[str, Any] = {
                "capture_output": True,
                "text": True,
                "timeout": 30,
            }
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kw["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                **kw,
            )
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
    else:
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except OSError:
            pass


def cancel_pipeline_run() -> dict[str, Any]:
    """Terminate the subprocess started by POST /api/run, if still running."""
    global _CANCELLED_BY_USER
    with _RUN_LOCK:
        proc = _CURRENT_PROC
    if proc is None:
        return {"ok": False, "error": "no_run"}
    if proc.poll() is not None:
        return {"ok": False, "error": "already_finished"}
    _CANCELLED_BY_USER = True
    _terminate_pipeline_subprocess(proc)
    return {"ok": True}


def _set_pipeline_ui_run_lock(active: bool) -> None:
    """Touch/remove lock so --reload does not kill the server mid-run."""
    try:
        if active:
            _PIPELINE_UI_RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
            _PIPELINE_UI_RUN_LOCK.write_text(str(os.getpid()), encoding="utf-8")
        else:
            _PIPELINE_UI_RUN_LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def _run_locked_pipeline_cmd(cmd: list[str], log_payload: dict[str, Any]) -> dict[str, Any]:
    """
    Run one pipeline subprocess with the same global lock as POST /api/run.
    ``log_payload`` is copied and augmented with command, stdout/stderr, returncode, then written
    to logs/pipeline_ui_last.json. Returns ``{_blocked: True, error: ...}`` if another run is active.
    """
    global _CANCELLED_BY_USER, _CURRENT_PROC
    cwd = str(_REPO_ROOT)
    with _RUN_LOCK:
        if _CURRENT_PROC is not None and _CURRENT_PROC.poll() is None:
            return {"_blocked": True, "error": "A pipeline run is already in progress."}
        _CANCELLED_BY_USER = False

    _set_pipeline_ui_run_lock(True)
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    with _RUN_LOCK:
        _CURRENT_PROC = proc
    try:
        stdout, stderr = proc.communicate()
    finally:
        _set_pipeline_ui_run_lock(False)
        with _RUN_LOCK:
            if _CURRENT_PROC is proc:
                _CURRENT_PROC = None

    cancelled = _CANCELLED_BY_USER
    _CANCELLED_BY_USER = False

    lp = dict(log_payload)
    lp["command"] = cmd
    lp["cwd"] = cwd
    lp["returncode"] = proc.returncode
    lp["stdout"] = _truncate_log_text(stdout or "")
    lp["stderr"] = _truncate_log_text(stderr or "")
    if cancelled:
        lp["cancelled"] = True
        lp["error"] = None
    elif proc.returncode != 0:
        lp["error"] = f"exit_code_{proc.returncode}"
    else:
        lp["error"] = None
    _write_pipeline_ui_last_run(lp)

    return {
        "_blocked": False,
        "command": cmd,
        "cwd": cwd,
        "returncode": proc.returncode,
        "stdout": stdout or "",
        "stderr": stderr or "",
        "cancelled": cancelled,
    }


def pipeline_status_payload() -> dict[str, Any]:
    with _RUN_LOCK:
        running = _CURRENT_PROC is not None and _CURRENT_PROC.poll() is None
    return {
        "running": running,
        "features": {
            "reset_day": True,
        },
    }


def _read_ui_version() -> str:
    try:
        v = _UI_VERSION_PATH.read_text(encoding="utf-8").strip()
        return v or "0.0.0"
    except OSError:
        return "0.0.0"


def _ui_tracked_paths() -> list[Path]:
    """Files under pipeline_ui/ that define the running app (for fingerprint + reload watch)."""
    paths: list[Path] = []
    if not _UI_DIR.is_dir():
        return paths
    for p in sorted(_UI_DIR.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(_UI_DIR)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if rel.parts and rel.parts[0] in ("__pycache__",):
            continue
        if p.suffix.lower() == ".pyc":
            continue
        paths.append(p)
    return paths


def _ui_fingerprint_from_paths(paths: list[Path] | None = None) -> dict[str, Any]:
    paths = paths or _ui_tracked_paths()
    files: dict[str, str] = {}
    for p in paths:
        rel = p.relative_to(_UI_DIR).as_posix()
        try:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue
        files[rel] = digest
    combined = hashlib.sha256()
    for name in sorted(files):
        combined.update(name.encode("utf-8"))
        combined.update(files[name].encode("ascii"))
    full = combined.hexdigest()
    return {
        "fingerprint": full,
        "fingerprint_short": full[:8],
        "files": files,
    }


def _ui_reload_available() -> bool:
    return os.environ.get("PIPELINE_UI_RELOAD_CHILD", "").strip() == "1"


def _capture_server_ui_state() -> dict[str, Any]:
    fp = _ui_fingerprint_from_paths()
    return {
        "version": _read_ui_version(),
        "fingerprint": fp["fingerprint"],
        "fingerprint_short": fp["fingerprint_short"],
        "file_digests": fp["files"],
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def ui_version_payload() -> dict[str, Any]:
    disk = _ui_fingerprint_from_paths()
    started = _SERVER_STARTED_UI or {}
    server_fp = started.get("fingerprint") or ""
    return {
        "ok": True,
        "version": _read_ui_version(),
        "fingerprint": disk["fingerprint"],
        "fingerprint_short": disk["fingerprint_short"],
        "server_started_fingerprint": server_fp or None,
        "server_started_at": started.get("started_at"),
        "server_stale": bool(server_fp and server_fp != disk["fingerprint"]),
        "reload_available": _ui_reload_available(),
        "server_pid": os.getpid(),
    }


def _ui_verify_message(
    aligned: bool,
    client_stale: bool,
    server_stale: bool,
    reload_available: bool,
) -> str:
    if aligned:
        return "UI aligned: browser, server, and disk match."
    parts: list[str] = []
    if server_stale:
        parts.append("server is running older code than disk")
    if client_stale:
        parts.append("browser loaded older assets than disk")
    msg = "; ".join(parts).capitalize() + "."
    if server_stale and reload_available:
        msg += " Use Restart server below."
    elif server_stale:
        msg += " Restart pipeline_ui/server.py --reload."
    if client_stale:
        msg += " Hard-refresh the page (Ctrl+Shift+R)."
    return msg


def ui_verify_payload(client_fingerprint: str) -> dict[str, Any]:
    base = ui_version_payload()
    disk_fp = base["fingerprint"]
    client_fp = (client_fingerprint or "").strip()
    server_fp = base.get("server_started_fingerprint") or ""
    client_stale = bool(client_fp and client_fp != disk_fp)
    server_stale = bool(server_fp and server_fp != disk_fp)
    aligned = not client_stale and not server_stale
    changed_files: list[str] = []
    if _SERVER_STARTED_UI and server_stale:
        old_files = _SERVER_STARTED_UI.get("file_digests") or {}
        new_files = _ui_fingerprint_from_paths()["files"]
        for name in sorted(set(old_files) | set(new_files)):
            if old_files.get(name) != new_files.get(name):
                changed_files.append(name)
    base.update(
        {
            "aligned": aligned,
            "client_fingerprint": client_fp or None,
            "client_stale": client_stale,
            "changed_files": changed_files,
            "message": _ui_verify_message(
                aligned,
                client_stale,
                server_stale,
                bool(base.get("reload_available")),
            ),
        }
    )
    return base


def ui_restart_payload() -> dict[str, Any]:
    if not _ui_reload_available():
        return {
            "ok": False,
            "error": "reload_not_available",
            "message": (
                "Restart from UI requires pipeline_ui/server.py --reload "
                "(parent process watches for restart requests)."
            ),
        }
    if _PIPELINE_UI_RUN_LOCK.is_file():
        return {
            "ok": False,
            "error": "run_in_progress",
            "message": "Wait for the pipeline run to finish before restarting the UI server.",
        }
    try:
        _UI_RESTART_TRIGGER.write_text(
            datetime.now(UTC).isoformat(),
            encoding="utf-8",
        )
    except OSError as exc:
        return {"ok": False, "error": "trigger_failed", "message": str(exc)}
    return {
        "ok": True,
        "message": "Restart requested; the UI server should reload shortly.",
    }


def _write_pipeline_ui_last_run(payload: dict[str, Any]) -> None:
    """Overwrite logs/pipeline_ui_last.json with the latest /api/run inputs and result (local debugging)."""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = dict(payload)
        payload["logged_at"] = datetime.now().isoformat(timespec="seconds")
        if "source" not in payload:
            payload["source"] = "pipeline_ui POST /api/run"
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        tmp = _PIPELINE_UI_LAST_LOG.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(_PIPELINE_UI_LAST_LOG)
    except OSError as e:
        print(f"[pipeline_ui] Could not write {_PIPELINE_UI_LAST_LOG}: {e}", file=sys.stderr)


def _anchor_image_path_for_segment(date_id: str, segment_index: int) -> Path | None:
    """Return ``movie-images/<date_id>/anchors/NN_<character>.(png|jpg)`` if it exists."""
    if segment_index < 1:
        return None
    did = sanitize_date_id_for_path(date_id)
    if not did:
        return None
    narr_path = _REPO_ROOT / "narrations" / f"narration{did}.json"
    if not narr_path.is_file():
        return None
    try:
        data = json.loads(narr_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    seg = _narration_segment_for_index(data, segment_index)
    if not seg:
        return None
    cid = (seg.get("reference_character_id") or "").strip()
    anchors = _REPO_ROOT / "movie-images" / did / "anchors"
    if not anchors.is_dir():
        return None
    _anchor_exts = (".png", ".jpg", ".jpeg", ".webp")
    if cid:
        for ext in _anchor_exts:
            p = anchors / f"{segment_index:02d}_{cid}{ext}"
            if p.is_file():
                return p
    # Any still for this segment (handles id mismatch vs filename or legacy files).
    for ext in _anchor_exts:
        for p in sorted(anchors.glob(f"{segment_index:02d}_*{ext}")):
            if p.is_file():
                return p
    return None


def fal_scene_anchor_i2i_http_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Run FAL scene-anchor i2i for one segment (same logic as narration-to-video / fal vendor)."""
    from pipeline.conversation_scene_anchor import (
        conversation_run_for_segment,
        conversation_uses_shared_conversation_master,
        resolve_talking_head_scene_anchor,
    )
    from pipeline.narration_visual_mode import VISUAL_MODE_TALKING_HEAD, normalize_visual_mode
    from video_vendors import (
        build_prompts,
        load_fal_scene_anchor_i2i_meta,
        opening_period_line_for_narration_segment,
    )
    from video_vendors.fal import FalVendor, _sanitize_fal_prompt
    from video_vendors.fal_scene_anchor_i2i import run_segment_anchor_to_disk

    jd_raw = body.get("journal_date")
    if not isinstance(jd_raw, str) or not jd_raw.strip():
        return {"ok": False, "error": "missing_journal_date"}
    safe = _safe_journal_date(jd_raw.strip())
    if not safe:
        return {"ok": False, "error": "invalid_journal_date"}
    did = _journal_date_to_date_id(safe)
    if not did:
        return {"ok": False, "error": "invalid_date_id"}
    did_s = sanitize_date_id_for_path(did)
    if not did_s:
        return {"ok": False, "error": "invalid_date_id"}
    did = did_s

    seg = body.get("segment_index", 1)
    try:
        segment_index = int(seg)
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad_segment_index"}
    if segment_index < 1:
        return {"ok": False, "error": "bad_segment_index"}

    ar_raw = body.get("aspect_ratio") or "9:16"
    aspect_ratio = str(ar_raw).strip() if isinstance(ar_raw, str) else "9:16"
    if aspect_ratio not in ("9:16", "16:9"):
        aspect_ratio = "9:16"

    narr_dir = _REPO_ROOT / "narrations"
    narr_path = narr_dir / f"narration{did}.json"
    if not narr_path.is_file():
        return {"ok": False, "error": "no_narration", "date_id": did}

    narr_data = json.loads(narr_path.read_text(encoding="utf-8-sig"))
    prompts = build_prompts(did, narrations_dir=narr_dir, vendor="fal")
    openings, _worlds, core_overrides = load_fal_scene_anchor_i2i_meta(did, narrations_dir=narr_dir)
    world = opening_period_line_for_narration_segment(narr_data, segment_index, date_id=did)
    if segment_index > len(prompts):
        return {"ok": False, "error": "segment_out_of_range", "n_segments": len(prompts)}

    raw = prompts[segment_index - 1]
    rest, cid, _scene_anchor = FalVendor._parse_markers(raw)
    seg_row = _narration_segment_for_index(narr_data, segment_index)
    if not cid and seg_row:
        cid = (
            seg_row.get("reference_character_id") or seg_row.get("talking_head_subject") or ""
        ).strip()

    sanitized = _sanitize_fal_prompt(rest, aggressive=False)
    out_dir = _REPO_ROOT / "movie-images" / did
    out_dir.mkdir(parents=True, exist_ok=True)

    conv_run = conversation_run_for_segment(narr_data, segment_index)
    vm = normalize_visual_mode(seg_row) if seg_row else "b_roll"
    from pipeline.broll_scene_anchor import segment_eligible_for_scene_anchor

    if vm != VISUAL_MODE_TALKING_HEAD and not segment_eligible_for_scene_anchor(
        narr_data, segment_index
    ):
        return {
            "ok": False,
            "error": "broll_scene_anchor_disabled",
            "message": (
                "This B-roll segment has no portrait-backed character "
                "(set reference_character_id to lewis, clark, drouillard, york, etc. "
                "with a character-portraits/ asset). Wan uses text-to-video."
            ),
        }
    try:
        url: str | None = None
        path: Path | None = None
        mask_path: Path | None = None
        if (
            conv_run is not None
            and vm == VISUAL_MODE_TALKING_HEAD
            and conversation_uses_shared_conversation_master()
        ):
            resolved = resolve_talking_head_scene_anchor(
                repo_root=_REPO_ROOT,
                output_dir=out_dir,
                segment_index=segment_index,
                anchor_char=cid,
                narr=narr_data,
                prompts=prompts,
                openings=openings,
                core_overrides=core_overrides,
                world_prefix=world,
                aspect_ratio=aspect_ratio,
                reuse_scene_anchor_stills=False,
                sanitized_prompt=sanitized,
                date_id=did,
            )
            url = resolved.image_url
            path = resolved.image_path
            mask_path = resolved.mask_path
        else:
            url, path = run_segment_anchor_to_disk(
                repo_root=_REPO_ROOT,
                segment_index=segment_index,
                prompt_without_markers_sanitized=sanitized,
                character_id=cid or None,
                output_dir=out_dir,
                opening_frames=openings,
                world_prefix_for_i2i=world,
                core_location_override=(
                    core_overrides[segment_index - 1]
                    if 0 < segment_index <= len(core_overrides)
                    else None
                ),
                aspect_ratio=aspect_ratio,
                aggressive=False,
            )
    except FileNotFoundError as e:
        return {"ok": False, "error": "no_portrait", "message": str(e)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        from pipeline.scene_anchor_quality import SceneAnchorQualityError

        if isinstance(e, SceneAnchorQualityError):
            return {
                "ok": False,
                "error": "anchor_quality_failed",
                "reason_code": e.reason_code,
                "message": str(e),
            }
        return {"ok": False, "error": "fal_failed", "message": str(e)}

    rel = None
    if path:
        try:
            rel = str(path.resolve().relative_to(_REPO_ROOT.resolve())).replace("\\", "/")
        except ValueError:
            rel = str(path)
    mask_rel = None
    if mask_path:
        try:
            mask_rel = str(mask_path.resolve().relative_to(_REPO_ROOT.resolve())).replace("\\", "/")
        except ValueError:
            mask_rel = str(mask_path)

    from pipeline.conversation_scene_anchor import (
        conversation_run_for_segment,
        master_anchor_path,
    )

    conv_run = conversation_run_for_segment(narr_data, segment_index)
    conv_refresh = [segment_index]
    conv_meta: dict[str, Any] = {
        "conversation_anchor_run": False,
        "conversation_refresh_segments": conv_refresh,
    }
    if conv_run is not None:
        conv_refresh = list(conv_run.segment_indices)
        master_rel = ""
        mp = master_anchor_path(out_dir, conv_run)
        if mp.is_file():
            try:
                master_rel = str(mp.relative_to(_REPO_ROOT.resolve())).replace("\\", "/")
            except ValueError:
                master_rel = mp.name
        conv_meta = {
            "conversation_anchor_run": True,
            "conversation_refresh_segments": conv_refresh,
            "conversation_run_first_segment": conv_run.first_segment_index,
            "conversation_run_segments": list(conv_run.segment_indices),
            "conversation_composite_id": conv_run.composite_id,
            "conversation_master_rel": master_rel,
        }

    preview_q = "journal_date=" + safe + "&segment=" + str(segment_index)
    return {
        "ok": True,
        "date_id": did,
        "segment_index": segment_index,
        "character_id": cid,
        "fal_image_url": url,
        "saved_path": str(path) if path else None,
        "relative_path": rel,
        "mask_relative_path": mask_rel,
        "preview_url": "/api/preview/anchor-image?" + preview_q,
        **conv_meta,
    }


def _scene_anchor_i2i_http_code(result: dict[str, Any]) -> int:
    if result.get("ok"):
        return 200
    err = result.get("error")
    if err in (
        "missing_journal_date",
        "invalid_journal_date",
        "invalid_date_id",
        "bad_segment_index",
    ):
        return 400
    if err in ("no_narration", "no_portrait", "unknown_job_id"):
        return 404
    if err == "segment_out_of_range":
        return 400
    if err == "not_scene_anchor":
        return 422
    if err == "job_already_running":
        return 409
    return 500


def _scene_anchor_job_key(body: dict[str, Any]) -> str | None:
    jd_raw = body.get("journal_date")
    if not isinstance(jd_raw, str) or not jd_raw.strip():
        return None
    safe = _safe_journal_date(jd_raw.strip())
    if not safe:
        return None
    did = _journal_date_to_date_id(safe)
    if not did:
        return None
    did_s = sanitize_date_id_for_path(did)
    if not did_s:
        return None
    try:
        seg_i = int(body.get("segment_index", 1))
    except (TypeError, ValueError):
        return None
    if seg_i < 1:
        return None
    return f"{did_s}:{seg_i}"


def _run_scene_anchor_job(job_id: str, body: dict[str, Any]) -> None:
    try:
        result = fal_scene_anchor_i2i_http_payload(body)
    except Exception as exc:
        result = {
            "ok": False,
            "error": "scene_anchor_exception",
            "message": str(exc),
        }
    _SCENE_ANCHOR_JOBS.finish(job_id, result)


def start_fal_scene_anchor_i2i_job(body: dict[str, Any]) -> dict[str, Any]:
    """
    Start scene-anchor i2i on a background thread; return immediately with ``job_id`` for polling.
    FAL work often takes 1–2 minutes — holding the HTTP POST open fails when the UI reloads.
    """
    b = body if isinstance(body, dict) else {}
    key = _scene_anchor_job_key(b)
    job_id, already_running = _SCENE_ANCHOR_JOBS.start(
        _run_scene_anchor_job, args=(dict(b),), key=key, thread_name="scene-anchor"
    )
    if already_running:
        return {
            "ok": True,
            "async": True,
            "already_running": True,
            "job_id": job_id,
            "status": "running",
            "poll_url": f"/api/fal/scene-anchor-i2i/status?job_id={job_id}",
        }
    return {
        "ok": True,
        "async": True,
        "job_id": job_id,
        "status": "running",
        "poll_url": f"/api/fal/scene-anchor-i2i/status?job_id={job_id}",
    }


def fal_scene_anchor_i2i_job_status(job_id: str) -> dict[str, Any]:
    return _SCENE_ANCHOR_JOBS.status(job_id)


def _journal_date_body_to_date_id(
    body: dict[str, Any],
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Return (safe_journal_date, date_id, error_dict)."""
    jd_raw = body.get("journal_date")
    if not isinstance(jd_raw, str) or not jd_raw.strip():
        return None, None, {"ok": False, "error": "missing_journal_date"}
    safe = _safe_journal_date(jd_raw.strip())
    if not safe:
        return None, None, {"ok": False, "error": "invalid_journal_date"}
    did = _journal_date_to_date_id(safe)
    if not did:
        return None, None, {"ok": False, "error": "invalid_date_id"}
    did_s = sanitize_date_id_for_path(did)
    if not did_s:
        return None, None, {"ok": False, "error": "invalid_date_id"}
    return safe, did_s, None


def _anchor_preview_video_path_for_date_id(date_id: str) -> Path | None:
    from pipeline.anchor_preview import preview_output_path

    did = sanitize_date_id_for_path(date_id)
    if not did:
        return None
    p = preview_output_path(_REPO_ROOT, did)
    return p if p.is_file() else None


def anchor_preview_status_http(journal_date: str) -> dict[str, Any]:
    from pipeline.anchor_preview import status_payload
    from pipeline.conversation_scene_anchor import conversation_anchor_ui_enrichment

    safe = _safe_journal_date((journal_date or "").strip())
    if not safe:
        return {"ok": False, "error": "invalid_journal_date"}
    did = _journal_date_to_date_id(safe)
    if not did:
        return {"ok": False, "error": "invalid_date_id"}
    did_s = sanitize_date_id_for_path(did)
    if not did_s:
        return {"ok": False, "error": "invalid_date_id"}
    payload = status_payload(did_s, repo_root=_REPO_ROOT, journal_date=safe)
    narr_path = _REPO_ROOT / "narrations" / f"narration{did_s}.json"
    if narr_path.is_file():
        try:
            narr_data = json.loads(narr_path.read_text(encoding="utf-8-sig"))
            if isinstance(narr_data, dict):
                conv_ui = conversation_anchor_ui_enrichment(narr_data)
                payload["conversation_runs"] = conv_ui.get("conversation_runs") or []
        except (json.JSONDecodeError, OSError):
            pass
    return payload


def _run_anchor_preview_job(job_id: str, body: dict[str, Any], phase: str) -> None:
    from pipeline.anchor_preview import (
        assemble_anchor_preview_video,
        assemble_anchor_preview_with_missing_anchors,
        build_scene_anchors_batch,
        render_anchor_preview_clips,
        summarize_anchor_preview_issues,
    )

    safe, did, err = _journal_date_body_to_date_id(body)
    if err:
        result = err
    else:
        force = _boolish(body.get("force"), False)
        if "skip_existing" in body:
            skip_existing = _boolish(body.get("skip_existing"), True)
        else:
            skip_existing = not force
        wide = _boolish(body.get("wide_screen"), False)
        shorts = not wide
        try:
            if phase == "anchors":
                report = build_scene_anchors_batch(
                    did or "",
                    repo_root=_REPO_ROOT,
                    skip_existing=skip_existing,
                    force=force,
                    aspect_ratio=str(body.get("aspect_ratio") or "9:16"),
                )
                issues = summarize_anchor_preview_issues(report)
                result = {
                    "ok": True,
                    "phase": phase,
                    "report": report.to_dict(),
                    "issues": issues,
                    "has_warnings": bool(issues),
                }
            elif phase == "assemble":
                report, out = assemble_anchor_preview_with_missing_anchors(
                    did or "",
                    repo_root=_REPO_ROOT,
                    skip_existing=skip_existing,
                    force=force,
                    shorts=shorts,
                    ambient=_boolish(body.get("ambient"), False),
                    aspect_ratio=str(body.get("aspect_ratio") or "9:16"),
                )
                issues = summarize_anchor_preview_issues(report)
                result = {
                    "ok": True,
                    "phase": phase,
                    "preview_video": str(out),
                    "report": report.to_dict(),
                    "issues": issues,
                    "has_warnings": bool(issues),
                }
            else:
                result = {"ok": False, "error": "unknown_phase", "phase": phase}
        except Exception as exc:
            result = {
                "ok": False,
                "error": "anchor_preview_failed",
                "message": str(exc),
                "phase": phase,
            }
        if safe:
            result["journal_date"] = safe
            result["date_id"] = did

    _ANCHOR_PREVIEW_JOBS.finish(job_id, result)


def start_anchor_preview_job(body: dict[str, Any], phase: str) -> dict[str, Any]:
    """Background job for batch anchors or preview assembly."""
    b = body if isinstance(body, dict) else {}
    safe, did, err = _journal_date_body_to_date_id(b)
    if err:
        return err
    job_id, already_running = _ANCHOR_PREVIEW_JOBS.start(
        _run_anchor_preview_job,
        args=(dict(b), phase),
        key=did,
        public={"phase": phase},
        thread_name=f"anchor-preview-{phase}",
    )
    if already_running:
        return {
            "ok": True,
            "async": True,
            "already_running": True,
            "job_id": job_id,
            "status": "running",
            "poll_url": f"/api/anchor-preview/status?job_id={job_id}",
        }
    return {
        "ok": True,
        "async": True,
        "job_id": job_id,
        "status": "running",
        "phase": phase,
        "poll_url": f"/api/anchor-preview/job-status?job_id={job_id}",
    }


def anchor_preview_job_status(job_id: str) -> dict[str, Any]:
    return _ANCHOR_PREVIEW_JOBS.status(job_id)


def _narration_config_json_path() -> Path:
    return _REPO_ROOT / "config" / "narration_config.json"


def conversation_scene_anchor_settings_payload() -> dict[str, Any]:
    from pipeline.conversation_scene_anchor import (
        conversation_master_reference_mode,
        conversation_master_split_crop_width_fraction,
        conversation_master_split_right_shift_fraction,
        conversation_scene_anchor_enabled,
        conversation_scene_anchor_strategy,
        conversation_uses_shared_master_bookend,
        conversation_uses_shared_master_mask,
        conversation_uses_shared_master_split,
    )

    return {
        "ok": True,
        "enabled": conversation_scene_anchor_enabled(),
        "strategy": conversation_scene_anchor_strategy(),
        "uses_shared_master_split": conversation_uses_shared_master_split(),
        "uses_shared_master_mask": conversation_uses_shared_master_mask(),
        "uses_shared_master_bookend": conversation_uses_shared_master_bookend(),
        "master_reference_mode": conversation_master_reference_mode(),
        "master_split_crop_width_fraction": conversation_master_split_crop_width_fraction(),
        "master_split_right_shift_fraction": conversation_master_split_right_shift_fraction(),
    }


def update_conversation_scene_anchor_settings(body: dict[str, Any]) -> dict[str, Any]:
    b = body if isinstance(body, dict) else {}
    strategy = str(b.get("strategy") or "").strip().lower()
    if strategy not in (
        "shared_master_split",
        "shared_master_mask",
        "shared_master_bookend",
        "solo_scene_anchor",
    ):
        return {
            "ok": False,
            "error": "invalid_strategy",
            "message": (
                "strategy must be shared_master_split, shared_master_mask, "
                "shared_master_bookend, or solo_scene_anchor"
            ),
        }
    cfg_path = _narration_config_json_path()
    if not cfg_path.is_file():
        return {"ok": False, "error": "missing_config", "message": str(cfg_path)}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"ok": False, "error": "config_read_failed", "message": str(exc)}
    block = data.get("fal_conversation_scene_anchor")
    if not isinstance(block, dict):
        block = {}
        data["fal_conversation_scene_anchor"] = block
    block["strategy"] = strategy
    try:
        cfg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": "config_write_failed", "message": str(exc)}
    return conversation_scene_anchor_settings_payload()


def conversation_scene_anchor_status_http(journal_date: str) -> dict[str, Any]:
    from pipeline.conversation_scene_anchor import (
        conversation_anchor_ui_enrichment,
        conversation_uses_shared_master_split,
        discover_conversation_anchor_runs,
        master_anchor_path,
    )

    safe = _safe_journal_date((journal_date or "").strip())
    if not safe:
        return {"ok": False, "error": "invalid_journal_date"}
    did = _journal_date_to_date_id(safe)
    if not did:
        return {"ok": False, "error": "invalid_date_id"}
    narr_path = _REPO_ROOT / "narrations" / f"narration{did}.json"
    if not narr_path.is_file():
        return {
            "ok": True,
            "date_id": did,
            "journal_date": safe,
            "has_narration": False,
            "conversation_runs": [],
            "uses_shared_master_split": conversation_uses_shared_master_split(),
        }
    narr = json.loads(narr_path.read_text(encoding="utf-8-sig"))
    conv_ui = conversation_anchor_ui_enrichment(narr)
    runs = discover_conversation_anchor_runs(narr)
    out_dir = _REPO_ROOT / "movie-images" / did
    runs_out: list[dict[str, Any]] = []
    for idx, run in enumerate(runs):
        mp = master_anchor_path(out_dir, run)
        master_rel = ""
        if mp.is_file():
            try:
                master_rel = str(mp.relative_to(_REPO_ROOT.resolve())).replace("\\", "/")
            except ValueError:
                master_rel = mp.name
        runs_out.append(
            {
                "run_index": idx,
                "first_segment_index": run.first_segment_index,
                "segment_indices": list(run.segment_indices),
                "composite_id": run.composite_id,
                "left_speaker_id": run.left_speaker_id,
                "right_speaker_id": run.right_speaker_id,
                "shared_setting": run.shared_setting,
                "shared_setting_preview": (run.shared_setting or "")[:160],
                "master_exists": mp.is_file(),
                "master_relative_path": master_rel,
                "master_preview_url": (
                    f"/api/preview/conversation-master?journal_date={safe}&run_index={idx}"
                    if mp.is_file()
                    else None
                ),
            }
        )
    return {
        "ok": True,
        "date_id": did,
        "journal_date": safe,
        "has_narration": True,
        "long_conversation_mode": narration_json_expects_long_conversation_mode(narr),
        "uses_shared_master_split": conversation_uses_shared_master_split(),
        "conversation_runs": runs_out,
        "conversation_by_segment": conv_ui.get("conversation_by_segment") or {},
    }


def conversation_shared_setting_http(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Persist ``conversation_micro_arc.shared_setting`` and sync opening frames."""
    from pipeline.conversation_scene_anchor import apply_conversation_shared_setting_update

    b = body if isinstance(body, dict) else {}
    safe, did, err = _journal_date_body_to_date_id(b)
    if err:
        return 400, err
    setting_raw = b.get("shared_setting")
    if setting_raw is None or not str(setting_raw).strip():
        return 400, {"ok": False, "error": "missing_shared_setting"}
    try:
        run_index = int(b.get("run_index", 0))
    except (TypeError, ValueError):
        run_index = 0
    narr_path = _REPO_ROOT / "narrations" / f"narration{did}.json"
    if not narr_path.is_file():
        return 404, {"ok": False, "error": "no_narration", "date_id": did}
    data = json.loads(narr_path.read_text(encoding="utf-8-sig"))
    try:
        summary = apply_conversation_shared_setting_update(
            data, str(setting_raw).strip(), run_index=run_index
        )
    except ValueError as exc:
        return 400, {"ok": False, "error": "invalid_shared_setting", "message": str(exc)}
    code, out = _write_narration_json(narr_path, data)
    if code != 200:
        return code, out
    return 200, {"ok": True, "date_id": did, "journal_date": safe, **summary}


def conversation_master_regen_http_payload(body: dict[str, Any]) -> dict[str, Any]:
    from pipeline.conversation_scene_anchor import (
        apply_conversation_shared_setting_update,
        conversation_uses_shared_conversation_master,
        conversation_uses_shared_master_bookend,
        conversation_uses_shared_master_mask,
        discover_conversation_anchor_runs,
        ensure_conversation_run_anchors,
    )
    from video_vendors import build_prompts, load_fal_scene_anchor_i2i_meta

    b = body if isinstance(body, dict) else {}
    safe, did, err = _journal_date_body_to_date_id(b)
    if err:
        return err
    if not conversation_uses_shared_conversation_master():
        return {
            "ok": False,
            "error": "strategy_not_shared_master",
            "message": (
                "Switch anchor strategy to Shared master + split, mask, or bookend "
                "to regenerate the master."
            ),
        }
    narr_path = _REPO_ROOT / "narrations" / f"narration{did}.json"
    if not narr_path.is_file():
        return {"ok": False, "error": "no_narration", "date_id": did}
    try:
        run_index = int(b.get("run_index", 0))
    except (TypeError, ValueError):
        run_index = 0
    narr = json.loads(narr_path.read_text(encoding="utf-8-sig"))
    setting_override = str(b.get("shared_setting") or "").strip()
    if setting_override:
        try:
            apply_conversation_shared_setting_update(narr, setting_override, run_index=run_index)
        except ValueError as exc:
            return {"ok": False, "error": "invalid_shared_setting", "message": str(exc)}
        code, wout = _write_narration_json(narr_path, narr)
        if code != 200:
            return {"ok": False, "error": "narration_write_failed", **wout}
    runs = discover_conversation_anchor_runs(narr)
    if not runs:
        return {"ok": False, "error": "no_conversation_runs", "date_id": did}
    if run_index < 0 or run_index >= len(runs):
        return {
            "ok": False,
            "error": "run_index_out_of_range",
            "run_count": len(runs),
        }
    ar_raw = b.get("aspect_ratio") or "9:16"
    aspect_ratio = str(ar_raw).strip() if isinstance(ar_raw, str) else "9:16"
    if aspect_ratio not in ("9:16", "16:9"):
        aspect_ratio = "9:16"
    run = runs[run_index]
    prompts = build_prompts(did, narrations_dir=_REPO_ROOT / "narrations", vendor="fal")
    openings, _, core_overrides = load_fal_scene_anchor_i2i_meta(
        did, narrations_dir=_REPO_ROOT / "narrations"
    )
    out_dir = _REPO_ROOT / "movie-images" / did
    if conversation_uses_shared_master_bookend():
        from pipeline.conversation_bookend_anchor import (
            ensure_conversation_run_bookend_anchors,
        )

        derived = ensure_conversation_run_bookend_anchors(
            repo_root=_REPO_ROOT,
            output_dir=out_dir,
            run=run,
            narr=narr,
            prompts=prompts,
            openings=openings,
            core_overrides=core_overrides,
            aspect_ratio=aspect_ratio,
            date_id=did,
            skip_existing=False,
            force=True,
        )
    elif conversation_uses_shared_master_mask():
        from pipeline.conversation_omnihuman_mask import (
            ensure_conversation_run_omnihuman_masks,
        )

        derived = ensure_conversation_run_omnihuman_masks(
            repo_root=_REPO_ROOT,
            output_dir=out_dir,
            run=run,
            narr=narr,
            prompts=prompts,
            openings=openings,
            core_overrides=core_overrides,
            aspect_ratio=aspect_ratio,
            date_id=did,
            skip_existing=False,
            force=True,
        )
    else:
        derived = ensure_conversation_run_anchors(
            repo_root=_REPO_ROOT,
            output_dir=out_dir,
            run=run,
            narr=narr,
            prompts=prompts,
            openings=openings,
            core_overrides=core_overrides,
            aspect_ratio=aspect_ratio,
            date_id=did,
            skip_existing=False,
            force=True,
        )
    seg_paths = sorted(
        str(p.relative_to(_REPO_ROOT.resolve())).replace("\\", "/")
        for p in derived.values()
        if p.is_file()
    )
    return {
        "ok": True,
        "date_id": did,
        "run_index": run_index,
        "segment_indices": list(run.segment_indices),
        "anchor_paths": seg_paths,
        "conversation_refresh_segments": list(run.segment_indices),
    }


def _conv_master_job_key(body: dict[str, Any]) -> str | None:
    safe, did, err = _journal_date_body_to_date_id(body)
    if err or not did:
        return None
    try:
        run_index = int(body.get("run_index", 0))
    except (TypeError, ValueError):
        run_index = 0
    return f"{did}:{run_index}"


def _run_conv_master_regen_job(job_id: str, body: dict[str, Any]) -> None:
    try:
        result = conversation_master_regen_http_payload(body)
    except Exception as exc:
        result = {
            "ok": False,
            "error": "conv_master_exception",
            "message": str(exc),
        }
    _CONV_MASTER_JOBS.finish(job_id, result)


def start_conv_master_regen_job(body: dict[str, Any]) -> dict[str, Any]:
    b = body if isinstance(body, dict) else {}
    key = _conv_master_job_key(b)
    job_id, _already_running = _CONV_MASTER_JOBS.start(
        _run_conv_master_regen_job, args=(b,), key=key, thread_name="conv-master"
    )
    return {
        "ok": True,
        "async": True,
        "job_id": job_id,
        "status": "running",
        "poll_url": f"/api/conversation-scene-anchor/regenerate-master/status?job_id={job_id}",
    }


def conv_master_regen_job_status(job_id: str) -> dict[str, Any]:
    return _CONV_MASTER_JOBS.status(job_id)


def main() -> None:
    global _SERVER_STARTED_UI
    _SERVER_STARTED_UI = _capture_server_ui_state()
    host = os.environ.get("PIPELINE_UI_HOST", _DEFAULT_HOST)
    port = int(os.environ.get("PIPELINE_UI_PORT", str(_DEFAULT_PORT)))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Pipeline UI: http://{host}:{port}/")
    print(f"UI version {_SERVER_STARTED_UI['version']} ({_SERVER_STARTED_UI['fingerprint_short']})")
    print(f"Repo root: {_REPO_ROOT}")
    print(
        f"Last run log: {_PIPELINE_UI_LAST_LOG.relative_to(_REPO_ROOT)} (inputs + command + stdout/stderr)"
    )
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


def _reload_enabled() -> bool:
    env = os.environ.get("PIPELINE_UI_RELOAD", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    return "--reload" in sys.argv or "-r" in sys.argv


def _reload_watch_paths() -> list[Path]:
    paths = list(_ui_tracked_paths())
    if _UI_RESTART_TRIGGER not in paths:
        paths.append(_UI_RESTART_TRIGGER)
    return paths


def _pipeline_ui_listen_port() -> int:
    return int(os.environ.get("PIPELINE_UI_PORT", str(_DEFAULT_PORT)))


def _pipeline_ui_port_probe_host() -> str:
    host = os.environ.get("PIPELINE_UI_HOST", _DEFAULT_HOST)
    return "127.0.0.1" if host in ("0.0.0.0", "::") else host


def _wait_pipeline_ui_port_free(*, timeout_sec: float = 12.0) -> bool:
    """Return True when nothing is accepting connections on the UI port."""
    port = _pipeline_ui_listen_port()
    probe = _pipeline_ui_port_probe_host()
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((probe, port), timeout=0.25):
                time.sleep(0.2)
        except OSError:
            return True
    return False


def _run_reload_loop() -> None:
    """Spawn server.py (no --reload) and restart when watched files change."""
    script = Path(__file__).resolve()
    child_argv = [sys.executable, str(script)]
    for a in sys.argv[1:]:
        if a in ("--reload", "-r"):
            continue
        child_argv.append(a)

    watch = _reload_watch_paths()
    if not watch:
        print("pipeline_ui reload: no files to watch; running once.", flush=True)
        os.execv(child_argv[0], child_argv)
        return

    try:
        _UI_RESTART_TRIGGER.touch(exist_ok=True)
    except OSError:
        pass

    watch = [p for p in watch if p.exists() or p == _UI_RESTART_TRIGGER]
    rel_watch = []
    for p in watch:
        try:
            rel_watch.append(str(p.relative_to(_REPO_ROOT)))
        except ValueError:
            rel_watch.append(str(p))
    print(
        "Pipeline UI (reload mode): watching "
        + ", ".join(rel_watch[:6])
        + (f" (+{len(rel_watch) - 6} more)" if len(rel_watch) > 6 else "")
        + " — save a UI file or POST /api/ui/restart to restart.",
        flush=True,
    )

    proc: subprocess.Popen | None = None

    def _stop_child() -> None:
        nonlocal proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        proc = None

    def _on_signal(_signum: int, _frame: Any) -> None:
        _stop_child()
        raise SystemExit(130)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (AttributeError, ValueError):
            pass

    def _spawn_child() -> subprocess.Popen:
        child_env = os.environ.copy()
        child_env["PIPELINE_UI_RELOAD_CHILD"] = "1"
        for attempt in range(5):
            proc = subprocess.Popen(
                child_argv,
                cwd=str(_REPO_ROOT),
                env=child_env,
            )
            time.sleep(0.35)
            if proc.poll() is None:
                return proc
            code = proc.returncode if proc.returncode is not None else 1
            if attempt >= 4:
                sys.exit(code)
            print(
                f"[pipeline_ui] child exited immediately (code {code}); "
                "waiting for port and retrying…",
                flush=True,
            )
            _wait_pipeline_ui_port_free(timeout_sec=4.0)
        raise RuntimeError("unreachable")

    consecutive_quick_exits = 0
    try:
        while True:
            spawn_time = time.monotonic()
            proc = _spawn_child()

            mtimes: dict[Path, float | None] = {}
            for p in watch:
                try:
                    mtimes[p] = p.stat().st_mtime
                except OSError:
                    mtimes[p] = None

            while proc.poll() is None:
                time.sleep(0.45)
                changed = False
                changed_path = watch[0]
                for p in watch:
                    try:
                        cur = p.stat().st_mtime
                    except OSError:
                        cur = None
                    if cur != mtimes.get(p):
                        changed = True
                        changed_path = p
                        break
                if changed:
                    try:
                        rel = str(changed_path.relative_to(_REPO_ROOT))
                    except ValueError:
                        rel = str(changed_path)
                    if _PIPELINE_UI_RUN_LOCK.is_file():
                        print(
                            f"\n[pipeline_ui] file changed ({rel}); "
                            "waiting for pipeline run to finish before restart…",
                            flush=True,
                        )
                        while _PIPELINE_UI_RUN_LOCK.is_file() and proc.poll() is None:
                            time.sleep(0.5)
                    print(f"\n[pipeline_ui] file changed ({rel}); restarting server…", flush=True)
                    _stop_child()
                    if not _wait_pipeline_ui_port_free():
                        print(
                            "[pipeline_ui] port still busy after child stop; "
                            "waiting longer before respawn…",
                            flush=True,
                        )
                        _wait_pipeline_ui_port_free(timeout_sec=15.0)
                    watch[:] = _reload_watch_paths()
                    break
            else:
                # Child exited on its own (crash, or something killed just the worker) —
                # not from our file-change/restart-trigger path above. Respawn it so the
                # UI comes back without anyone having to re-run `server.py --reload` by hand,
                # but give up after a burst of near-instant deaths instead of spinning forever.
                code = proc.returncode if proc.returncode is not None else 0
                _stop_child()
                if time.monotonic() - spawn_time < 5.0:
                    consecutive_quick_exits += 1
                else:
                    consecutive_quick_exits = 0
                if consecutive_quick_exits > 5:
                    print(
                        f"[pipeline_ui] child exited repeatedly right after start (code {code}); "
                        "giving up — fix the error above, then restart manually.",
                        flush=True,
                    )
                    sys.exit(code)
                print(
                    f"[pipeline_ui] child exited unexpectedly (code {code}); respawning…",
                    flush=True,
                )
                if not _wait_pipeline_ui_port_free():
                    _wait_pipeline_ui_port_free(timeout_sec=15.0)
                watch[:] = _reload_watch_paths()
    except KeyboardInterrupt:
        _stop_child()
        raise SystemExit(130) from None


if __name__ == "__main__":
    if _reload_enabled():
        _run_reload_loop()
    else:
        main()
