"""Latest-links and Library-tab payload builders for pipeline_ui (portraits,
prompt packs, episode-diversity catalog) plus static-asset path safety helpers.

Split out of ``server.py`` (see ``ai-plans/pipeline-ui-server-split.md``, step 3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.prompt_pack_paths import packs_root
from pipeline_ui.paths import (
    _file_href_basename,
    _url_from_internet_shortcut,
    suggested_journal_date_for_ui,
)
from pipeline_ui.runtime import srv as _srv


def load_latest_links() -> dict[str, Any]:
    """Targets from repo-root latest_narration.url / latest_video.url (written by pipeline)."""
    nar_href = _url_from_internet_shortcut(_srv()._LATEST_NARRATION_URL)
    vid_href = _url_from_internet_shortcut(_srv()._LATEST_VIDEO_URL)
    out: dict[str, Any] = {
        "narration": None,
        "video": None,
    }
    if nar_href:
        label = _file_href_basename(nar_href, "narration JSON")
        out["narration"] = {
            "href": nar_href,
            "label": label,
            "preview_path": "/api/preview/narration",
        }
    if vid_href:
        label = _file_href_basename(vid_href, "video")
        out["video"] = {
            "href": vid_href,
            "label": label,
            "preview_path": "/api/preview/video",
        }
    sug = suggested_journal_date_for_ui()
    if sug:
        out["suggested_journal_date"] = sug
    return out


_CHARACTER_PORTRAITS_DIR = _srv()._REPO_ROOT / "character-portraits"
_VOICE_PREVIEWS_DIR = _CHARACTER_PORTRAITS_DIR / "voice_previews"
_PROMPT_PACKS_DIR = packs_root(_srv()._REPO_ROOT)
# Image extensions surfaced in the Library tab portraits grid.
_PORTRAIT_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
# Pipeline portrait resolution order (matches pipeline.narration_characters.storage).
_PIPELINE_PORTRAIT_EXTS = (".png", ".jpg", ".jpeg")
# Voice preview audio extensions surfaced by /api/library/voice-preview/<file>.
_VOICE_PREVIEW_EXTS = (".mp3", ".m4a", ".wav", ".ogg")


def _safe_portrait_filename(name: str) -> Path | None:
    """Resolve a portrait filename inside character-portraits/ (top-level only, no subdirs)."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    p = (_CHARACTER_PORTRAITS_DIR / name).resolve()
    try:
        root = _CHARACTER_PORTRAITS_DIR.resolve()
    except OSError:
        return None
    if not str(p).startswith(str(root)):
        return None
    if not p.is_file() or p.parent != root:
        return None
    if p.suffix.lower() not in _PORTRAIT_IMAGE_EXTS:
        return None
    return p


def library_portraits_payload() -> dict[str, Any]:
    """List PNG character portraits at the top level of character-portraits/.

    Returns a JSON-friendly payload of {ok, portraits: [{filename, ext, size_bytes}], dir}.
    Only top-level files (no subdirectories like voice_backups/) are included.
    """
    if not _CHARACTER_PORTRAITS_DIR.is_dir():
        return {"ok": False, "error": "missing_directory", "portraits": []}
    rows: list[dict[str, Any]] = []
    for child in sorted(_CHARACTER_PORTRAITS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_file():
            continue
        if child.name.startswith("."):
            continue
        ext = child.suffix.lower()
        if ext not in _PORTRAIT_IMAGE_EXTS:
            continue
        try:
            size_bytes = child.stat().st_size
        except OSError:
            size_bytes = 0
        rows.append(
            {
                "filename": child.name,
                "stem": child.stem,
                "ext": ext,
                "size_bytes": size_bytes,
            }
        )
    return {
        "ok": True,
        "dir": "character-portraits",
        "portraits": rows,
    }


def _resolve_pipeline_portrait_filename(stem: str) -> str | None:
    """Resolve the first existing `<stem><ext>` in character-portraits/ using pipeline order."""
    s = (stem or "").strip()
    if not s:
        return None
    for ext in _PIPELINE_PORTRAIT_EXTS:
        cand = _CHARACTER_PORTRAITS_DIR / f"{s}{ext}"
        if cand.is_file():
            return cand.name
    return None


def _resolve_voice_preview_filename(character_id: str) -> str | None:
    """Resolve `voice_previews/<id>_preview.<ext>` for a character id."""
    cid = (character_id or "").strip()
    if not cid:
        return None
    if not _VOICE_PREVIEWS_DIR.is_dir():
        return None
    for ext in _VOICE_PREVIEW_EXTS:
        cand = _VOICE_PREVIEWS_DIR / f"{cid}_preview{ext}"
        if cand.is_file():
            return cand.name
    return None


_STATIC_ALLOWED_EXTS = {
    ".css",
    ".js",
    ".mjs",
    ".map",
    ".json",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".woff",
    ".woff2",
    ".ico",
}
_STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ico": "image/x-icon",
}


def _safe_static_path(rel: str) -> Path | None:
    """Resolve a relative path under pipeline_ui/static/ with traversal protection.

    Allows nested directories (e.g. ``js/library.js``) but rejects ``..`` segments,
    absolute paths, hidden files, and any extension not in the allowlist.
    """
    if not rel:
        return None
    cleaned = rel.replace("\\", "/").lstrip("/")
    if ".." in cleaned.split("/"):
        return None
    static_dir = _srv()._STATIC_DIR
    if not static_dir.is_dir():
        return None
    p = (static_dir / cleaned).resolve()
    try:
        root = static_dir.resolve()
    except OSError:
        return None
    try:
        p.relative_to(root)
    except ValueError:
        return None
    if not p.is_file():
        return None
    if any(part.startswith(".") for part in p.relative_to(root).parts):
        return None
    if p.suffix.lower() not in _STATIC_ALLOWED_EXTS:
        return None
    return p


def _safe_voice_preview_filename(name: str) -> Path | None:
    """Resolve a voice preview filename inside voice_previews/ (top-level only, no subdirs)."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    if not _VOICE_PREVIEWS_DIR.is_dir():
        return None
    p = (_VOICE_PREVIEWS_DIR / name).resolve()
    try:
        root = _VOICE_PREVIEWS_DIR.resolve()
    except OSError:
        return None
    if not str(p).startswith(str(root)):
        return None
    if not p.is_file() or p.parent != root:
        return None
    if p.suffix.lower() not in _VOICE_PREVIEW_EXTS:
        return None
    return p


def library_pipeline_portraits_payload() -> dict[str, Any]:
    """Return only the portraits the pipeline actively resolves.

    Order:
      1. ``pair_portraits[]`` composites (e.g. ``lewis_clark``, ``clark_york``…)
      2. ``people[]`` individuals (in config order)

    Each entry: ``{id, kind, label, filename, ext, size_bytes, member_ids?, roles?,
    description?}`` — only includes ids whose ``<id>.{png,jpg,jpeg}`` exists. Composites
    with missing files are reported under ``missing_composites`` for diagnostics.
    """
    from pipeline.narration_characters.paths import CHAR_PATH

    if not _CHARACTER_PORTRAITS_DIR.is_dir():
        return {"ok": False, "error": "missing_portraits_dir", "portraits": []}
    if not CHAR_PATH.is_file():
        return {"ok": False, "error": "missing_characters_config", "portraits": []}
    try:
        raw = json.loads(CHAR_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {
            "ok": False,
            "error": "characters_config_invalid",
            "message": str(e),
            "portraits": [],
        }

    people_by_id: dict[str, dict[str, Any]] = {}
    people_order: list[str] = []
    for row in raw.get("people") or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "").strip()
        if not pid or pid in people_by_id:
            continue
        people_by_id[pid] = row
        people_order.append(pid)

    portraits: list[dict[str, Any]] = []
    missing_composites: list[str] = []
    missing_people: list[str] = []
    seen_ids: set[str] = set()

    for row in raw.get("pair_portraits") or []:
        if not isinstance(row, dict):
            continue
        comp = str(row.get("composite_id") or "").strip()
        if not comp or comp in seen_ids:
            continue
        seen_ids.add(comp)
        member_ids_raw = row.get("member_ids") or []
        members: list[str] = []
        if isinstance(member_ids_raw, list):
            members = [str(x).strip() for x in member_ids_raw if str(x).strip()]
        filename = _resolve_pipeline_portrait_filename(comp)
        if not filename:
            missing_composites.append(comp)
            continue
        p = _CHARACTER_PORTRAITS_DIR / filename
        try:
            size_bytes = p.stat().st_size
        except OSError:
            size_bytes = 0
        member_names: list[str] = []
        for mid in members:
            mp = people_by_id.get(mid)
            if mp and mp.get("name"):
                member_names.append(str(mp.get("name")))
            else:
                member_names.append(mid)
        label = " + ".join(member_names) if member_names else comp
        portraits.append(
            {
                "id": comp,
                "kind": "pair",
                "label": label,
                "filename": filename,
                "ext": Path(filename).suffix.lower(),
                "size_bytes": size_bytes,
                "member_ids": members,
            }
        )

    for pid in people_order:
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        row = people_by_id[pid]
        filename = _resolve_pipeline_portrait_filename(pid)
        if not filename:
            missing_people.append(pid)
            continue
        p = _CHARACTER_PORTRAITS_DIR / filename
        try:
            size_bytes = p.stat().st_size
        except OSError:
            size_bytes = 0
        name = str(row.get("name") or pid)
        roles_raw = row.get("roles") or []
        roles: list[str] = []
        if isinstance(roles_raw, list):
            roles = [str(r).strip() for r in roles_raw if str(r).strip()]
        desc = str(row.get("physical_description") or "").strip()
        voice_preview = _resolve_voice_preview_filename(pid)
        portraits.append(
            {
                "id": pid,
                "kind": "person",
                "label": name,
                "filename": filename,
                "ext": Path(filename).suffix.lower(),
                "size_bytes": size_bytes,
                "roles": roles,
                "description": desc,
                "voice_preview": voice_preview,
            }
        )

    return {
        "ok": True,
        "dir": "character-portraits",
        "config": "config/narration_characters.json",
        "portraits": portraits,
        "missing_composites": missing_composites,
        "missing_people": missing_people,
    }


def library_prompt_packs_payload() -> dict[str, Any]:
    """Enumerate prompt_packs/* directories with pack.json (if any) and .txt file contents.

    Each pack entry: {id, path, pack_json: object|null, pack_json_error: str|null, files: [{name, kind, text, size_bytes}]}.
    Files include .txt (and pack.json kept separately if valid JSON, else surfaced under pack_json_error).
    """
    if not _PROMPT_PACKS_DIR.is_dir():
        return {"ok": False, "error": "missing_directory", "packs": []}
    packs: list[dict[str, Any]] = []
    for pack_dir in sorted(_PROMPT_PACKS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not pack_dir.is_dir():
            continue
        if pack_dir.name.startswith("."):
            continue
        pack_json_obj: Any = None
        pack_json_err: str | None = None
        pack_json_path = pack_dir / "pack.json"
        if pack_json_path.is_file():
            try:
                pack_json_obj = json.loads(pack_json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                pack_json_err = str(e)
        files: list[dict[str, Any]] = []
        for child in sorted(pack_dir.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_file():
                continue
            if child.name.startswith(".") or child.name.lower() == "pack.json":
                continue
            ext = child.suffix.lower()
            kind = "text" if ext in (".txt", ".md") else "other"
            text = ""
            if kind == "text":
                try:
                    text = child.read_text(encoding="utf-8")
                except OSError as e:
                    text = f"[read error: {e}]"
            try:
                size_bytes = child.stat().st_size
            except OSError:
                size_bytes = 0
            files.append(
                {
                    "name": child.name,
                    "kind": kind,
                    "text": text if kind == "text" else "",
                    "size_bytes": size_bytes,
                }
            )
        packs.append(
            {
                "id": pack_dir.name,
                "path": f"prompt_packs/{pack_dir.name}",
                "pack_json": pack_json_obj,
                "pack_json_error": pack_json_err,
                "files": files,
            }
        )
    return {"ok": True, "dir": "prompt_packs", "packs": packs}


def library_diversity_payload() -> dict[str, Any]:
    """Episode diversity catalog: static rules, LLM cache, live hint preview."""
    from pipeline.recent_episode_diversity import build_library_diversity_payload

    repo_root = _srv()._REPO_ROOT
    narr_dir = repo_root / "narrations"
    try:
        from pipeline.narration_common import load_narration_config

        cfg = load_narration_config()
    except Exception:
        cfg = {}
    return build_library_diversity_payload(
        repo_root,
        narr_dir,
        prompt_pack="lewis_clark",
        narration_config=cfg,
    )


def library_week_arcs_payload() -> dict[str, Any]:
    """All saved week-arc plans (state/week_arcs/week_<start_date_id>.json), newest first.

    Returns each plan's full doc (through_line, avoid_this_week, per-day roles/modes) since
    these files are small; there's no separate detail endpoint. Excludes the checked-in
    week_arc.example.json template.
    """
    from pipeline.week_arc import list_saved_week_arcs, load_week_arc_config

    repo_root = _srv()._REPO_ROOT
    cfg = load_week_arc_config(repo_root)
    arcs = list_saved_week_arcs(repo_root)
    return {
        "ok": True,
        "dir": "state/week_arcs",
        "config": {
            "first_anchor_date_id": cfg.get("first_anchor_date_id"),
            "week_journal_days_min": cfg.get("week_journal_days_min"),
            "week_journal_days_max": cfg.get("week_journal_days_max"),
        },
        "arcs": arcs,
    }


def regenerate_diversity_checker_http(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST /api/library/diversity/regenerate-checker — LLM-regenerate one pattern checker."""
    b = body if isinstance(body, dict) else {}
    pattern_id = str(b.get("pattern_id") or "").strip()
    if not pattern_id:
        return 400, {"ok": False, "error": "missing_pattern_id"}
    prompt_pack = str(b.get("prompt_pack") or "lewis_clark").strip()
    try:
        from pipeline.episode_diversity_audit import regenerate_diversity_checker

        repo_root = _srv()._REPO_ROOT
        result = regenerate_diversity_checker(
            repo_root,
            repo_root / "narrations",
            prompt_pack,
            pattern_id,
        )
        return 200, result
    except ValueError as exc:
        return 400, {"ok": False, "error": "bad_request", "message": str(exc)}
    except Exception as exc:
        return 500, {"ok": False, "error": "exception", "message": str(exc)}


def refresh_diversity_audit_http(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST /api/library/diversity/refresh — full LLM audit → state cache."""
    b = body if isinstance(body, dict) else {}
    prompt_pack = str(b.get("prompt_pack") or "lewis_clark").strip()
    last_raw = b.get("last")
    try:
        last = int(last_raw) if last_raw is not None and str(last_raw).strip() != "" else 0
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "bad_request", "message": "last must be an integer"}
    model = str(b.get("model") or "").strip() or None
    try:
        from pipeline.episode_diversity_audit import refresh_diversity_audit

        repo_root = _srv()._REPO_ROOT
        result = refresh_diversity_audit(
            repo_root,
            repo_root / "narrations",
            prompt_pack,
            last=last,
            model=model,
        )
        # Omit full payload from HTTP (can be large); keep summary fields.
        out = {k: v for k, v in result.items() if k != "payload"}
        return 200, out
    except ValueError as exc:
        return 400, {"ok": False, "error": "bad_request", "message": str(exc)}
    except Exception as exc:
        return 500, {"ok": False, "error": "exception", "message": str(exc)}
