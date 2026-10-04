"""Pipeline run actions for pipeline_ui: TTS-only, video-reuse-anchors, reset-day,
per-segment video regen, and assembly-only (videos-mp3-to-movie) runs.

Split out of ``server.py`` (see ``ai-plans/pipeline-ui-server-split.md``, step 4).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import video_manifest
from pipeline.day_reset import archive_day_artifacts, revert_last_run_date_if_matches
from pipeline.ui_argv import boolish as _boolish
from pipeline_ui.narration_edit import load_options
from pipeline_ui.paths import _journal_date_to_date_id, _resolve_python, _safe_journal_date
from pipeline_ui.runtime import srv as _srv
from pipeline_ui.youtube_view import _output_video_path_for_date_id


def generate_tts_run(body: dict[str, Any]) -> dict[str, Any]:
    """Run narration-to-mp3 only (segment MP3s, durations.json, final.mp3)."""
    srv = _srv()
    repo_root = srv._REPO_ROOT
    b = body if isinstance(body, dict) else {}
    safe, did, err = srv._journal_date_body_to_date_id(b)
    if err:
        return err

    narr_path = repo_root / "narrations" / f"narration{did}.json"
    if not narr_path.is_file():
        return {"ok": False, "error": "no_narration", "message": f"Missing {narr_path.name}"}

    tts_raw = b.get("tts")
    tts = str(tts_raw).strip().lower() if tts_raw is not None else "openai"
    if tts not in ("openai", "pyttsx3"):
        tts = "openai"

    seg_arg: str | None = None
    seg_raw = b.get("segment_indices")
    if seg_raw is not None and str(seg_raw).strip():
        seg_arg = str(seg_raw).strip()

    spec = load_options()
    if spec.get("error"):
        return {"ok": False, "error": "options_load_failed", "message": str(spec.get("error"))}
    py = _resolve_python(
        os.environ.get("PIPELINE_UI_PYTHON"),
        list(
            spec.get("backend", {}).get("python_candidates")
            or [".venv/Scripts/python.exe", ".venv/bin/python"]
        ),
    )
    script = repo_root / "narration-to-mp3.py"
    if not script.is_file():
        return {"ok": False, "error": "missing_script", "message": str(script)}

    cmd = [py, str(script), did or "", "--tts", tts]
    if seg_arg:
        cmd.extend(["--segments", seg_arg])

    log_payload: dict[str, Any] = {
        "inputs": {
            "journal_date": safe,
            "date_id": did,
            "tts": tts,
            "segment_indices": seg_arg,
            "action": "generate_tts",
        },
        "source": "pipeline_ui POST /api/generate-tts",
    }

    run = srv._run_locked_pipeline_cmd(cmd, log_payload)
    if run.get("_blocked"):
        return {
            "ok": False,
            "error": "run_in_progress",
            "message": run.get("error", "A pipeline run is already in progress."),
        }
    if run.get("returncode") != 0 and not run.get("cancelled"):
        return {
            "ok": False,
            "error": "pipeline_failed",
            "command": run.get("command"),
            "returncode": run.get("returncode"),
            "stderr": run.get("stderr"),
            "stdout": run.get("stdout"),
        }

    final_mp3 = repo_root / "audio" / (did or "") / "final.mp3"
    durations = repo_root / "audio" / (did or "") / "durations.json"
    run["ok"] = True
    run["journal_date"] = safe
    run["date_id"] = did
    run["tts"] = tts
    run["final_mp3_rel"] = (
        str(final_mp3.relative_to(repo_root)).replace("\\", "/") if final_mp3.is_file() else ""
    )
    run["durations_rel"] = (
        str(durations.relative_to(repo_root)).replace("\\", "/") if durations.is_file() else ""
    )
    return run


def generate_video_reuse_anchors_run(body: dict[str, Any]) -> dict[str, Any]:
    """Run narration-to-video with --reuse-scene-anchor-stills (OmniHuman/Wan after anchor review)."""
    srv = _srv()
    repo_root = srv._REPO_ROOT
    b = body if isinstance(body, dict) else {}
    safe, did, err = srv._journal_date_body_to_date_id(b)
    if err:
        return err

    narr_path = repo_root / "narrations" / f"narration{did}.json"
    if not narr_path.is_file():
        return {"ok": False, "error": "no_narration"}

    wc = b.get("wide_screen")
    if isinstance(wc, str):
        wide_screen = wc.strip().lower() in ("1", "true", "yes", "on")
    else:
        wide_screen = bool(wc) if wc is not None else False

    spec = load_options()
    if spec.get("error"):
        return {"ok": False, "error": "options_load_failed", "message": str(spec.get("error"))}
    py = _resolve_python(
        os.environ.get("PIPELINE_UI_PYTHON"),
        list(
            spec.get("backend", {}).get("python_candidates")
            or [".venv/Scripts/python.exe", ".venv/bin/python"]
        ),
    )
    script = repo_root / "narration-to-video.py"
    if not script.is_file():
        return {"ok": False, "error": "missing_script", "message": str(script)}

    cmd = [
        py,
        str(script),
        did or "",
        "--vendor",
        "fal",
        "--reuse-scene-anchor-stills",
    ]
    if not wide_screen:
        cmd.append("--shorts")
    else:
        cmd.append("--wide-screen")

    assemble_after = _boolish(b.get("assemble_after"), True)
    log_payload: dict[str, Any] = {
        "inputs": {
            "journal_date": safe,
            "date_id": did,
            "wide_screen": wide_screen,
            "assemble_after": assemble_after,
            "action": "generate_video_reuse_anchors",
        },
        "source": "pipeline_ui POST /api/generate-video-reuse-anchors",
    }

    run = srv._run_locked_pipeline_cmd(cmd, log_payload)
    if run.get("_blocked"):
        return {
            "ok": False,
            "error": "run_in_progress",
            "message": run.get("error", "A pipeline run is already in progress."),
        }
    if run.get("returncode") != 0 and not run.get("cancelled"):
        return {
            "ok": False,
            "error": "pipeline_failed",
            "command": run.get("command"),
            "returncode": run.get("returncode"),
            "stderr": run.get("stderr"),
        }

    if assemble_after:
        asm = assemble_video_run(
            {
                "journal_date": safe,
                "wide_screen": wide_screen,
            }
        )
        if not asm.get("ok"):
            return {
                "ok": False,
                "error": "assemble_failed",
                "video_step": "ok",
                "assemble": asm,
            }
        run["assemble"] = asm

    run["ok"] = True
    run["journal_date"] = safe
    run["date_id"] = did
    return run


def reset_day_artifacts_run(body: dict[str, Any]) -> dict[str, Any]:
    """
    Move narrations, audio, movie-images, and output artifacts for one journal date into
    ``archive/reset_<date_id>_<timestamp>/``. Blocks while a pipeline subprocess is running.
    """
    srv = _srv()
    repo_root = srv._REPO_ROOT
    b = body if isinstance(body, dict) else {}
    with srv._RUN_LOCK:
        if srv._CURRENT_PROC is not None and srv._CURRENT_PROC.poll() is None:
            return {
                "ok": False,
                "error": "run_in_progress",
                "message": "A pipeline run is already in progress.",
            }
    jd_raw = b.get("journal_date")
    if not isinstance(jd_raw, str) or not jd_raw.strip():
        return {"ok": False, "error": "missing_journal_date"}
    safe = _safe_journal_date(jd_raw.strip())
    if not safe:
        return {"ok": False, "error": "invalid_journal_date"}
    did = _journal_date_to_date_id(safe)
    if not did:
        return {"ok": False, "error": "invalid_date_id"}

    dry_run = _boolish(b.get("dry_run"), False)
    result = archive_day_artifacts(repo_root, did, dry_run=dry_run)
    out = result.to_dict()
    out["journal_date"] = safe
    if not dry_run and result.ok and result.moved:
        prior = revert_last_run_date_if_matches(repo_root, safe)
        out["last_date_after"] = prior
    if result.errors:
        out["ok"] = False
        out["error"] = "reset_failed"
    return out


def regenerate_video_segment_run(body: dict[str, Any]) -> dict[str, Any]:
    """
    Run ``run-daily.py`` with ``--regenerate-video-segments`` for one 1-based segment index.
    Uses the same subprocess lock as POST /api/run. Vendor must be ``fal`` or ``google``.
    """
    srv = _srv()
    repo_root = srv._REPO_ROOT
    b = body if isinstance(body, dict) else {}
    jd_raw = b.get("journal_date")
    if not isinstance(jd_raw, str) or not jd_raw.strip():
        return {"ok": False, "error": "missing_journal_date"}
    safe = _safe_journal_date(jd_raw.strip())
    if not safe:
        return {"ok": False, "error": "invalid_journal_date"}
    did = _journal_date_to_date_id(safe)
    if not did:
        return {"ok": False, "error": "invalid_date_id"}

    seg_raw = b.get("segment_index", 1)
    try:
        seg_i = int(seg_raw)
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad_segment_index"}
    if seg_i < 1:
        return {"ok": False, "error": "bad_segment_index"}

    narr_path = repo_root / "narrations" / f"narration{did}.json"
    if not narr_path.is_file():
        return {"ok": False, "error": "no_narration"}
    try:
        nar = json.loads(narr_path.read_text(encoding="utf-8"))
        n_script = len(nar.get("narration_script") or [])
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": "narration_read_failed", "message": str(e)}
    if n_script < 1:
        return {"ok": False, "error": "no_segments"}
    if seg_i > n_script:
        return {"ok": False, "error": "segment_out_of_range", "n_segments": n_script}

    vendor = str(b.get("vendor") or "fal").strip().lower()
    if vendor not in ("fal", "google"):
        return {
            "ok": False,
            "error": "unsupported_vendor",
            "message": "Per-segment regen supports fal and google only (not Sora).",
        }

    wc = b.get("wide_screen")
    if isinstance(wc, str):
        wide_screen = wc.strip().lower() in ("1", "true", "yes", "on")
    else:
        wide_screen = bool(wc) if wc is not None else False

    spec = load_options()
    if spec.get("error"):
        return {"ok": False, "error": "options_load_failed", "message": str(spec.get("error"))}
    backend = spec.get("backend") or {}
    script = backend.get("script", "run-daily.py")
    py = _resolve_python(
        os.environ.get("PIPELINE_UI_PYTHON"),
        list(backend.get("python_candidates") or [".venv/Scripts/python.exe", ".venv/bin/python"]),
    )
    script_path = repo_root / script
    if not script_path.is_file():
        return {"ok": False, "error": "missing_script", "message": str(script_path)}

    cmd = [
        py,
        str(script_path),
        "--date",
        safe,
        "--vendor",
        vendor,
        "--regenerate-video-segments",
        str(seg_i),
    ]
    if wide_screen:
        cmd.append("--wide-screen")
    cmd.append("--skip-existing")
    cmd.append("--force-assembly")

    log_payload: dict[str, Any] = {
        "inputs": {
            "journal_date": safe,
            "date_id": did,
            "segment_index": seg_i,
            "vendor": vendor,
            "wide_screen": wide_screen,
            "action": "regenerate_video_segment",
        },
        "source": "pipeline_ui POST /api/regenerate-video-segment",
    }

    run = srv._run_locked_pipeline_cmd(cmd, log_payload)
    if run.get("_blocked"):
        return {
            "ok": False,
            "error": "run_in_progress",
            "message": run.get("error", "A pipeline run is already in progress."),
        }
    ok_exit = run["returncode"] == 0 or run.get("cancelled", False)
    out: dict[str, Any] = {
        "ok": ok_exit,
        "command": run["command"],
        "cwd": run["cwd"],
        "returncode": run["returncode"],
        "stdout": run["stdout"],
        "stderr": run["stderr"],
        "cancelled": run.get("cancelled", False),
    }
    if not ok_exit and not run.get("cancelled"):
        out["error"] = "pipeline_failed"
    return out


def _video_vendor_for_date_id(date_id: str) -> str:
    """Best-effort vendor from movie-images run_report.json (defaults fal)."""
    rr = _srv()._REPO_ROOT / "movie-images" / date_id / "run_report.json"
    if rr.is_file():
        try:
            data = json.loads(rr.read_text(encoding="utf-8"))
            v = data.get("vendor")
            if isinstance(v, str) and v.strip():
                return v.strip()
        except (json.JSONDecodeError, OSError):
            pass
    return "fal"


def write_assembly_manifest_for_date_id(
    date_id: str,
    *,
    wide_screen: bool = False,
    narration_vendor: str = "openai",
    tts_vendor: str = "openai",
    video_vendor: str | None = None,
) -> dict[str, Any]:
    """
    Write output/*_<date_id>_video.manifest.json after UI assembly (videos-mp3-to-movie).
    Matches run-daily manifest fields; safe to call when manifest already exists (overwrites metadata).
    """
    repo_root = _srv()._REPO_ROOT
    if not re.fullmatch(r"\d{8}", date_id):
        return {"ok": False, "error": "invalid_date_id"}
    vpath = _output_video_path_for_date_id(date_id)
    if not vpath or not vpath.is_file():
        return {
            "ok": False,
            "error": "no_video",
            "message": f"No output/*_{date_id}_video.mp4",
        }
    vv = video_vendor or _video_vendor_for_date_id(date_id)
    aspect_ratio = "16:9" if wide_screen else "9:16"
    manifest_path = video_manifest.write_pipeline_manifest(
        vpath,
        date_id=date_id,
        aspect_ratio=aspect_ratio,
        narration_vendor=narration_vendor,
        tts_vendor=tts_vendor,
        video_vendor=vv,
        repo_root=repo_root,
    )
    length_seconds = (video_manifest.load(manifest_path) or {}).get("length_seconds")
    try:
        rel = manifest_path.relative_to(repo_root).as_posix()
    except ValueError:
        rel = str(manifest_path)
    return {
        "ok": True,
        "manifest_path": rel,
        "output_video": vpath.name,
        "aspect_ratio": aspect_ratio,
        "length_seconds": length_seconds,
    }


def assemble_video_run(body: dict[str, Any]) -> dict[str, Any]:
    """
    Remux ``output/*_<date>_video.mp4`` from existing segment clips + audio (videos-mp3-to-movie only).
    Does not regenerate narration, TTS, or per-segment video.
    """
    from pipeline.output_naming import DEFAULT_VIDEO_OUTPUT_PREFIX

    srv = _srv()
    repo_root = srv._REPO_ROOT
    b = body if isinstance(body, dict) else {}
    jd_raw = b.get("journal_date")
    if not isinstance(jd_raw, str) or not jd_raw.strip():
        return {"ok": False, "error": "missing_journal_date"}
    safe = _safe_journal_date(jd_raw.strip())
    if not safe:
        return {"ok": False, "error": "invalid_journal_date"}
    did = _journal_date_to_date_id(safe)
    if not did:
        return {"ok": False, "error": "invalid_date_id"}

    durations_path = repo_root / "audio" / did / "durations.json"
    if not durations_path.is_file():
        return {
            "ok": False,
            "error": "no_audio_timeline",
            "message": f"Missing {durations_path.relative_to(repo_root)}",
        }

    wc = b.get("wide_screen")
    if isinstance(wc, str):
        wide_screen = wc.strip().lower() in ("1", "true", "yes", "on")
    else:
        wide_screen = bool(wc) if wc is not None else False

    ambient = False
    if "ambient" in b:
        ambient = _boolish(b.get("ambient"), False)

    spec = load_options()
    if spec.get("error"):
        return {"ok": False, "error": "options_load_failed", "message": str(spec.get("error"))}
    py = _resolve_python(
        os.environ.get("PIPELINE_UI_PYTHON"),
        list(
            spec.get("backend", {}).get("python_candidates")
            or [".venv/Scripts/python.exe", ".venv/bin/python"]
        ),
    )
    movie_script = repo_root / "videos-mp3-to-movie.py"
    if not movie_script.is_file():
        return {"ok": False, "error": "missing_script", "message": str(movie_script)}

    cmd = [
        py,
        str(movie_script),
        did,
        "--output-prefix",
        DEFAULT_VIDEO_OUTPUT_PREFIX,
    ]
    if not wide_screen:
        cmd.append("--shorts")
    if ambient:
        cmd.append("--ambient")

    log_payload: dict[str, Any] = {
        "inputs": {
            "journal_date": safe,
            "date_id": did,
            "wide_screen": wide_screen,
            "ambient": ambient,
            "action": "assemble_video",
        },
        "source": "pipeline_ui POST /api/assemble-video",
    }
    run = srv._run_locked_pipeline_cmd(cmd, log_payload)
    if run.get("_blocked"):
        return {
            "ok": False,
            "error": "run_in_progress",
            "message": run.get("error", "A pipeline run is already in progress."),
        }
    ok_exit = run["returncode"] == 0 or run.get("cancelled", False)
    out: dict[str, Any] = {
        "ok": ok_exit,
        "command": run["command"],
        "cwd": run["cwd"],
        "returncode": run["returncode"],
        "stdout": run["stdout"],
        "stderr": run["stderr"],
        "cancelled": run.get("cancelled", False),
    }
    if ok_exit and not run.get("cancelled"):
        man = write_assembly_manifest_for_date_id(
            did,
            wide_screen=wide_screen,
        )
        out["manifest"] = man
        if not man.get("ok"):
            out["manifest_warning"] = man.get("message") or man.get("error")
    if not ok_exit and not run.get("cancelled"):
        out["error"] = "pipeline_failed"
    return out
