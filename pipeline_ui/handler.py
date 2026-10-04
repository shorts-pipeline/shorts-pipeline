"""HTTP request dispatch for pipeline_ui: routes ~54 API paths + static/preview
serving to the payload/action functions in the other pipeline_ui modules.

Split out of ``server.py`` (see ``ai-plans/pipeline-ui-server-split.md``, step 5).
Anything that still lives directly in ``server.py`` (run-lock/subprocess status,
UI version/reload, the three async-job wrapper clusters, conversation-scene-anchor
settings) is read through ``_srv()`` at call time so tests monkeypatching
``pipeline_ui.server`` globals keep working and so ``server`` can import this
module without a cycle.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from pipeline.narration_utils import (
    narration_json_expects_dialogue_mode,
    narration_json_expects_long_conversation_mode,
)
from pipeline.ui_argv import boolish as _boolish
from pipeline.ui_argv import build_argv
from pipeline_ui.broll_view import (
    _ensure_b_roll_thumbnail_jpeg,
    _safe_repo_video_rel,
    b_roll_append_from_preview,
    b_roll_materialize_http,
    b_roll_suggest_http_payload,
)
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
    load_options,
    narration_update_narration_http,
    narration_update_opening_frame_http,
    narration_update_raw_json_http,
    narration_update_talking_head_prompt_http,
    narration_update_video_prompt_http,
)
from pipeline_ui.narration_view import narration_segments_payload, video_preview_cues_payload
from pipeline_ui.paths import (
    _journal_date_to_date_id,
    _resolve_python,
    _safe_journal_date,
    episode_state_http_payload,
    journal_entry_payload,
    latest_narration_path,
    latest_video_path,
    run_report_http_payload,
)
from pipeline_ui.prompt_preview import phase1_prompt_preview_payload
from pipeline_ui.run_actions import (
    assemble_video_run,
    generate_tts_run,
    generate_video_reuse_anchors_run,
    regenerate_video_segment_run,
    reset_day_artifacts_run,
)
from pipeline_ui.runtime import srv as _srv
from pipeline_ui.youtube_view import (
    _output_video_path_for_date_id,
    youtube_upload_from_preview,
    youtube_upload_status_for_ui,
)
from scripts.b_roll_suggest_for_narration import DEFAULT_MIN_BASE_SIMILARITY


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[pipeline_ui] {self.address_string()} - {fmt % args}")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _send_narration_preview(self, journal_date: str | None = None) -> None:
        p: Path | None = None
        if journal_date and journal_date.strip():
            safe = _safe_journal_date(journal_date.strip())
            if not safe:
                self._send(
                    400, b"Invalid journal_date query (YYYY-MM-DD).", "text/plain; charset=utf-8"
                )
                return
            did = _journal_date_to_date_id(safe)
            if did:
                cand = (_srv()._REPO_ROOT / "narrations" / f"narration{did}.json").resolve()
                if cand.is_file():
                    p = cand
        else:
            p = latest_narration_path()
        if not p:
            self._send(
                404,
                b"No narration JSON for this date (or no latest_narration.url).",
                "text/plain; charset=utf-8",
            )
            return
        raw = p.read_text(encoding="utf-8", errors="replace")
        try:
            obj = json.loads(raw)
            pretty = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
        except json.JSONDecodeError:
            pretty = raw
        self._send(200, pretty.encode("utf-8"), "text/plain; charset=utf-8")

    def _send_video_file(self, p: Path, *, not_found_message: bytes) -> None:
        if not p or not p.is_file():
            self._send(404, not_found_message, "text/plain; charset=utf-8")
            return
        size = p.stat().st_size
        range_header = self.headers.get("Range")
        if range_header:
            m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
            if m:
                start_s, end_s = m.group(1), m.group(2)
                try:
                    if start_s == "" and end_s != "":
                        suffix = int(end_s)
                        start = max(0, size - suffix)
                        end = size - 1
                    elif start_s != "":
                        start = int(start_s)
                        end = int(end_s) if end_s != "" else size - 1
                    else:
                        start = 0
                        end = size - 1
                    start = max(0, min(start, size - 1))
                    end = max(start, min(end, size - 1))
                    length = end - start + 1
                    self.send_response(206)
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(length))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    with open(p, "rb") as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = f.read(min(65536, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    return
                except (ValueError, OSError):
                    pass
        try:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(p, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def _send_video_preview(self, journal_date: str | None = None) -> None:
        p: Path | None = None
        if journal_date and journal_date.strip():
            safe = _safe_journal_date(journal_date.strip())
            if not safe:
                self._send(
                    400, b"Invalid journal_date query (YYYY-MM-DD).", "text/plain; charset=utf-8"
                )
                return
            did = _journal_date_to_date_id(safe)
            if did:
                p = _output_video_path_for_date_id(did)
        else:
            p = latest_video_path()
        self._send_video_file(
            p if p else Path(),
            not_found_message=(
                b"No video for this date (expected output/*_<date>_video.mp4) or no latest_video.url."
            ),
        )

    def _send_anchor_preview_video(self, journal_date: str | None = None) -> None:
        if not journal_date or not journal_date.strip():
            self._send(400, b"missing journal_date", "text/plain; charset=utf-8")
            return
        safe = _safe_journal_date(journal_date.strip())
        if not safe:
            self._send(
                400, b"Invalid journal_date query (YYYY-MM-DD).", "text/plain; charset=utf-8"
            )
            return
        did = _journal_date_to_date_id(safe)
        if not did:
            self._send(400, b"Invalid date", "text/plain; charset=utf-8")
            return
        p = _srv()._anchor_preview_video_path_for_date_id(did)
        self._send_video_file(
            p if p else Path(),
            not_found_message=(
                b"No anchor preview video (run Build preview clips + assemble on Produce tab)."
            ),
        )

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        if path == "/api/preview/video":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [None])[0]
            p = None
            if jd and jd.strip():
                safe = _safe_journal_date(jd.strip())
                if safe:
                    did = _journal_date_to_date_id(safe)
                    if did:
                        p = _output_video_path_for_date_id(did)
            else:
                p = latest_video_path()
            if not p:
                self.send_response(404)
                self.end_headers()
                return
            size = p.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path == "/api/preview/anchor-preview-video":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [None])[0]
            p = None
            if jd and jd.strip():
                safe = _safe_journal_date(jd.strip())
                if safe:
                    did = _journal_date_to_date_id(safe)
                    if did:
                        p = _srv()._anchor_preview_video_path_for_date_id(did)
            if not p:
                self.send_response(404)
                self.end_headers()
                return
            size = p.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path == "/api/preview/narration":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [None])[0]
            p = None
            if jd and jd.strip():
                safe = _safe_journal_date(jd.strip())
                if safe:
                    did = _journal_date_to_date_id(safe)
                    if did:
                        cand = (_srv()._REPO_ROOT / "narrations" / f"narration{did}.json").resolve()
                        if cand.is_file():
                            p = cand
            else:
                p = latest_narration_path()
            if not p:
                self.send_response(404)
                self.end_headers()
                return
            raw = p.read_text(encoding="utf-8", errors="replace")
            try:
                obj = json.loads(raw)
                body = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
            except json.JSONDecodeError:
                body = raw
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/") or "/"

        if path == "/api/fal/scene-anchor-i2i/status":
            qs = parse_qs(parsed.query or "")
            job_id = (qs.get("job_id") or [""])[0]
            result = _srv().fal_scene_anchor_i2i_job_status(str(job_id or "").strip())
            code = _srv()._scene_anchor_i2i_http_code(result)
            if result.get("status") == "running":
                code = 200
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/preview/narration":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [None])[0]
            self._send_narration_preview(jd.strip() if jd else None)
            return
        if path == "/api/preview/video":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [None])[0]
            self._send_video_preview(jd.strip() if jd else None)
            return

        if path == "/api/preview/anchor-preview-video":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [None])[0]
            self._send_anchor_preview_video(jd.strip() if jd else None)
            return

        if path == "/api/anchor-preview/status":
            qs = parse_qs(parsed.query or "")
            job_id = (qs.get("job_id") or [""])[0].strip()
            if job_id:
                result = _srv().anchor_preview_job_status(job_id)
                code = 200 if result.get("status") == "running" or result.get("ok") else 404
                body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self._send(code, body, "application/json; charset=utf-8")
                return
            jd = (qs.get("journal_date") or [None])[0]
            if not jd or not str(jd).strip():
                self._send(400, b"missing journal_date", "text/plain; charset=utf-8")
                return
            payload = _srv().anchor_preview_status_http(str(jd).strip())
            code = 200 if payload.get("ok") else 400
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/anchor-preview/job-status":
            qs = parse_qs(parsed.query or "")
            job_id = (qs.get("job_id") or [""])[0]
            result = _srv().anchor_preview_job_status(str(job_id or "").strip())
            code = 200 if result.get("status") == "running" or result.get("ok") else 404
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/conversation-scene-anchor/settings":
            payload = _srv().conversation_scene_anchor_settings_payload()
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return

        if path == "/api/conversation-scene-anchor/status":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [""])[0].strip()
            if not jd:
                self._send(400, b"missing journal_date", "text/plain; charset=utf-8")
                return
            payload = _srv().conversation_scene_anchor_status_http(jd)
            code = 200 if payload.get("ok") else 400
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/conversation-scene-anchor/regenerate-master/status":
            qs = parse_qs(parsed.query or "")
            job_id = (qs.get("job_id") or [""])[0].strip()
            result = _srv().conv_master_regen_job_status(job_id)
            code = 200 if result.get("status") == "running" or result.get("ok") else 404
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/preview/video-cues":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [None])[0]
            pm = (qs.get("preview_mode") or [""])[0].strip().lower()
            require_vid = pm != "anchor"
            payload = video_preview_cues_payload(
                jd.strip() if jd else None,
                require_output_video=require_vid,
            )
            code = 200 if payload.get("ok") else 400
            if payload.get("error") == "no_video":
                code = 404
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/narration-segments":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [None])[0]
            if not jd or not str(jd).strip():
                self._send(400, b"missing journal_date", "text/plain; charset=utf-8")
                return
            try:
                payload = narration_segments_payload(str(jd).strip())
                err = payload.get("error")
                if payload.get("ok"):
                    code = 200
                elif err == "no_file":
                    code = 404
                else:
                    code = 400
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as exc:
                code = 500
                payload = {"ok": False, "error": "segments_failed", "message": str(exc)}
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/narration-dialogue-mode":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [None])[0]
            if not jd or not str(jd).strip():
                self._send(400, b"missing journal_date", "text/plain; charset=utf-8")
                return
            safe = _safe_journal_date(jd.strip())
            if not safe:
                self._send(400, b"invalid journal_date", "text/plain; charset=utf-8")
                return
            did = _journal_date_to_date_id(safe)
            if not did:
                self._send(400, b"Invalid date", "text/plain; charset=utf-8")
                return
            narr_path = _srv()._REPO_ROOT / "narrations" / f"narration{did}.json"
            if not narr_path.is_file():
                out = {
                    "ok": True,
                    "journal_date": safe,
                    "date_id": did,
                    "dialogue_mode": False,
                    "long_conversation_mode": False,
                    "source": "no_file",
                    "long_conversation_source": "no_file",
                }
            else:
                try:
                    data = json.loads(narr_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as e:
                    self._send(
                        500,
                        json.dumps({"ok": False, "error": "read_failed", "message": str(e)}).encode(
                            "utf-8"
                        ),
                        "application/json; charset=utf-8",
                    )
                    return
                dm = narration_json_expects_dialogue_mode(data)
                lcm = narration_json_expects_long_conversation_mode(data)
                src = (
                    "dialogue_mode_key"
                    if isinstance(data.get("dialogue_mode"), bool)
                    else ("segments_heuristic" if dm else "none")
                )
                lsrc = (
                    "long_conversation_mode_key"
                    if isinstance(data.get("long_conversation_mode"), bool)
                    else "none"
                )
                out = {
                    "ok": True,
                    "journal_date": safe,
                    "date_id": did,
                    "dialogue_mode": dm,
                    "long_conversation_mode": lcm,
                    "source": src,
                    "long_conversation_source": lsrc,
                }
            body = json.dumps(out, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return

        if path == "/api/phase1-prompt-preview":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [None])[0]
            nt_raw = (qs.get("no_focus_topic") or qs.get("no_theme") or ["0"])[0]
            dialogue_raw = (qs.get("dialogue") or ["0"])[0]
            long_conv_raw = (qs.get("long_conversation") or qs.get("longconversation") or ["0"])[0]
            if not jd or not jd.strip():
                self._send(400, b"missing journal_date", "text/plain; charset=utf-8")
                return
            try:
                payload = phase1_prompt_preview_payload(
                    jd.strip(),
                    _boolish(nt_raw, False),
                    dialogue=_boolish(dialogue_raw, False),
                    long_conversation=_boolish(long_conv_raw, False),
                )
                if payload.get("ok"):
                    code = 200
                else:
                    err = payload.get("error")
                    code = 404 if err == "xml_not_found" else 400
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as exc:
                code = 500
                payload_err = {"ok": False, "error": "preview_failed", "message": str(exc)}
                body = json.dumps(payload_err, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path.startswith("/api/journal/"):
            date_part = path[len("/api/journal/") :].lstrip("/")
            payload = journal_entry_payload(date_part)
            err = payload.get("error")
            if payload.get("ok"):
                code = 200
            elif err == "invalid_date":
                code = 400
            elif err == "not_found":
                code = 404
            else:
                code = 500
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/options":
            try:
                spec = load_options()
                payload = json.dumps(spec, indent=2, ensure_ascii=False).encode("utf-8")
                self._send(200, payload, "application/json; charset=utf-8")
            except Exception as e:
                msg = json.dumps({"error": str(e)}).encode("utf-8")
                self._send(500, msg, "application/json; charset=utf-8")
            return

        if path == "/api/latest-links":
            try:
                data = load_latest_links()
                payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
                self._send(200, payload, "application/json; charset=utf-8")
            except Exception as e:
                msg = json.dumps({"error": str(e)}).encode("utf-8")
                self._send(500, msg, "application/json; charset=utf-8")
            return

        if path == "/api/youtube/upload-status":
            qs = parse_qs(parsed.query or "")
            scan_arch = _boolish((qs.get("scan_archive") or ["1"])[0], True)
            try:
                data = youtube_upload_status_for_ui(scan_archive=scan_arch)
                payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self._send(200, payload, "application/json; charset=utf-8")
            except Exception as e:
                msg = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                self._send(500, msg, "application/json; charset=utf-8")
            return

        if path == "/api/status":
            body = json.dumps(_srv().pipeline_status_payload(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return

        if path == "/api/ui/version":
            body = json.dumps(_srv().ui_version_payload(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return

        if path == "/api/run-report":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [""])[0].strip()
            pl = run_report_http_payload(jd)
            err = pl.get("error")
            if pl.get("ok"):
                code = 200
            elif err == "invalid_journal_date":
                code = 400
            elif err == "no_report":
                code = 404
            else:
                code = 400
            body = json.dumps(pl, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/episode-state":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [""])[0].strip()
            refresh_raw = (qs.get("refresh") or ["1"])[0].strip().lower()
            refresh = refresh_raw not in ("0", "false", "no", "off")
            pl = episode_state_http_payload(jd, refresh=refresh)
            err = pl.get("error")
            if pl.get("ok"):
                code = 200
            elif err == "invalid_journal_date":
                code = 400
            else:
                code = 400
            body = json.dumps(pl, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/b-roll/suggest":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [""])[0].strip()
            try:
                top = int((qs.get("top") or ["5"])[0])
            except ValueError:
                top = 5
            pe_raw = (qs.get("prefer_environment") or ["0"])[0].strip().lower()
            prefer_env = pe_raw in ("1", "true", "yes", "on")
            min_base_raw = (qs.get("min_base") or [""])[0].strip()
            if min_base_raw:
                try:
                    min_base = float(min_base_raw)
                except ValueError:
                    min_base = DEFAULT_MIN_BASE_SIMILARITY
            else:
                min_base = DEFAULT_MIN_BASE_SIMILARITY
            pl = b_roll_suggest_http_payload(jd, top, prefer_env, min_base_similarity=min_base)
            err = pl.get("error")
            if pl.get("ok"):
                code = 200
            elif err in ("invalid_journal_date", "invalid_date_id"):
                code = 400
            elif err in ("no_narration",):
                code = 404
            else:
                code = 400
            body = json.dumps(pl, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/b-roll/thumbnail":
            qs = parse_qs(parsed.query or "")
            rel = unquote((qs.get("rel") or [""])[0].strip())
            vp = _safe_repo_video_rel(rel)
            if not vp:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            jpg = _ensure_b_roll_thumbnail_jpeg(vp)
            if not jpg:
                print(
                    f"[pipeline_ui] B-roll thumbnail failed for {vp} (ffmpeg in PATH: "
                    f"{bool(_srv()._ffmpeg_executable())})",
                    file=sys.stderr,
                )
                if not _srv()._ffmpeg_executable():
                    self._send(
                        503,
                        b"ffmpeg not found in PATH (install ffmpeg and restart the server)",
                        "text/plain; charset=utf-8",
                    )
                else:
                    self._send(
                        502,
                        b"ffmpeg could not extract a thumbnail from this MP4",
                        "text/plain; charset=utf-8",
                    )
                return
            try:
                data = jpg.read_bytes()
            except OSError:
                self._send(500, b"Read error", "text/plain; charset=utf-8")
                return
            self._send(200, data, "image/jpeg")
            return

        if path == "/api/preview/anchor-image":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [""])[0].strip()
            seg_raw = (qs.get("segment") or ["1"])[0].strip()
            try:
                seg_i = int(seg_raw)
            except ValueError:
                self._send(400, b"bad segment", "text/plain; charset=utf-8")
                return
            if not jd:
                self._send(400, b"missing journal_date", "text/plain; charset=utf-8")
                return
            safe = _safe_journal_date(jd)
            if not safe:
                self._send(400, b"invalid journal_date", "text/plain; charset=utf-8")
                return
            did = _journal_date_to_date_id(safe)
            if not did:
                self._send(400, b"invalid date_id", "text/plain; charset=utf-8")
                return
            img_path = _srv()._anchor_image_path_for_segment(did, seg_i)
            if not img_path:
                self._send(404, b"anchor image not found", "text/plain; charset=utf-8")
                return
            suf = img_path.suffix.lower()
            if suf == ".png":
                ctype = "image/png"
            elif suf in (".jpg", ".jpeg"):
                ctype = "image/jpeg"
            elif suf == ".webp":
                ctype = "image/webp"
            else:
                ctype = "application/octet-stream"
            try:
                data = img_path.read_bytes()
            except OSError:
                self._send(500, b"read error", "text/plain; charset=utf-8")
                return
            self._send(200, data, ctype)
            return

        if path == "/api/preview/conversation-master":
            qs = parse_qs(parsed.query or "")
            jd = (qs.get("journal_date") or [""])[0].strip()
            run_raw = (qs.get("run_index") or ["0"])[0].strip()
            try:
                run_index = int(run_raw)
            except ValueError:
                self._send(400, b"bad run_index", "text/plain; charset=utf-8")
                return
            if not jd:
                self._send(400, b"missing journal_date", "text/plain; charset=utf-8")
                return
            safe = _safe_journal_date(jd)
            if not safe:
                self._send(400, b"invalid journal_date", "text/plain; charset=utf-8")
                return
            did = _journal_date_to_date_id(safe)
            if not did:
                self._send(400, b"invalid date_id", "text/plain; charset=utf-8")
                return
            from pipeline.conversation_scene_anchor import (
                discover_conversation_anchor_runs,
                master_anchor_path,
            )

            narr_path = _srv()._REPO_ROOT / "narrations" / f"narration{did}.json"
            if not narr_path.is_file():
                self._send(404, b"no narration", "text/plain; charset=utf-8")
                return
            narr = json.loads(narr_path.read_text(encoding="utf-8-sig"))
            runs = discover_conversation_anchor_runs(narr)
            if run_index < 0 or run_index >= len(runs):
                self._send(404, b"no conversation run", "text/plain; charset=utf-8")
                return
            mp = master_anchor_path(_srv()._REPO_ROOT / "movie-images" / did, runs[run_index])
            if not mp.is_file():
                self._send(404, b"master not found", "text/plain; charset=utf-8")
                return
            suf = mp.suffix.lower()
            if suf == ".png":
                ctype = "image/png"
            elif suf in (".jpg", ".jpeg"):
                ctype = "image/jpeg"
            elif suf == ".webp":
                ctype = "image/webp"
            else:
                ctype = "application/octet-stream"
            try:
                data = mp.read_bytes()
            except OSError:
                self._send(500, b"read error", "text/plain; charset=utf-8")
                return
            self._send(200, data, ctype)
            return

        if path == "/api/library/portraits":
            try:
                pl = library_portraits_payload()
            except Exception as exc:
                pl = {"ok": False, "error": "exception", "message": str(exc)}
            code = 200 if pl.get("ok") else 500
            body = json.dumps(pl, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/library/pipeline-portraits":
            try:
                pl = library_pipeline_portraits_payload()
            except Exception as exc:
                pl = {"ok": False, "error": "exception", "message": str(exc)}
            code = 200 if pl.get("ok") else 500
            body = json.dumps(pl, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path.startswith("/api/library/portrait/"):
            name = unquote(path[len("/api/library/portrait/") :])
            p = _safe_portrait_filename(name)
            if p is None:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            suf = p.suffix.lower()
            if suf == ".png":
                ctype = "image/png"
            elif suf in (".jpg", ".jpeg"):
                ctype = "image/jpeg"
            elif suf == ".webp":
                ctype = "image/webp"
            else:
                ctype = "application/octet-stream"
            try:
                data = p.read_bytes()
            except OSError:
                self._send(500, b"read error", "text/plain; charset=utf-8")
                return
            self._send(200, data, ctype)
            return

        if path.startswith("/api/library/voice-preview/"):
            name = unquote(path[len("/api/library/voice-preview/") :])
            p = _safe_voice_preview_filename(name)
            if p is None:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            suf = p.suffix.lower()
            if suf == ".mp3":
                ctype = "audio/mpeg"
            elif suf == ".m4a":
                ctype = "audio/mp4"
            elif suf == ".wav":
                ctype = "audio/wav"
            elif suf == ".ogg":
                ctype = "audio/ogg"
            else:
                ctype = "application/octet-stream"
            try:
                data = p.read_bytes()
            except OSError:
                self._send(500, b"read error", "text/plain; charset=utf-8")
                return
            self._send(200, data, ctype)
            return

        if path == "/api/library/prompt-packs":
            try:
                pl = library_prompt_packs_payload()
            except Exception as exc:
                pl = {"ok": False, "error": "exception", "message": str(exc)}
            code = 200 if pl.get("ok") else 500
            body = json.dumps(pl, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/library/diversity":
            try:
                pl = library_diversity_payload()
            except Exception as exc:
                pl = {"ok": False, "error": "exception", "message": str(exc)}
            code = 200 if pl.get("ok") else 500
            body = json.dumps(pl, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/library/week-arcs":
            try:
                pl = library_week_arcs_payload()
            except Exception as exc:
                pl = {"ok": False, "error": "exception", "message": str(exc)}
            code = 200 if pl.get("ok") else 500
            body = json.dumps(pl, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path.startswith("/static/"):
            rel = unquote(path[len("/static/") :])
            p = _safe_static_path(rel)
            if p is None:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            ctype = _STATIC_CONTENT_TYPES.get(p.suffix.lower(), "application/octet-stream")
            try:
                data = p.read_bytes()
            except OSError:
                self._send(500, b"read error", "text/plain; charset=utf-8")
                return
            self._send(200, data, ctype)
            return

        if path == "/" or path == "/index.html":
            if not _srv()._INDEX_HTML.exists():
                self._send(
                    500,
                    b"Missing index.html next to server.py",
                    "text/plain; charset=utf-8",
                )
                return
            self._send(200, _srv()._INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
            return

        self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = (parsed.path or "").rstrip("/") or "/"

        if path == "/api/library/diversity/regenerate-checker":
            body_in = self._read_json_body()
            code, result = regenerate_diversity_checker_http(
                body_in if isinstance(body_in, dict) else {}
            )
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/library/diversity/refresh":
            body_in = self._read_json_body()
            code, result = refresh_diversity_audit_http(
                body_in if isinstance(body_in, dict) else {}
            )
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/b-roll/add-from-preview":
            body_in = self._read_json_body()
            date_id = body_in.get("date_id")
            seg = body_in.get("segment_index")
            if not isinstance(date_id, str):
                date_id = str(date_id) if date_id is not None else ""
            result = b_roll_append_from_preview(date_id, seg)
            code = 200 if result.get("ok") else 400
            if result.get("error") == "no_mp4":
                code = 404
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/b-roll/materialize":
            body_in = self._read_json_body()
            code, result = b_roll_materialize_http(body_in)
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/narration/update-video-prompt":
            body_in = self._read_json_body()
            code, result = narration_update_video_prompt_http(
                body_in if isinstance(body_in, dict) else {}
            )
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/narration/update-talking-head-prompt":
            body_in = self._read_json_body()
            code, result = narration_update_talking_head_prompt_http(
                body_in if isinstance(body_in, dict) else {}
            )
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/narration/update-opening-frame":
            body_in = self._read_json_body()
            code, result = narration_update_opening_frame_http(
                body_in if isinstance(body_in, dict) else {}
            )
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/narration/update-narration":
            body_in = self._read_json_body()
            code, result = narration_update_narration_http(
                body_in if isinstance(body_in, dict) else {}
            )
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/narration/update-json":
            body_in = self._read_json_body()
            code, result = narration_update_raw_json_http(
                body_in if isinstance(body_in, dict) else {}
            )
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/youtube/upload-from-preview":
            body_in = self._read_json_body()
            pr = body_in.get("privacy") if isinstance(body_in.get("privacy"), str) else "public"
            jd_raw = body_in.get("journal_date")
            jd_u: str | None = None
            if isinstance(jd_raw, str) and jd_raw.strip():
                jd_u = jd_raw.strip()
            force_u = bool(body_in.get("force")) if isinstance(body_in, dict) else False
            allow_gap_u = bool(body_in.get("allow_gap")) if isinstance(body_in, dict) else False
            result = youtube_upload_from_preview(
                pr, journal_date=jd_u, force=force_u, allow_gap=allow_gap_u
            )
            code = 200 if result.get("ok") else 500
            err = result.get("error")
            if err == "no_video":
                code = 404
            elif err in ("no_manifest", "bad_video_name", "invalid_path"):
                code = 400
            elif err in ("already_uploaded", "upload_sequence"):
                code = 409
            elif err == "timeout":
                code = 504
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/cancel":
            result = _srv().cancel_pipeline_run()
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return

        if path == "/api/ui/verify":
            body_in = self._read_json_body()
            client_fp = ""
            if isinstance(body_in, dict):
                client_fp = str(body_in.get("client_fingerprint") or "")
            result = _srv().ui_verify_payload(client_fp)
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return

        if path == "/api/ui/restart":
            result = _srv().ui_restart_payload()
            err = result.get("error")
            if result.get("ok"):
                code = 200
            elif err == "run_in_progress":
                code = 409
            elif err == "reload_not_available":
                code = 503
            else:
                code = 500
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/fal/scene-anchor-i2i":
            body_in = self._read_json_body()
            sync = _boolish((body_in or {}).get("sync"), False)
            if sync:
                try:
                    result = _srv().fal_scene_anchor_i2i_http_payload(
                        body_in if isinstance(body_in, dict) else {}
                    )
                except Exception as exc:
                    result = {
                        "ok": False,
                        "error": "scene_anchor_exception",
                        "message": str(exc),
                    }
            else:
                result = _srv().start_fal_scene_anchor_i2i_job(
                    body_in if isinstance(body_in, dict) else {}
                )
            code = _srv()._scene_anchor_i2i_http_code(result)
            if result.get("async") and result.get("ok"):
                code = 202
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/conversation-scene-anchor/settings":
            body_in = self._read_json_body()
            result = _srv().update_conversation_scene_anchor_settings(
                body_in if isinstance(body_in, dict) else {}
            )
            code = 200 if result.get("ok") else 400
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/conversation-scene-anchor/shared-setting":
            body_in = self._read_json_body()
            code, result = _srv().conversation_shared_setting_http(
                body_in if isinstance(body_in, dict) else {}
            )
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/conversation-scene-anchor/regenerate-master":
            body_in = self._read_json_body()
            result = _srv().start_conv_master_regen_job(
                body_in if isinstance(body_in, dict) else {}
            )
            code = 202 if result.get("async") and result.get("ok") else 400
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/anchor-preview/build-anchors":
            body_in = self._read_json_body()
            result = _srv().start_anchor_preview_job(
                body_in if isinstance(body_in, dict) else {}, "anchors"
            )
            code = 202 if result.get("async") and result.get("ok") else 400
            if result.get("error") == "run_in_progress":
                code = 409
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/anchor-preview/assemble":
            body_in = self._read_json_body()
            result = _srv().start_anchor_preview_job(
                body_in if isinstance(body_in, dict) else {}, "assemble"
            )
            code = 202 if result.get("async") and result.get("ok") else 400
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/generate-tts":
            body_in = self._read_json_body()
            result = generate_tts_run(body_in if isinstance(body_in, dict) else {})
            err = result.get("error")
            if result.get("ok"):
                code = 200
            elif err == "run_in_progress":
                code = 409
            elif err in ("missing_journal_date", "invalid_journal_date", "invalid_date_id"):
                code = 400
            elif err == "no_narration":
                code = 404
            else:
                code = 500
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/generate-video-reuse-anchors":
            body_in = self._read_json_body()
            result = generate_video_reuse_anchors_run(body_in if isinstance(body_in, dict) else {})
            err = result.get("error")
            if result.get("ok"):
                code = 200
            elif err == "run_in_progress":
                code = 409
            elif err in ("missing_journal_date", "invalid_journal_date", "invalid_date_id"):
                code = 400
            elif err == "no_narration":
                code = 404
            else:
                code = 500
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/reset-day":
            body_in = self._read_json_body()
            result = reset_day_artifacts_run(body_in if isinstance(body_in, dict) else {})
            err = result.get("error")
            if result.get("ok"):
                code = 200
            elif err == "run_in_progress":
                code = 409
            elif err in ("missing_journal_date", "invalid_journal_date", "invalid_date_id"):
                code = 400
            elif err == "reset_failed":
                code = 500
            else:
                code = 400
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/regenerate-video-segment":
            body_in = self._read_json_body()
            result = regenerate_video_segment_run(body_in if isinstance(body_in, dict) else {})
            err = result.get("error")
            if result.get("ok"):
                code = 200
            elif err == "run_in_progress":
                code = 409
            elif err in (
                "missing_journal_date",
                "invalid_journal_date",
                "invalid_date_id",
                "bad_segment_index",
                "unsupported_vendor",
                "options_load_failed",
                "missing_script",
            ):
                code = 400
            elif err in ("no_narration", "no_segments"):
                code = 404
            elif err == "segment_out_of_range":
                code = 400
            else:
                code = 500 if err == "pipeline_failed" else 400
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path == "/api/assemble-video":
            body_in = self._read_json_body()
            result = assemble_video_run(body_in if isinstance(body_in, dict) else {})
            err = result.get("error")
            if result.get("ok"):
                code = 200
            elif err == "run_in_progress":
                code = 409
            elif err in (
                "missing_journal_date",
                "invalid_journal_date",
                "invalid_date_id",
            ):
                code = 400
            elif err == "no_audio_timeline":
                code = 404
            else:
                code = 500 if err == "pipeline_failed" else 400
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return

        if path != "/api/run":
            print(
                f"[pipeline_ui] POST path not handled: {path!r} (raw {self.path!r})",
                file=sys.stderr,
            )
            err_body = json.dumps(
                {"ok": False, "error": "not_found", "path": path},
                ensure_ascii=False,
            ).encode("utf-8")
            self._send(404, err_body, "application/json; charset=utf-8")
            return

        body = self._read_json_body()
        if body.get("action") == "reset_day":
            result = reset_day_artifacts_run(body)
            err = result.get("error")
            if result.get("ok"):
                code = 200
            elif err == "run_in_progress":
                code = 409
            elif err in ("missing_journal_date", "invalid_journal_date", "invalid_date_id"):
                code = 400
            elif err == "reset_failed":
                code = 500
            else:
                code = 400
            payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(code, payload, "application/json; charset=utf-8")
            return

        values = body.get("values")
        if not isinstance(values, dict):
            values = {}
        workflow_raw = body.get("workflow")
        workflow = workflow_raw.strip().lower() if isinstance(workflow_raw, str) else None

        cmd: list[str] | None = None
        spec: dict[str, Any] | None = None
        run_logged = False
        try:
            spec = load_options()
            if spec.get("error"):
                raise RuntimeError(spec["error"])
            _backend = spec.get("backend") or {}
            _py = _resolve_python(
                os.environ.get("PIPELINE_UI_PYTHON"),
                list(
                    _backend.get("python_candidates")
                    or [".venv/Scripts/python.exe", ".venv/bin/python"]
                ),
            )
            cmd = build_argv(
                values, spec, workflow, repo_root=_srv()._REPO_ROOT, python_executable=_py
            )
            log_payload: dict[str, Any] = {
                "inputs": {
                    "form_values": values,
                    "workflow": workflow or "full",
                    "options_title": spec.get("title"),
                },
                "source": "pipeline_ui POST /api/run",
            }
            run = _srv()._run_locked_pipeline_cmd(cmd, log_payload)
            if run.get("_blocked"):
                err = {"error": run.get("error", "A pipeline run is already in progress.")}
                payload = json.dumps(err, ensure_ascii=False).encode("utf-8")
                self._send(409, payload, "application/json; charset=utf-8")
                return

            run_logged = True
            out: dict[str, Any] = {
                "command": run["command"],
                "cwd": run["cwd"],
                "returncode": run["returncode"],
                "stdout": run["stdout"],
                "stderr": run["stderr"],
                "cancelled": run.get("cancelled", False),
            }
            payload = json.dumps(out, indent=2, ensure_ascii=False).encode("utf-8")
            ok_exit = run["returncode"] == 0 or run.get("cancelled", False)
            try:
                self._send(200 if ok_exit else 500, payload, "application/json; charset=utf-8")
            except (BrokenPipeError, ConnectionResetError, OSError):
                # Run finished and is logged; browser disconnected (reload, tab close, timeout).
                return
        except Exception as e:
            err_obj: dict[str, Any] = {"error": str(e)}
            if cmd is not None:
                err_obj["command"] = cmd
            if not run_logged:
                _srv()._write_pipeline_ui_last_run(
                    {
                        "inputs": {
                            "form_values": values,
                            "workflow": workflow or "full",
                            "options_title": (spec or {}).get("title"),
                        },
                        "command": cmd,
                        "cwd": str(_srv()._REPO_ROOT) if _srv()._REPO_ROOT else None,
                        "returncode": None,
                        "stdout": None,
                        "stderr": None,
                        "error": str(e),
                    }
                )
            try:
                err = json.dumps(err_obj).encode("utf-8")
                self._send(500, err, "application/json; charset=utf-8")
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
