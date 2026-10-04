"""
Pipeline logging for API calls and large file creation.

- ``logs/pipeline.log`` — one JSON line per API call (metadata only, no large bodies).
- ``logs/api_bodies.log`` — one JSON line per call with **request** / **response** previews
  (redacted for huge ``data:`` URIs; then truncated to a per-field byte budget).

When ``api_bodies.log`` exceeds ``PIPELINE_API_LOG_FILE_MAX_BYTES`` (env, default 20 MiB),
it is rotated to ``api_bodies.log.1``, previous ``.1`` → ``.2``, … and the oldest archive
is removed (``PIPELINE_API_LOG_ARCHIVE_COUNT`` archives kept, default 9).

Environment overrides (optional):

- ``PIPELINE_API_LOG_MAX_FIELD_CHARS`` — max characters per serialized request/response
  field after redaction (default 262144 = 256 KiB).
- ``PIPELINE_API_LOG_FILE_MAX_BYTES`` — rotate active body log when larger than this (default 20971520).
- ``PIPELINE_API_LOG_ARCHIVE_COUNT`` — number of rotated files ``api_bodies.log.1`` … ``.N`` (default 9).
"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGS_DIR = Path("logs")
LOG_FILE = LOGS_DIR / "pipeline.log"
BODY_LOG_FILE = LOGS_DIR / "api_bodies.log"
LARGE_FILE_THRESHOLD_BYTES = 100 * 1024  # 100 KB

_TRUNCATE_SUFFIX = " ... [truncated]"


# Defaults (override with env vars documented in module docstring)
def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else default
    except ValueError:
        return default


MAX_FIELD_CHARS = _env_int("PIPELINE_API_LOG_MAX_FIELD_CHARS", 262_144)
MAX_BODY_LOG_BYTES = _env_int("PIPELINE_API_LOG_FILE_MAX_BYTES", 20 * 1024 * 1024)
MAX_BODY_ARCHIVES = _env_int("PIPELINE_API_LOG_ARCHIVE_COUNT", 9)

_RE_DATA_URI = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<b64>.+)$", re.DOTALL)


def _ensure_log_dir() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR


def _truncate(s: str, max_chars: int) -> str:
    if not s or len(s) <= max_chars:
        return s or ""
    return s[: max_chars - len(_TRUNCATE_SUFFIX)] + _TRUNCATE_SUFFIX


def redact_for_api_log(obj: Any, _depth: int = 0) -> Any:
    """
    Deep-copy JSON-like structures and replace embedded data-URIs / huge strings
    so logs stay useful without multi-megabyte lines.
    """
    if _depth > 40:
        return "[max depth]"
    if isinstance(obj, str):
        m = _RE_DATA_URI.match(obj.strip())
        if m:
            n = len(obj)
            return f"[redacted data-uri {m.group('mime')}; {n} chars]"
        if len(obj) > 64_000:
            return _truncate(obj, 4096) + f" [string total {len(obj)} chars]"
        return obj
    if isinstance(obj, (bytes, bytearray)):
        return f"[bytes len={len(obj)}]"
    if isinstance(obj, dict):
        return {str(k): redact_for_api_log(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        if len(obj) > 500:
            head = [redact_for_api_log(v, _depth + 1) for v in obj[:250]]
            return head + [f"[list truncated: {len(obj)} items total]"]
        return [redact_for_api_log(v, _depth + 1) for v in obj]
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    # Fallback: repr, capped
    s = repr(obj)
    return _truncate(s, 8000)


def _serialize_for_log(value: Any) -> str:
    redacted = redact_for_api_log(
        copy.deepcopy(value) if isinstance(value, (dict, list)) else value
    )
    if redacted is None:
        return ""
    if isinstance(redacted, str):
        body = redacted
    else:
        try:
            body = json.dumps(redacted, ensure_ascii=False, indent=0)
        except (TypeError, ValueError):
            body = str(redacted)
    return _truncate(body, MAX_FIELD_CHARS)


def _rotate_body_log_if_needed() -> None:
    """When ``api_bodies.log`` is too large, shift numbered archives and start a new file."""
    if not BODY_LOG_FILE.exists():
        return
    try:
        if BODY_LOG_FILE.stat().st_size <= MAX_BODY_LOG_BYTES:
            return
    except OSError:
        return
    _ensure_log_dir()
    oldest = LOGS_DIR / f"api_bodies.log.{MAX_BODY_ARCHIVES}"
    try:
        oldest.unlink(missing_ok=True)
    except OSError:
        pass
    for i in range(MAX_BODY_ARCHIVES - 1, 0, -1):
        src = LOGS_DIR / f"api_bodies.log.{i}"
        if not src.is_file():
            continue
        dst = LOGS_DIR / f"api_bodies.log.{i + 1}"
        try:
            src.replace(dst)
        except OSError:
            pass
    try:
        BODY_LOG_FILE.replace(LOGS_DIR / "api_bodies.log.1")
    except OSError:
        pass


def _write_log(event_type: str, details: dict) -> None:
    entry = {
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event": event_type,
        **details,
    }
    _ensure_log_dir()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_api_call(
    provider: str,
    method: str,
    *,
    model: str | None = None,
    extra: dict | None = None,
) -> None:
    """Log an API call without bodies (same metadata as body logs)."""
    log_api_call_with_bodies(
        provider,
        method,
        request_body=None,
        response_body=None,
        model=model,
        extra=extra,
    )


def log_api_call_with_bodies(
    provider: str,
    method: str,
    *,
    request_body: str | list | dict | None = None,
    response_body: str | list | dict | None = None,
    model: str | None = None,
    extra: dict | None = None,
) -> None:
    """
    Log metadata to ``pipeline.log`` and request/response previews to ``api_bodies.log``.

    ``request_body`` / ``response_body`` may be str or JSON-serializable structures.
    Large ``data:...;base64,...`` strings are replaced with short redaction markers.
    """
    details = {"provider": provider, "method": method}
    if model:
        details["model"] = model
    if extra:
        details["extra"] = extra
    _write_log("API_CALL", details)

    req_preview = _serialize_for_log(request_body) if request_body is not None else ""
    resp_preview = _serialize_for_log(response_body) if response_body is not None else ""
    entry = {
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event": "API_CALL_BODIES",
        "provider": provider,
        "method": method,
        "request_preview": req_preview,
        "response_preview": resp_preview,
    }
    if model:
        entry["model"] = model
    if extra:
        entry["extra"] = extra
    _ensure_log_dir()
    _rotate_body_log_if_needed()
    try:
        with open(BODY_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def log_file_created(path: Path | str, size_bytes: int) -> None:
    """Log creation of a large file (>= LARGE_FILE_THRESHOLD_BYTES)."""
    if size_bytes < LARGE_FILE_THRESHOLD_BYTES:
        return
    path_str = str(path) if isinstance(path, Path) else path
    _write_log("FILE_CREATED", {"path": path_str, "size_bytes": size_bytes})


def log_video_complete(
    date_id: str,
    output_path: Path | str,
    *,
    narration_vendor: str,
    tts_vendor: str,
    video_vendor: str,
) -> None:
    """Log that a final output video was produced and which vendors were used."""
    path_str = str(output_path) if isinstance(output_path, Path) else output_path
    _write_log(
        "VIDEO_COMPLETE",
        {
            "date_id": date_id,
            "output": path_str,
            "narration_vendor": narration_vendor,
            "tts_vendor": tts_vendor,
            "video_vendor": video_vendor,
        },
    )
