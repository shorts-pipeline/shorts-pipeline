"""Journal-date and repo-path helpers, plus run-report / episode-state payload
builders for the pipeline_ui HTTP API.

Split out of ``server.py`` (see ``ai-plans/pipeline-ui-server-split.md``, step 3).
Shared repo-root-relative paths (``_REPO_ROOT``, ``_LATEST_NARRATION_URL``, ...)
are read from ``pipeline_ui.server`` via ``_srv()`` at call time (lazy import)
rather than imported as bare names, so that tests monkeypatching
``pipeline_ui.server._REPO_ROOT`` still redirect these functions (see the plan
doc's "Risks" section) and so ``server`` can import this module without a cycle.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from pipeline.output_naming import date_id_from_output_video_stem
from pipeline.run_report import load_run_report
from pipeline_ui.runtime import srv as _srv

# Journal XML files use YYYY-MM-DD; expedition spans these years (picker UI starts at 1804).
_JOURNAL_YEAR_MIN = 1803
_JOURNAL_YEAR_MAX = 1806


def _safe_journal_date(date_part: str) -> str | None:
    """Return normalized YYYY-MM-DD if valid and in expedition year range, else None."""
    s = unquote((date_part or "").strip())
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return None
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    if not (_JOURNAL_YEAR_MIN <= dt.year <= _JOURNAL_YEAR_MAX):
        return None
    return s


def _normalize_journal_date_input(raw: str) -> str | None:
    """Accept YYYY-MM-DD or YYYYMMDD; return canonical YYYY-MM-DD or None."""
    s = (raw or "").strip()
    if not s:
        return None
    hit = _safe_journal_date(s)
    if hit:
        return hit
    if re.fullmatch(r"\d{8}", s):
        try:
            datetime.strptime(s, "%Y%m%d")
        except ValueError:
            return None
        iso = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        return _safe_journal_date(iso)
    return None


def journal_entry_payload(date_str: str) -> dict[str, Any]:
    """Load journal-entries/<date>.xml for a validated date. Returns JSON-serializable dict."""
    safe = _safe_journal_date(date_str)
    if not safe:
        return {
            "ok": False,
            "error": "invalid_date",
            "message": "Use YYYY-MM-DD within expedition years.",
        }
    rel = Path("journal-entries") / f"{safe}.xml"
    path = (_srv()._REPO_ROOT / rel).resolve()
    try:
        path.relative_to(_srv()._REPO_ROOT.resolve())
    except ValueError:
        return {"ok": False, "error": "invalid_path"}
    if not path.is_file():
        return {"ok": False, "error": "not_found", "date": safe, "path": str(rel)}
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "error": "read_error", "message": str(e)}
    return {
        "ok": True,
        "date": safe,
        "path": str(rel),
        "content": content,
    }


def _resolve_python(explicit: str | None, candidates: list[str]) -> str:
    if explicit:
        p = Path(explicit)
        if p.is_absolute():
            return str(p) if p.exists() else sys.executable
        cand = _srv()._REPO_ROOT / explicit
        return str(cand) if cand.exists() else sys.executable
    for rel in candidates:
        p = _srv()._REPO_ROOT / rel
        if p.exists():
            return str(p)
    return sys.executable


def _url_from_internet_shortcut(path: Path) -> str | None:
    """Parse URL= line from a Windows .url InternetShortcut file."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        s = line.strip()
        if s.upper().startswith("URL="):
            return s[4:].strip()
    return None


def _file_href_basename(href: str, fallback: str) -> str:
    """Best-effort filename label for file:// hrefs (handles spaces via unquote)."""
    try:
        u = urlparse(href)
        if u.scheme == "file":
            path = unquote(u.path or "")
            if len(path) >= 3 and path[0] == "/" and path[2] == ":":
                path = path[1:]
            name = Path(path).name
            return name if name else fallback
    except Exception:
        pass
    raw = href.replace("file:///", "").replace("file://", "")
    name = Path(unquote(raw)).name
    return name if name else fallback


def _path_from_file_href(href: str) -> Path | None:
    """Resolve a file:// href to a Path; only allow files under the repo root."""
    if not href:
        return None
    u = urlparse(href)
    if u.scheme != "file":
        return None
    path = unquote(u.path or "")
    if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    p = Path(path)
    try:
        p = p.resolve(strict=False)
    except OSError:
        return None
    root = _srv()._REPO_ROOT.resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    if not p.is_file():
        return None
    return p


def latest_narration_path() -> Path | None:
    href = _url_from_internet_shortcut(_srv()._LATEST_NARRATION_URL)
    return _path_from_file_href(href) if href else None


def latest_video_path() -> Path | None:
    href = _url_from_internet_shortcut(_srv()._LATEST_VIDEO_URL)
    return _path_from_file_href(href) if href else None


def _date_id_from_output_video(path: Path) -> str | None:
    """Parse YYYYMMDD from <prefix>_<id>_video.mp4 or Sora video_<id>.mp4."""
    return date_id_from_output_video_stem(path.stem)


def suggested_journal_date_for_ui() -> str | None:
    """
    YYYY-MM-DD to pre-fill the pipeline UI journal picker: the chronologically latest
    among last successful run (state), ``latest_narration.url``, and ``latest_video.url``.

    State alone can lag behind the narration shortcut (e.g. narration regenerated without
    a full run-daily that updates ``last_date``); taking the max avoids defaulting one day
    behind the newest narration file.
    """
    candidates: list[str] = []
    state_path = _srv()._REPO_ROOT / "state" / "run_daily_state.json"
    if state_path.is_file():
        try:
            st = json.loads(state_path.read_text(encoding="utf-8"))
            last = st.get("last_date")
            if isinstance(last, str):
                safe = _safe_journal_date(last)
                if safe:
                    candidates.append(safe)
        except (json.JSONDecodeError, OSError):
            pass
    narr_path = latest_narration_path()
    if narr_path:
        m = re.fullmatch(r"narration(\d{8})\.json", narr_path.name, flags=re.IGNORECASE)
        if m:
            did = m.group(1)
            ds = f"{did[:4]}-{did[4:6]}-{did[6:8]}"
            safe = _safe_journal_date(ds)
            if safe:
                candidates.append(safe)
    vid_path = latest_video_path()
    if vid_path:
        did = _date_id_from_output_video(vid_path.resolve())
        if did:
            ds = f"{did[:4]}-{did[4:6]}-{did[6:8]}"
            safe = _safe_journal_date(ds)
            if safe:
                candidates.append(safe)
    if not candidates:
        return None
    return max(candidates)


def _narrative_segment_num_from_duration_file(file_field: str) -> int | None:
    """e.g. segments/01.mp3 -> 1; intro -> None."""
    s = (file_field or "").replace("\\", "/").strip()
    if not s or s == "intro":
        return None
    m = re.search(r"(\d+)\.mp[0-9a-z]*$", s, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _journal_date_to_date_id(journal_date: str) -> str | None:
    safe = _safe_journal_date(journal_date)
    if not safe:
        return None
    return safe.replace("-", "")


def run_report_http_payload(journal_date: str) -> dict[str, Any]:
    """JSON for UI: last FAL ``movie-images/<date_id>/run_report.json`` for a journal date."""
    safe = _safe_journal_date((journal_date or "").strip())
    if not safe:
        return {"ok": False, "error": "invalid_journal_date"}
    date_id = _journal_date_to_date_id(safe)
    if not date_id:
        return {"ok": False, "error": "invalid_date_id"}
    out_dir = _srv()._REPO_ROOT / "movie-images" / date_id
    data = load_run_report(out_dir)
    if not data:
        return {
            "ok": False,
            "error": "no_report",
            "date_id": date_id,
            "journal_date": safe,
            "message": "No run_report.json yet; run narration-to-video with vendor fal for this date.",
        }
    return {"ok": True, "date_id": date_id, "journal_date": safe, "report": data}


def episode_state_http_payload(journal_date: str, *, refresh: bool = True) -> dict[str, Any]:
    """JSON for UI: pipeline readiness + ``narrations/narration<DATE>_state.json`` sidecar."""
    safe = _safe_journal_date((journal_date or "").strip())
    if not safe:
        return {"ok": False, "error": "invalid_journal_date"}
    date_id = _journal_date_to_date_id(safe)
    if not date_id:
        return {"ok": False, "error": "invalid_journal_date", "journal_date": safe}

    from pipeline.episode_state import (
        build_episode_state,
        episode_state_sidecar_path,
        refresh_episode_state_sidecar,
    )

    narr_path = _srv()._REPO_ROOT / "narrations" / f"narration{date_id}.json"
    narration: dict[str, Any] | None = None
    if narr_path.is_file():
        try:
            raw = json.loads(narr_path.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                narration = raw
        except (json.JSONDecodeError, OSError):
            narration = None

    if refresh:
        state = refresh_episode_state_sidecar(
            _srv()._REPO_ROOT,
            date_id,
            narration=narration,
            source="pipeline_ui",
        )
    else:
        state = build_episode_state(
            _srv()._REPO_ROOT,
            date_id,
            narration=narration,
            source="pipeline_ui_scan",
        )

    sidecar = episode_state_sidecar_path(_srv()._REPO_ROOT, date_id)
    return {
        "ok": True,
        "date_id": date_id,
        "journal_date": safe,
        "sidecar_rel": (
            str(sidecar.relative_to(_srv()._REPO_ROOT)).replace("\\", "/")
            if sidecar.is_file()
            else ""
        ),
        "state": state,
    }
