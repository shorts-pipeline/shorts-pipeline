"""YouTube upload-status view payload and upload-from-preview action for pipeline_ui.

Split out of ``server.py`` (see ``ai-plans/pipeline-ui-server-split.md``, step 3).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import video_manifest
from pipeline.output_naming import date_id_from_output_video_stem
from pipeline.youtube_metadata import youtube_title_and_description
from pipeline_ui.paths import (
    _date_id_from_output_video,
    _journal_date_to_date_id,
    _safe_journal_date,
    latest_video_path,
)
from pipeline_ui.runtime import srv as _srv


def _output_video_path_for_date_id(date_id: str) -> Path | None:
    out = _srv()._REPO_ROOT / "output"
    if not re.fullmatch(r"\d{8}", date_id):
        return None
    matches = sorted(out.glob(f"*_{date_id}_video.mp4"))
    for p in matches:
        r = p.resolve()
        if r.is_file() and date_id_from_output_video_stem(r.stem) == date_id:
            return r
    return None


def _manifest_paths_for_youtube_upload_status(*, scan_archive: bool) -> list[Path]:
    paths: list[Path] = []
    out_dir = _srv()._REPO_ROOT / "output"
    if out_dir.is_dir():
        paths.extend(sorted(out_dir.glob("*.manifest.json")))
    if scan_archive:
        arch = _srv()._REPO_ROOT / "archive"
        if arch.is_dir():
            paths.extend(sorted(arch.glob("*/output/*.manifest.json")))
    return paths


def _date_id_to_journal_date(date_id: str) -> str | None:
    if not re.fullmatch(r"\d{8}", date_id):
        return None
    return f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:8]}"


def youtube_upload_status_for_ui(*, scan_archive: bool = True) -> dict[str, Any]:
    """
    Summarize YouTube uploads from manifests (youtube_video_id).

    output/ manifests win over archive/ when the same date_id appears in both.
    ``pending`` counts assembled videos in output/ with a manifest but no upload yet.
    """
    seen_uploaded: set[str] = set()
    uploaded_date_ids: list[str] = []
    for path in _manifest_paths_for_youtube_upload_status(scan_archive=scan_archive):
        data = video_manifest.load(path)
        if not data:
            continue
        date_id = data.get("date_id")
        video_id = data.get("youtube_video_id")
        if not (date_id and video_id):
            continue
        date_id = str(date_id)
        if not re.fullmatch(r"\d{8}", date_id):
            continue
        if date_id in seen_uploaded:
            continue
        seen_uploaded.add(date_id)
        uploaded_date_ids.append(date_id)
    uploaded_date_ids.sort()

    last_date_id = uploaded_date_ids[-1] if uploaded_date_ids else None
    last_journal_date = _date_id_to_journal_date(last_date_id) if last_date_id else None

    pending_date_ids: list[str] = []
    seen_pending: set[str] = set()
    out_dir = _srv()._REPO_ROOT / "output"
    if out_dir.is_dir():
        for mpath in sorted(out_dir.glob("*.manifest.json")):
            data = video_manifest.load(mpath)
            if not data or data.get("youtube_video_id"):
                continue
            date_id = data.get("date_id")
            if not date_id:
                continue
            date_id = str(date_id)
            if not re.fullmatch(r"\d{8}", date_id):
                continue
            if date_id in seen_uploaded or date_id in seen_pending:
                continue
            vpath = _output_video_path_for_date_id(date_id)
            if not vpath or not vpath.is_file():
                continue
            seen_pending.add(date_id)
            pending_date_ids.append(date_id)
    pending_date_ids.sort()

    oldest_pending_date_id = pending_date_ids[0] if pending_date_ids else None
    oldest_pending_journal_date = (
        _date_id_to_journal_date(oldest_pending_date_id) if oldest_pending_date_id else None
    )

    return {
        "ok": True,
        "scan_archive": scan_archive,
        "uploaded_count": len(uploaded_date_ids),
        "last_date_id": last_date_id,
        "last_journal_date": last_journal_date,
        "pending_count": len(pending_date_ids),
        "oldest_pending_date_id": oldest_pending_date_id,
        "oldest_pending_journal_date": oldest_pending_journal_date,
    }


def _youtube_title_and_description_for_date_id(date_id: str) -> tuple[str, str]:
    """Match run-daily.py YouTube upload metadata for a narration date_id."""
    return youtube_title_and_description(date_id, repo_root=_srv()._REPO_ROOT)


def youtube_upload_from_preview(
    privacy: str,
    journal_date: str | None = None,
    *,
    force: bool = False,
    allow_gap: bool = False,
) -> dict[str, Any]:
    """Run youtube_upload.py on output video with manifest (latest shortcut or explicit journal_date).

    Refuses (unless the matching override is passed) when the episode is already
    uploaded (``force``) or earlier episodes are still pending (``allow_gap``).
    """
    privacy = (privacy or "public").strip().lower()
    if privacy not in ("public", "unlisted", "private"):
        privacy = "public"

    vpath: Path | None = None
    if journal_date:
        safe = _safe_journal_date(journal_date.strip())
        if not safe:
            return {
                "ok": False,
                "error": "invalid_date",
                "message": "Invalid journal_date (use YYYY-MM-DD).",
            }
        did = _journal_date_to_date_id(safe)
        if not did:
            return {"ok": False, "error": "invalid_date", "message": "Could not derive date_id."}
        vpath = _output_video_path_for_date_id(did)
        if not vpath:
            return {
                "ok": False,
                "error": "no_video",
                "message": f"No output video for {safe} (expected output/*_{did}_video.mp4).",
            }
    else:
        vpath = latest_video_path()
        if not vpath:
            return {"ok": False, "error": "no_video", "message": "No latest_video.url target."}
    vpath = vpath.resolve()
    root = _srv()._REPO_ROOT.resolve()
    try:
        rel_video = vpath.relative_to(root)
    except ValueError:
        return {"ok": False, "error": "invalid_path", "message": "Video path escapes repo."}
    if not vpath.is_file():
        return {
            "ok": False,
            "error": "no_video",
            "message": f"Missing file: {rel_video.as_posix()}",
        }

    manifest_path = video_manifest.manifest_path_for_video(vpath)
    try:
        rel_man = manifest_path.relative_to(root)
    except ValueError:
        return {"ok": False, "error": "invalid_path", "message": "Manifest path escapes repo."}
    if not manifest_path.is_file():
        return {
            "ok": False,
            "error": "no_manifest",
            "message": f"No manifest at {rel_man.as_posix()}. Run assembly (run-daily) so the pipeline writes it.",
        }

    date_id = _date_id_from_output_video(vpath)
    if not date_id:
        return {
            "ok": False,
            "error": "bad_video_name",
            "message": "Could not parse date_id from video filename.",
        }

    from pipeline.youtube_upload_guard import (
        DuplicateUploadError,
        UploadSequenceError,
        check_upload_allowed,
    )

    try:
        check_upload_allowed(
            vpath,
            manifest_path,
            repo_root=root,
            force=force,
            allow_gap=allow_gap,
        )
    except DuplicateUploadError as e:
        return {
            "ok": False,
            "error": "already_uploaded",
            "message": str(e),
            "youtube_video_id": e.video_id,
        }
    except UploadSequenceError as e:
        return {
            "ok": False,
            "error": "upload_sequence",
            "message": str(e),
            "missing_date_ids": e.missing,
        }

    title, description = _youtube_title_and_description_for_date_id(date_id)
    upload_py = _srv()._REPO_ROOT / "youtube_upload.py"
    cmd: list[str] = [
        sys.executable,
        str(upload_py),
        str(rel_video.as_posix()),
        "--manifest",
        str(rel_man.as_posix()),
        "--privacy",
        privacy,
        "--title",
        title,
        "--description",
        description,
        "-T",
        "Lewis and Clark",
        "expedition",
        "history",
        "journal",
    ]
    if force:
        cmd.append("--force")
    if allow_gap:
        cmd.append("--allow-gap")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=7200,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "message": "Upload timed out (2h limit)."}

    out: dict[str, Any] = {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + "\n" + (proc.stdout or ""))[-4000:]
        out["error"] = "upload_failed"
        out["message"] = tail.strip() or "youtube_upload.py failed"
        return out

    youtube_url = ""
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if "Uploaded:" in line and "http" in line:
            idx = line.find("http")
            if idx >= 0:
                youtube_url = line[idx:].split()[0] if line[idx:].split() else line[idx:]
                break
    out["youtube_url"] = youtube_url
    out["message"] = (
        "Upload complete." if youtube_url else "Upload finished (could not parse URL from stdout)."
    )
    return out
