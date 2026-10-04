"""B-roll thumbnail, suggest, and materialize view/action payloads for pipeline_ui.

Split out of ``server.py`` (see ``ai-plans/pipeline-ui-server-split.md``, step 3).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from pipeline_ui.paths import (
    _journal_date_to_date_id,
    _normalize_journal_date_input,
    _resolve_python,
)
from pipeline_ui.runtime import srv as _srv
from scripts.b_roll_suggest_for_narration import (
    DEFAULT_MIN_BASE_SIMILARITY,
    build_b_roll_suggest_payload,
)


def _file_uri_for_local_path(p: Path) -> str:
    p = p.resolve()
    if os.name == "nt":
        return "file:///" + p.as_posix()
    return "file://" + p.as_posix()


def _safe_repo_video_rel(rel: str) -> Path | None:
    """Resolve repo-relative path to an existing .mp4 under movie-images/ or b_roll_library/."""
    if not rel:
        return None
    rel = rel.replace("\\", "/").strip().lstrip("/")
    if ".." in rel or "//" in rel:
        return None
    if not rel.lower().endswith(".mp4"):
        return None
    if not (rel.startswith("movie-images/") or rel.startswith("b_roll_library/")):
        return None
    repo_root = _srv()._REPO_ROOT
    p = (repo_root / rel).resolve()
    root = repo_root.resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    return p if p.is_file() else None


def _ffmpeg_extract_thumbnail_frame(ffmpeg_bin: str, video_path: Path, tmp: Path) -> bool:
    """Try several seek strategies; return True if tmp is a non-empty JPEG."""
    sub_kw: dict[str, Any] = dict(
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        sub_kw["creationflags"] = subprocess.CREATE_NO_WINDOW

    # FFmpeg 6.1+ / image2 muxer: single JPEG requires -update 1 or it errors / writes no file.
    strategies: list[list[str]] = [
        # Fast input seek (may be inaccurate; good first try)
        [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            "0.25",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-update",
            "1",
            "-q:v",
            "4",
            str(tmp),
        ],
        # Accurate seek after demux (slower; avoids black first frame)
        [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-ss",
            "0.25",
            "-frames:v",
            "1",
            "-update",
            "1",
            "-q:v",
            "4",
            str(tmp),
        ],
        # First decoded frame (t=0)
        [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-update",
            "1",
            "-q:v",
            "4",
            str(tmp),
        ],
    ]
    last_stderr = ""
    for cmd in strategies:
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass
        r = subprocess.run(cmd, **sub_kw)
        if r.stderr:
            last_stderr = (r.stderr or "").strip()
        if r.returncode != 0:
            continue
        try:
            if tmp.is_file() and tmp.stat().st_size > 0:
                return True
        except OSError:
            pass
    if tmp.is_file():
        try:
            tmp.unlink()
        except OSError:
            pass
    if last_stderr:
        tail = last_stderr[-800:] if len(last_stderr) > 800 else last_stderr
        print(f"[pipeline_ui] ffmpeg stderr (last try): {tail}", file=sys.stderr)
    return False


def _ensure_b_roll_thumbnail_jpeg(video_path: Path) -> Path | None:
    _srv_mod = _srv()
    ffmpeg_bin = _srv_mod._ffmpeg_executable()
    if not ffmpeg_bin:
        return None
    try:
        st = video_path.stat()
    except OSError:
        return None
    key_src = f"{video_path.resolve()}:{st.st_mtime_ns}:{st.st_size}"
    key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:28]
    thumb_cache = _srv_mod._THUMB_CACHE
    out = thumb_cache / f"{key}.jpg"
    if out.is_file() and out.stat().st_size > 0:
        return out
    try:
        thumb_cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    with _srv_mod._THUMB_GEN_LOCK:
        if out.is_file() and out.stat().st_size > 0:
            return out
        try:
            st2 = video_path.stat()
        except OSError:
            return None
        key_src2 = f"{video_path.resolve()}:{st2.st_mtime_ns}:{st2.st_size}"
        key2 = hashlib.sha256(key_src2.encode("utf-8")).hexdigest()[:28]
        out = thumb_cache / f"{key2}.jpg"
        if out.is_file() and out.stat().st_size > 0:
            return out
        # Filename must end in .jpg so ffmpeg infers the image2 muxer (.jpg.tmp breaks on Windows).
        tmp = out.with_name(f"{out.stem}.partial.jpg")
        if not _ffmpeg_extract_thumbnail_frame(ffmpeg_bin, video_path, tmp):
            return None
        try:
            if out.is_file():
                out.unlink()
            tmp.replace(out)
        except OSError:
            return tmp if tmp.is_file() else None
        return out


def _enrich_b_roll_payload_file_uris(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("ok"):
        return payload
    repo_root = _srv()._REPO_ROOT
    for seg in payload.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        tr = seg.get("target_movie_rel")
        if isinstance(tr, str) and tr:
            tp = (repo_root / tr).resolve()
            if tp.is_file():
                try:
                    tp.relative_to(repo_root.resolve())
                    seg["target_movie_file_uri"] = _file_uri_for_local_path(tp)
                except ValueError:
                    seg["target_movie_file_uri"] = None
            else:
                seg["target_movie_file_uri"] = None
        for c in seg.get("candidates") or []:
            if not isinstance(c, dict):
                continue
            sr = c.get("source_movie_rel")
            if isinstance(sr, str) and sr:
                sp = (repo_root / sr).resolve()
                if sp.is_file():
                    try:
                        sp.relative_to(repo_root.resolve())
                        c["source_movie_file_uri"] = _file_uri_for_local_path(sp)
                    except ValueError:
                        c["source_movie_file_uri"] = None
                else:
                    c["source_movie_file_uri"] = None
            else:
                c["source_movie_file_uri"] = None
    return payload


_BROLL_MANIFEST_PATH = _srv()._REPO_ROOT / "b_roll_library" / "manifest.json"

_BROLL_TAG_STOPWORDS = frozenset(
    """
    the and for with from that this were are was has have their they been into
    river water shot camera medium wide close slow back ground foreground scene
    frame light dark soft small large other each both such than then some any
    very more most also just only over under upon through about into
    """.split()
)


def _tags_from_video_prompt_broll(text: str, max_tags: int = 10) -> list[str]:
    words = re.findall(r"[a-zA-Z]{3,}", (text or "").lower())
    out: list[str] = []
    seen: set[str] = set()
    for w in words:
        if w in _BROLL_TAG_STOPWORDS or len(w) < 4:
            continue
        if w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= max_tags:
            break
    return out if out else ["segment"]


def b_roll_append_from_preview(date_id: str, segment_index: int) -> dict[str, Any]:
    """Append one clip entry to b_roll_library/manifest.json if not duplicate."""
    date_id = (date_id or "").strip()
    if not re.fullmatch(r"\d{8}", date_id):
        return {"ok": False, "error": "invalid_date_id", "message": "date_id must be 8 digits."}
    try:
        seg_i = int(segment_index)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": "invalid_segment",
            "message": "segment_index must be an integer.",
        }
    if seg_i < 1 or seg_i > 99:
        return {"ok": False, "error": "invalid_segment", "message": "segment_index must be 1–99."}

    repo_root = _srv()._REPO_ROOT
    mp4 = (repo_root / "movie-images" / date_id / f"{seg_i:02d}.mp4").resolve()
    root = repo_root.resolve()
    try:
        mp4.relative_to(root)
    except ValueError:
        return {"ok": False, "error": "invalid_path", "message": "Segment path escapes repo."}
    if not mp4.is_file():
        rel = mp4.relative_to(root).as_posix()
        return {
            "ok": False,
            "error": "no_mp4",
            "message": f"Missing {rel} — generate this segment clip first.",
        }

    if not _BROLL_MANIFEST_PATH.is_file():
        return {
            "ok": False,
            "error": "no_manifest",
            "message": f"Missing {_BROLL_MANIFEST_PATH.relative_to(root)}.",
        }

    try:
        manifest = json.loads(_BROLL_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": "manifest_read", "message": str(e)}

    clips = manifest.get("clips")
    if not isinstance(clips, list):
        return {
            "ok": False,
            "error": "manifest_invalid",
            "message": "manifest.json: clips must be a list.",
        }

    for c in clips:
        if not isinstance(c, dict):
            continue
        src = c.get("source")
        if not isinstance(src, dict):
            continue
        if str(src.get("date_id") or "").strip() != date_id:
            continue
        try:
            if int(src.get("segment_index")) == seg_i:
                return {
                    "ok": True,
                    "already_exists": True,
                    "message": "This date_id + segment_index is already in the manifest.",
                    "clip_id": c.get("id"),
                }
        except (TypeError, ValueError):
            pass

    narr_path = repo_root / "narrations" / f"narration{date_id}.json"
    video_prompt = ""
    if narr_path.is_file():
        try:
            nd = json.loads(narr_path.read_text(encoding="utf-8"))
            script = nd.get("narration_script") or []
            if isinstance(script, list) and 1 <= seg_i <= len(script):
                row = script[seg_i - 1]
                if isinstance(row, dict):
                    vp = row.get("video_prompt")
                    if isinstance(vp, str):
                        video_prompt = vp
        except (json.JSONDecodeError, OSError):
            pass

    tags = _tags_from_video_prompt_broll(video_prompt)
    base_id = f"{date_id}_{seg_i:02d}_preview"
    clip_id = base_id
    existing_ids = {str(c.get("id")) for c in clips if isinstance(c, dict) and c.get("id")}
    n = 2
    while clip_id in existing_ids:
        clip_id = f"{base_id}_{n}"
        n += 1

    clip_entry: dict[str, Any] = {
        "id": clip_id,
        "file": f"clips/{date_id}_{seg_i:02d}.mp4",
        "tags": tags,
        "aspect_ratio": "9:16",
        "duration_seconds": None,
        "source": {
            "kind": "pipeline_generation",
            "date_id": date_id,
            "segment_index": seg_i,
            "vendor": "fal",
            "notes": "Added from pipeline UI (latest video preview while paused).",
        },
    }

    clips.append(clip_entry)
    manifest["clips"] = clips
    try:
        text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        tmp = _BROLL_MANIFEST_PATH.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(_BROLL_MANIFEST_PATH)
    except OSError as e:
        return {"ok": False, "error": "write_failed", "message": str(e)}

    return {
        "ok": True,
        "already_exists": False,
        "clip_id": clip_id,
        "message": f"Appended “{clip_id}” to b_roll_library/manifest.json.",
        "tags": tags,
    }


def b_roll_suggest_http_payload(
    journal_date: str,
    top: int,
    prefer_environment: bool,
    min_base_similarity: float = DEFAULT_MIN_BASE_SIMILARITY,
) -> dict[str, Any]:
    jd_norm = _normalize_journal_date_input(journal_date)
    if not jd_norm:
        return {
            "ok": False,
            "error": "invalid_journal_date",
            "message": "Use YYYY-MM-DD or YYYYMMDD within expedition years (1803–1806).",
        }
    did = _journal_date_to_date_id(jd_norm)
    if not did:
        return {
            "ok": False,
            "error": "invalid_journal_date",
            "message": "Use YYYY-MM-DD or YYYYMMDD within expedition years (1803–1806).",
        }
    top_clamped = max(1, min(int(top), 20))
    min_base = max(0.0, min(1.0, float(min_base_similarity)))
    payload = build_b_roll_suggest_payload(
        did,
        repo_root=_srv()._REPO_ROOT,
        top=top_clamped,
        prefer_environment=prefer_environment,
        min_base_similarity=min_base,
    )
    if payload.get("ok"):
        payload["top"] = top_clamped
        from pipeline.broll_scene_anchor import (
            broll_scene_anchor_enabled,
            segment_eligible_for_scene_anchor,
        )

        payload["broll_scene_anchor_enabled"] = broll_scene_anchor_enabled()
        narr_path = _srv()._REPO_ROOT / "narrations" / f"narration{did}.json"
        if narr_path.is_file():
            try:
                narr_data = json.loads(narr_path.read_text(encoding="utf-8-sig"))
                if isinstance(narr_data, dict):
                    for seg in payload.get("segments") or []:
                        if not isinstance(seg, dict):
                            continue
                        si = seg.get("segment_index")
                        if si is None:
                            continue
                        try:
                            seg["scene_anchor_eligible"] = segment_eligible_for_scene_anchor(
                                narr_data, int(si)
                            )
                        except (TypeError, ValueError):
                            pass
            except (json.JSONDecodeError, OSError):
                pass
        _enrich_b_roll_payload_file_uris(payload)
        _enrich_b_roll_conversation_metadata(payload, did)
    return payload


def _enrich_b_roll_conversation_metadata(payload: dict[str, Any], date_id: str) -> None:
    """Attach shared-backdrop conversation run fields to b-roll suggest segment rows."""
    narr_path = _srv()._REPO_ROOT / "narrations" / f"narration{date_id}.json"
    if not narr_path.is_file():
        return
    try:
        narr_data = json.loads(narr_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(narr_data, dict):
        return
    from pipeline.conversation_scene_anchor import conversation_anchor_ui_enrichment

    conv_ui = conversation_anchor_ui_enrichment(narr_data)
    by_seg = conv_ui.get("conversation_by_segment") or {}
    segs = payload.get("segments")
    if not isinstance(segs, list):
        return
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        si = seg.get("segment_index")
        extra = by_seg.get(str(si))
        if isinstance(extra, dict):
            seg.update(extra)
    payload["conversation_runs"] = conv_ui.get("conversation_runs") or []


def _safe_materialize_source_repo_rel(rel: str) -> str | None:
    """Return posix repo-relative path to an existing .mp4 under the repo, or None if unsafe/missing."""
    if not isinstance(rel, str) or not rel.strip():
        return None
    norm = rel.replace("\\", "/").strip().lstrip("/")
    if ".." in norm or "//" in norm:
        return None
    if not norm.lower().endswith(".mp4"):
        return None
    repo_root = _srv()._REPO_ROOT
    p = (repo_root / norm).resolve()
    root = repo_root.resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    if not p.is_file():
        return None
    return p.relative_to(root).as_posix()


def b_roll_materialize_http(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Validate body and run scripts/b_roll_materialize_movie_images.py. Returns (http_code, json_dict)."""
    date_raw = body.get("date_id")
    date_id = date_raw.strip() if isinstance(date_raw, str) else ""
    if not re.fullmatch(r"\d{8}", date_id):
        return 400, {
            "ok": False,
            "error": "invalid_date_id",
            "message": "date_id must be eight digits (YYYYMMDD).",
        }

    assign = body.get("assignments")
    if not isinstance(assign, list) or not assign:
        return 400, {
            "ok": False,
            "error": "no_assignments",
            "message": 'assignments must be a non-empty list of strings like "1=b_roll_library/clips/foo.mp4".',
        }

    parsed: list[tuple[int, str]] = []
    seen_seg: set[int] = set()
    for idx, item in enumerate(assign):
        if not isinstance(item, str) or "=" not in item:
            return 400, {
                "ok": False,
                "error": "bad_assignment",
                "message": f"Invalid assignment at index {idx} (expected segment_index=repo_relative_path).",
            }
        left, right = item.split("=", 1)
        left, right = left.strip(), right.strip()
        try:
            seg_i = int(left, 10)
        except ValueError:
            return 400, {
                "ok": False,
                "error": "bad_segment",
                "message": f"Invalid segment index in: {item!r}",
            }
        if seg_i < 1 or seg_i > 999:
            return 400, {
                "ok": False,
                "error": "bad_segment",
                "message": f"Segment index out of range: {seg_i}",
            }
        if seg_i in seen_seg:
            return 400, {
                "ok": False,
                "error": "duplicate_segment",
                "message": f"Duplicate segment index: {seg_i}",
            }
        seen_seg.add(seg_i)
        safe_rel = _safe_materialize_source_repo_rel(right)
        if not safe_rel:
            return 400, {
                "ok": False,
                "error": "invalid_path",
                "message": f"Missing or unsafe MP4 path: {right!r}",
            }
        parsed.append((seg_i, safe_rel))

    parsed.sort(key=lambda t: t[0])
    tokens = [f"{a}={b}" for a, b in parsed]

    repo_root = _srv()._REPO_ROOT
    script = repo_root / "scripts" / "b_roll_materialize_movie_images.py"
    if not script.is_file():
        return 500, {
            "ok": False,
            "error": "missing_script",
            "message": str(script),
        }

    try:
        spec = _srv().load_options()
        if spec.get("error"):
            raise RuntimeError(str(spec["error"]))
        backend = spec.get("backend") or {}
        py = _resolve_python(
            os.environ.get("PIPELINE_UI_PYTHON"),
            list(
                backend.get("python_candidates") or [".venv/Scripts/python.exe", ".venv/bin/python"]
            ),
        )
    except Exception as e:
        return 500, {"ok": False, "error": "options", "message": str(e)}

    cmd = [py, str(script), date_id, *tokens]
    cwd = str(repo_root)
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return 504, {
            "ok": False,
            "error": "timeout",
            "command": cmd,
            "cwd": cwd,
            "message": "b_roll_materialize_movie_images.py exceeded 600s.",
        }
    except OSError as e:
        return 500, {
            "ok": False,
            "error": "spawn_failed",
            "message": str(e),
            "command": cmd,
            "cwd": cwd,
        }

    out: dict[str, Any] = {
        "ok": proc.returncode == 0,
        "command": cmd,
        "cwd": cwd,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }
    code = 200 if proc.returncode == 0 else 500
    return code, out
