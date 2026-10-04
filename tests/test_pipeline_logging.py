"""Tests for pipeline_logging redaction and api_bodies rotation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import pipeline_logging as pl


def test_redact_data_uri() -> None:
    huge = "data:image/png;base64," + ("Z" * 5000)
    out = pl.redact_for_api_log({"image_urls": [huge, "https://x/y"]})
    assert isinstance(out, dict)
    urls = out["image_urls"]
    assert urls[0].startswith("[redacted data-uri image/png;")
    assert urls[1] == "https://x/y"


def test_rotate_api_bodies_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pl, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(pl, "LOG_FILE", tmp_path / "pipeline.log")
    monkeypatch.setattr(pl, "BODY_LOG_FILE", tmp_path / "api_bodies.log")
    monkeypatch.setattr(pl, "MAX_BODY_LOG_BYTES", 900)
    monkeypatch.setattr(pl, "MAX_BODY_ARCHIVES", 3)
    monkeypatch.setattr(pl, "MAX_FIELD_CHARS", 50_000)

    pl.log_api_call_with_bodies(
        "test",
        "m1",
        request_body={"payload": "x" * 1200},
        response_body={"ok": True},
    )
    p = tmp_path / "api_bodies.log"
    assert p.is_file() and p.stat().st_size > 900

    pl.log_api_call_with_bodies("test", "m2", request_body={"b": 2}, response_body={"ok": True})

    assert (tmp_path / "api_bodies.log.1").is_file()
    tail = p.read_text(encoding="utf-8").strip()
    entry = json.loads(tail)
    assert entry["method"] == "m2"
