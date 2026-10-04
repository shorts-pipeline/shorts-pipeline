"""Options loader and narration-JSON edit endpoints for pipeline_ui (Produce tab editors).

Split out of ``server.py`` (see ``ai-plans/pipeline-ui-server-split.md``, step 4).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline_ui.paths import _journal_date_to_date_id, _safe_journal_date
from pipeline_ui.runtime import srv as _srv


def load_options() -> dict[str, Any]:
    options_path = _srv()._OPTIONS_PATH
    if not options_path.exists():
        return {"error": f"Missing {options_path}", "fields": []}
    return json.loads(options_path.read_text(encoding="utf-8"))


def _logical_segment_index(seg: dict[str, Any], list_index: int) -> int:
    raw_idx = seg.get("segment_index")
    if raw_idx is None:
        return list_index + 1
    try:
        return int(raw_idx)
    except (TypeError, ValueError):
        return list_index + 1


def _narration_segment_for_index(data: dict[str, Any], segment_index: int) -> dict[str, Any] | None:
    """Resolve the narration_script entry for a 1-based segment index (same rules as b-roll suggest)."""
    script = data.get("narration_script") or []
    if not isinstance(script, list):
        return None
    for j, seg in enumerate(script):
        if not isinstance(seg, dict):
            continue
        if _logical_segment_index(seg, j) == segment_index:
            return seg
    return None


def _narration_script_list_index(data: dict[str, Any], segment_index: int) -> int | None:
    """List offset in ``narration_script`` for a 1-based logical segment index."""
    script = data.get("narration_script") or []
    if not isinstance(script, list):
        return None
    for j, seg in enumerate(script):
        if not isinstance(seg, dict):
            continue
        if _logical_segment_index(seg, j) == segment_index:
            return j
    return None


def narration_text_requests_segment_delete(text: str) -> bool:
    """True when the editor text is exactly DELETEME (case-insensitive, after strip)."""
    return (text or "").strip().upper() == "DELETEME"


def _remove_narration_script_segment(
    data: dict[str, Any], segment_index: int
) -> tuple[bool, str | None]:
    """
    Remove one ``narration_script`` row and renumber ``segment_index`` fields 1..n.

    Returns ``(ok, error_code)``.
    """
    script = data.get("narration_script")
    if not isinstance(script, list):
        return False, "invalid_script"
    if len(script) <= 1:
        return False, "cannot_delete_last_segment"
    list_idx = _narration_script_list_index(data, segment_index)
    if list_idx is None:
        return False, "segment_not_found"
    script.pop(list_idx)
    for j, seg in enumerate(script):
        if isinstance(seg, dict):
            seg["segment_index"] = j + 1
    return True, None


def _write_narration_json(narr_path: Path, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        narr_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        return 500, {"ok": False, "error": "write_failed", "message": str(e)}
    return 200, {"ok": True}


def narration_update_video_prompt_http(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Persist ``video_prompt`` for one ``narration_script`` row (matched by logical segment_index)."""
    b = body if isinstance(body, dict) else {}
    jd_raw = b.get("journal_date")
    if not isinstance(jd_raw, str) or not jd_raw.strip():
        return 400, {"ok": False, "error": "missing_journal_date"}
    safe = _safe_journal_date(jd_raw.strip())
    if not safe:
        return 400, {"ok": False, "error": "invalid_journal_date"}
    did = _journal_date_to_date_id(safe)
    if not did:
        return 400, {"ok": False, "error": "invalid_date_id"}

    seg_raw = b.get("segment_index", 1)
    try:
        seg_i = int(seg_raw)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "bad_segment_index"}
    if seg_i < 1:
        return 400, {"ok": False, "error": "bad_segment_index"}

    if "video_prompt" not in b:
        return 400, {"ok": False, "error": "missing_video_prompt"}
    vp_new = b["video_prompt"]
    if vp_new is None:
        vp_new = ""
    elif not isinstance(vp_new, str):
        vp_new = str(vp_new)

    repo_root = _srv()._REPO_ROOT
    narr_path = (repo_root / "narrations" / f"narration{did}.json").resolve()
    try:
        narr_path.relative_to(repo_root.resolve())
    except ValueError:
        return 400, {"ok": False, "error": "invalid_path"}
    if not narr_path.is_file():
        return 404, {"ok": False, "error": "no_narration"}

    try:
        raw = narr_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        return 500, {"ok": False, "error": "narration_read_failed", "message": str(e)}

    if narration_text_requests_segment_delete(vp_new):
        ok_rm, err_rm = _remove_narration_script_segment(data, seg_i)
        if not ok_rm:
            code = 404 if err_rm == "segment_not_found" else 400
            return code, {"ok": False, "error": err_rm or "delete_failed"}
        code, out = _write_narration_json(narr_path, data)
        if code != 200:
            return code, out
        script = data.get("narration_script") or []
        return 200, {
            "ok": True,
            "deleted": True,
            "date_id": did,
            "segment_index": seg_i,
            "segment_count": len(script) if isinstance(script, list) else 0,
        }

    seg = _narration_segment_for_index(data, seg_i)
    if not seg:
        return 404, {"ok": False, "error": "segment_not_found"}

    seg["video_prompt"] = vp_new
    code, out = _write_narration_json(narr_path, data)
    if code != 200:
        return code, out

    return 200, {"ok": True, "date_id": did, "segment_index": seg_i}


def narration_update_talking_head_prompt_http(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Persist ``talking_head_prompt`` for one ``narration_script`` row."""
    b = body if isinstance(body, dict) else {}
    jd_raw = b.get("journal_date")
    if not isinstance(jd_raw, str) or not jd_raw.strip():
        return 400, {"ok": False, "error": "missing_journal_date"}
    safe = _safe_journal_date(jd_raw.strip())
    if not safe:
        return 400, {"ok": False, "error": "invalid_journal_date"}
    did = _journal_date_to_date_id(safe)
    if not did:
        return 400, {"ok": False, "error": "invalid_date_id"}

    seg_raw = b.get("segment_index", 1)
    try:
        seg_i = int(seg_raw)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "bad_segment_index"}
    if seg_i < 1:
        return 400, {"ok": False, "error": "bad_segment_index"}

    if "talking_head_prompt" not in b:
        return 400, {"ok": False, "error": "missing_talking_head_prompt"}
    thp_new = b["talking_head_prompt"]
    if thp_new is None:
        thp_new = ""
    elif not isinstance(thp_new, str):
        thp_new = str(thp_new)

    repo_root = _srv()._REPO_ROOT
    narr_path = (repo_root / "narrations" / f"narration{did}.json").resolve()
    try:
        narr_path.relative_to(repo_root.resolve())
    except ValueError:
        return 400, {"ok": False, "error": "invalid_path"}
    if not narr_path.is_file():
        return 404, {"ok": False, "error": "no_narration"}

    try:
        raw = narr_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        return 500, {"ok": False, "error": "narration_read_failed", "message": str(e)}

    if narration_text_requests_segment_delete(thp_new):
        ok_rm, err_rm = _remove_narration_script_segment(data, seg_i)
        if not ok_rm:
            code = 404 if err_rm == "segment_not_found" else 400
            return code, {"ok": False, "error": err_rm or "delete_failed"}
        code, out = _write_narration_json(narr_path, data)
        if code != 200:
            return code, out
        script = data.get("narration_script") or []
        return 200, {
            "ok": True,
            "deleted": True,
            "date_id": did,
            "segment_index": seg_i,
            "segment_count": len(script) if isinstance(script, list) else 0,
        }

    seg = _narration_segment_for_index(data, seg_i)
    if not seg:
        return 404, {"ok": False, "error": "segment_not_found"}

    if (seg.get("visual_mode") or "").strip().lower() != "talking_head":
        return 400, {
            "ok": False,
            "error": "not_talking_head_segment",
            "message": "talking_head_prompt applies only to visual_mode talking_head rows.",
        }

    thp_stripped = thp_new.strip()
    if thp_stripped:
        seg["talking_head_prompt"] = thp_new
    else:
        seg.pop("talking_head_prompt", None)

    code, out = _write_narration_json(narr_path, data)
    if code != 200:
        return code, out

    return 200, {"ok": True, "date_id": did, "segment_index": seg_i}


def narration_update_opening_frame_http(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Persist ``opening_frame`` for one ``narration_script`` row (scene-anchor t=0 still)."""
    b = body if isinstance(body, dict) else {}
    jd_raw = b.get("journal_date")
    if not isinstance(jd_raw, str) or not jd_raw.strip():
        return 400, {"ok": False, "error": "missing_journal_date"}
    safe = _safe_journal_date(jd_raw.strip())
    if not safe:
        return 400, {"ok": False, "error": "invalid_journal_date"}
    did = _journal_date_to_date_id(safe)
    if not did:
        return 400, {"ok": False, "error": "invalid_date_id"}

    seg_raw = b.get("segment_index", 1)
    try:
        seg_i = int(seg_raw)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "bad_segment_index"}
    if seg_i < 1:
        return 400, {"ok": False, "error": "bad_segment_index"}

    if "opening_frame" not in b:
        return 400, {"ok": False, "error": "missing_opening_frame"}
    of_new = b["opening_frame"]
    if of_new is None:
        of_new = ""
    elif not isinstance(of_new, str):
        of_new = str(of_new)

    repo_root = _srv()._REPO_ROOT
    narr_path = (repo_root / "narrations" / f"narration{did}.json").resolve()
    try:
        narr_path.relative_to(repo_root.resolve())
    except ValueError:
        return 400, {"ok": False, "error": "invalid_path"}
    if not narr_path.is_file():
        return 404, {"ok": False, "error": "no_narration"}

    try:
        raw = narr_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        return 500, {"ok": False, "error": "narration_read_failed", "message": str(e)}

    seg = _narration_segment_for_index(data, seg_i)
    if not seg:
        return 404, {"ok": False, "error": "segment_not_found"}

    of_stripped = of_new.strip()
    if of_stripped:
        seg["opening_frame"] = of_new
    else:
        seg.pop("opening_frame", None)

    code, out = _write_narration_json(narr_path, data)
    if code != 200:
        return code, out

    return 200, {"ok": True, "date_id": did, "segment_index": seg_i}


def narration_update_narration_http(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Persist ``narration`` (voiceover text) for one ``narration_script`` row."""
    b = body if isinstance(body, dict) else {}
    jd_raw = b.get("journal_date")
    if not isinstance(jd_raw, str) or not jd_raw.strip():
        return 400, {"ok": False, "error": "missing_journal_date"}
    safe = _safe_journal_date(jd_raw.strip())
    if not safe:
        return 400, {"ok": False, "error": "invalid_journal_date"}
    did = _journal_date_to_date_id(safe)
    if not did:
        return 400, {"ok": False, "error": "invalid_date_id"}

    seg_raw = b.get("segment_index", 1)
    try:
        seg_i = int(seg_raw)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "bad_segment_index"}
    if seg_i < 1:
        return 400, {"ok": False, "error": "bad_segment_index"}

    if "narration" not in b:
        return 400, {"ok": False, "error": "missing_narration"}
    nar_new = b["narration"]
    if nar_new is None:
        nar_new = ""
    elif not isinstance(nar_new, str):
        nar_new = str(nar_new)

    repo_root = _srv()._REPO_ROOT
    narr_path = (repo_root / "narrations" / f"narration{did}.json").resolve()
    try:
        narr_path.relative_to(repo_root.resolve())
    except ValueError:
        return 400, {"ok": False, "error": "invalid_path"}
    if not narr_path.is_file():
        return 404, {"ok": False, "error": "no_narration"}

    try:
        raw = narr_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        return 500, {"ok": False, "error": "narration_read_failed", "message": str(e)}

    if narration_text_requests_segment_delete(nar_new):
        ok_rm, err_rm = _remove_narration_script_segment(data, seg_i)
        if not ok_rm:
            code = 404 if err_rm == "segment_not_found" else 400
            return code, {"ok": False, "error": err_rm or "delete_failed"}
        code, out = _write_narration_json(narr_path, data)
        if code != 200:
            return code, out
        script = data.get("narration_script") or []
        return 200, {
            "ok": True,
            "deleted": True,
            "date_id": did,
            "segment_index": seg_i,
            "segment_count": len(script) if isinstance(script, list) else 0,
        }

    seg = _narration_segment_for_index(data, seg_i)
    if not seg:
        return 404, {"ok": False, "error": "segment_not_found"}

    seg["narration"] = nar_new
    code, out = _write_narration_json(narr_path, data)
    if code != 200:
        return code, out

    return 200, {"ok": True, "date_id": did, "segment_index": seg_i}


def narration_update_raw_json_http(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Persist full narration JSON for one date after basic schema checks."""
    b = body if isinstance(body, dict) else {}
    jd_raw = b.get("journal_date")
    if not isinstance(jd_raw, str) or not jd_raw.strip():
        return 400, {"ok": False, "error": "missing_journal_date"}
    safe = _safe_journal_date(jd_raw.strip())
    if not safe:
        return 400, {"ok": False, "error": "invalid_journal_date"}
    did = _journal_date_to_date_id(safe)
    if not did:
        return 400, {"ok": False, "error": "invalid_date_id"}

    raw_new = b.get("json_text")
    if raw_new is None:
        return 400, {"ok": False, "error": "missing_json_text"}
    if not isinstance(raw_new, str):
        raw_new = str(raw_new)

    repo_root = _srv()._REPO_ROOT
    narr_path = (repo_root / "narrations" / f"narration{did}.json").resolve()
    try:
        narr_path.relative_to(repo_root.resolve())
    except ValueError:
        return 400, {"ok": False, "error": "invalid_path"}
    if not narr_path.is_file():
        return 404, {"ok": False, "error": "no_narration"}

    try:
        parsed = json.loads(raw_new)
    except json.JSONDecodeError as e:
        return 400, {"ok": False, "error": "invalid_json", "message": str(e)}
    if not isinstance(parsed, dict):
        return 400, {
            "ok": False,
            "error": "invalid_shape",
            "message": "Top-level JSON must be an object.",
        }
    if not isinstance(parsed.get("narration_script"), list):
        return 400, {
            "ok": False,
            "error": "invalid_shape",
            "message": "Expected narration_script to be a list.",
        }

    try:
        narr_path.write_text(
            json.dumps(parsed, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        return 500, {"ok": False, "error": "write_failed", "message": str(e)}

    return 200, {"ok": True, "date_id": did}
