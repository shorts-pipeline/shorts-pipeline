"""
Shared retry/backoff for fal_client.subscribe and FAL-related HTTP downloads.

Environment (optional):
  FAL_SUBSCRIBE_MAX_RETRIES — fal_client.subscribe attempts (default 4)
  FAL_HTTP_MAX_RETRIES — streaming / small GET attempts (default 4)
  FAL_RETRY_BASE_SECONDS — initial backoff before exponential growth (default 2.0)
"""

from __future__ import annotations

import os
import random
import sys
import time
from collections.abc import Callable
from pathlib import Path

import requests
import requests.exceptions


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
        return v if v > 0 else default
    except ValueError:
        return default


def is_transient_fal_subscribe_error(exc: BaseException) -> bool:
    """True for rate limits, gateway errors, and network timeouts (safe to retry)."""
    msg = str(exc).lower()
    if "content_policy" in msg or "content checker" in msg or "flagged by" in msg:
        return False
    if "422" in msg and ("validation" in msg or "unprocessable" in msg or "face_detection" in msg):
        return False
    tokens = (
        "429",
        "503",
        "502",
        "504",
        "408",
        "rate limit",
        "too many requests",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "econnreset",
        "broken pipe",
        "bad gateway",
        "gateway timeout",
        "service unavailable",
        "over capacity",
        "try again",
        "remote end closed",
    )
    return any(t in msg for t in tokens)


def is_transient_http_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if isinstance(exc, requests.Timeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return True
    chunked = getattr(requests.exceptions, "ChunkedEncodingError", None)
    if chunked is not None and isinstance(exc, chunked):
        return True
    tokens = (
        "429",
        "503",
        "502",
        "504",
        "timeout",
        "timed out",
        "connection reset",
        "temporarily unavailable",
    )
    return any(t in msg for t in tokens)


def fal_subscribe_with_retries[T](
    label: str,
    fn: Callable[[], T],
    *,
    max_attempts: int | None = None,
    base_delay: float | None = None,
) -> T:
    """
    Run ``fn`` (typically ``lambda: fal_client.subscribe(...)``) with exponential backoff.

    Env overrides: ``FAL_SUBSCRIBE_MAX_RETRIES`` (default 4), ``FAL_RETRY_BASE_SECONDS`` (default 2.0).
    """
    attempts = (
        max_attempts if max_attempts is not None else _env_int("FAL_SUBSCRIBE_MAX_RETRIES", 4)
    )
    base = base_delay if base_delay is not None else _env_float("FAL_RETRY_BASE_SECONDS", 2.0)
    last: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except BaseException as e:
            last = e
            if i >= attempts or not is_transient_fal_subscribe_error(e):
                raise
            sleep_s = min(60.0, base * (2 ** (i - 1)) + random.uniform(0, 0.5))
            print(
                f"   [WARN] {label}: transient FAL error (attempt {i}/{attempts}): {e}; "
                f"retrying in {sleep_s:.1f}s...",
                file=sys.stderr,
            )
            time.sleep(sleep_s)
    assert last is not None
    raise last


def http_stream_to_file_with_retries(
    url: str,
    dest: Path,
    *,
    label: str = "FAL download",
    max_attempts: int | None = None,
    base_delay: float | None = None,
    chunk_size: int = 16_384,
    timeout: int = 600,
) -> None:
    """GET ``url`` (streaming) to ``dest`` with retries on transient network errors."""
    attempts = max_attempts if max_attempts is not None else _env_int("FAL_HTTP_MAX_RETRIES", 4)
    base = base_delay if base_delay is not None else _env_float("FAL_RETRY_BASE_SECONDS", 2.0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            resp = requests.get(url, stream=True, timeout=timeout)
            resp.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".partial")
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size):
                    if chunk:
                        f.write(chunk)
            tmp.replace(dest)
            return
        except BaseException as e:
            last = e
            for p in (dest.with_suffix(dest.suffix + ".partial"),):
                try:
                    if p.is_file():
                        p.unlink(missing_ok=True)
                except OSError:
                    pass
            if i >= attempts or not is_transient_http_error(e):
                raise
            sleep_s = min(60.0, base * (2 ** (i - 1)) + random.uniform(0, 0.5))
            print(
                f"   [WARN] {label}: transient download error (attempt {i}/{attempts}): {e}; "
                f"retrying in {sleep_s:.1f}s...",
                file=sys.stderr,
            )
            time.sleep(sleep_s)
    assert last is not None
    raise last


def fal_upload_with_retries[T](
    label: str,
    fn: Callable[[], T],
    *,
    max_attempts: int | None = None,
    base_delay: float | None = None,
) -> T:
    """Retry ``fal_client.upload_file`` (or similar) on transient errors."""
    return fal_subscribe_with_retries(label, fn, max_attempts=max_attempts, base_delay=base_delay)


def http_get_bytes_with_retries(
    url: str,
    *,
    label: str = "FAL GET",
    max_attempts: int | None = None,
    base_delay: float | None = None,
    timeout: int = 120,
) -> bytes:
    """Small non-streaming GET (e.g. anchor image) with retries."""
    attempts = max_attempts if max_attempts is not None else _env_int("FAL_HTTP_MAX_RETRIES", 4)
    base = base_delay if base_delay is not None else _env_float("FAL_RETRY_BASE_SECONDS", 2.0)
    last: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.content
        except BaseException as e:
            last = e
            if i >= attempts or not is_transient_http_error(e):
                raise
            sleep_s = min(60.0, base * (2 ** (i - 1)) + random.uniform(0, 0.5))
            print(
                f"   [WARN] {label}: transient GET error (attempt {i}/{attempts}): {e}; "
                f"retrying in {sleep_s:.1f}s...",
                file=sys.stderr,
            )
            time.sleep(sleep_s)
    assert last is not None
    raise last
