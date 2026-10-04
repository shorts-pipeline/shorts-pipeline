"""Smoke tests for root pipeline scripts (dry-run and preflight wiring)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SMOKE_DATE_ID = "18049999"


def _load_script_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _REPO / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _segment(**kwargs: object) -> dict:
    base = {
        "segment_index": 1,
        "stage_direction": "[CUT TO – SCENE]",
        "narration": "Smoke test narration beat.",
        "video_prompt": "Missouri River at dawn, mist on the water.",
        "visual_mode": "b_roll",
    }
    base.update(kwargs)
    return base


def _write_smoke_narration(
    root: Path,
    date_id: str,
    *,
    segments: list[dict] | None = None,
    **extra: object,
) -> Path:
    if segments is None:
        segments = [_segment()]
    narr_dir = root / "narrations"
    narr_dir.mkdir(parents=True, exist_ok=True)
    path = narr_dir / f"narration{date_id}.json"
    payload = {
        "narration_version": "2.0",
        "title": "Smoke test",
        "narration_script": segments,
        **extra,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def smoke_cwd(tmp_path, monkeypatch):
    """Minimal repo layout under tmp_path; copy narration config when present."""
    monkeypatch.chdir(tmp_path)
    cfg_src = _REPO / "config" / "narration_config.json"
    if cfg_src.is_file():
        cfg_dst = tmp_path / "config" / "narration_config.json"
        cfg_dst.parent.mkdir(parents=True, exist_ok=True)
        cfg_dst.write_text(cfg_src.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def test_narration_to_video_fal_dry_run_exits_zero(smoke_cwd, monkeypatch) -> None:
    _write_smoke_narration(smoke_cwd, _SMOKE_DATE_ID)
    ntv = _load_script_module("narration_to_video_smoke", "narration-to-video.py")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "narration-to-video.py",
            _SMOKE_DATE_ID,
            "--vendor",
            "fal",
            "--dry-run",
            "--wide-screen",
        ],
    )
    ntv.main()  # dry-run returns normally (no sys.exit)


def test_narration_to_video_fal_dry_run_prints_plan(smoke_cwd, monkeypatch, capsys) -> None:
    _write_smoke_narration(smoke_cwd, _SMOKE_DATE_ID)
    ntv = _load_script_module("narration_to_video_smoke2", "narration-to-video.py")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "narration-to-video.py",
            _SMOKE_DATE_ID,
            "--vendor",
            "fal",
            "--dry-run",
        ],
    )
    ntv.main()
    out = capsys.readouterr()
    assert "[DRY-RUN]" in out.out
    assert _SMOKE_DATE_ID in out.out
    assert "fal" in out.out.lower()


def test_narration_to_mp3_preflight_rejects_two_cast_speakers(
    smoke_cwd, monkeypatch, capsys
) -> None:
    _write_smoke_narration(
        smoke_cwd,
        _SMOKE_DATE_ID,
        segments=[
            _segment(
                dialogue=[
                    {"speaker_id": "clark", "text": "Line one."},
                    {"speaker_id": "lewis", "text": "Line two."},
                ]
            )
        ],
    )
    mp3 = _load_script_module("narration_to_mp3_smoke_fail", "narration-to-mp3.py")
    monkeypatch.setattr(
        sys,
        "argv",
        ["narration-to-mp3.py", _SMOKE_DATE_ID, "--tts", "openai"],
    )
    with pytest.raises(SystemExit) as exc:
        mp3.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "preflight" in err.lower()
    assert "One-speaker-per-segment" in err


def test_narration_to_mp3_preflight_runs_before_tts(smoke_cwd, monkeypatch) -> None:
    _write_smoke_narration(smoke_cwd, _SMOKE_DATE_ID)
    mp3 = _load_script_module("narration_to_mp3_smoke_ok", "narration-to-mp3.py")
    preflight_calls: list[dict] = []

    def _track_preflight(**kwargs):
        preflight_calls.append(kwargs)
        from pipeline.automation_gates import preflight_narration_to_mp3

        return preflight_narration_to_mp3(**kwargs)

    monkeypatch.setattr(mp3, "preflight_narration_to_mp3", _track_preflight)

    def _reach_tts(*_args, **_kwargs):
        raise RuntimeError("REACHED_TTS")

    monkeypatch.setattr(mp3, "synthesize_openai", _reach_tts)

    monkeypatch.setattr(
        sys,
        "argv",
        ["narration-to-mp3.py", _SMOKE_DATE_ID, "--tts", "openai"],
    )
    with pytest.raises(RuntimeError, match="REACHED_TTS"):
        mp3.main()
    assert len(preflight_calls) == 1
    assert preflight_calls[0]["date_id"] == _SMOKE_DATE_ID


def test_narration_to_mp3_no_preflight_skips_gate(smoke_cwd, monkeypatch) -> None:
    _write_smoke_narration(
        smoke_cwd,
        _SMOKE_DATE_ID,
        segments=[
            _segment(
                dialogue=[
                    {"speaker_id": "clark", "text": "Line one."},
                    {"speaker_id": "lewis", "text": "Line two."},
                ]
            )
        ],
    )
    mp3 = _load_script_module("narration_to_mp3_smoke_skip", "narration-to-mp3.py")
    preflight_calls: list[dict] = []

    def _track_preflight(**kwargs):
        preflight_calls.append(kwargs)
        from pipeline.automation_gates import preflight_narration_to_mp3

        return preflight_narration_to_mp3(**kwargs)

    monkeypatch.setattr(mp3, "preflight_narration_to_mp3", _track_preflight)

    def _reach_tts(*_args, **_kwargs):
        raise RuntimeError("REACHED_TTS")

    monkeypatch.setattr(mp3, "synthesize_openai", _reach_tts)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "narration-to-mp3.py",
            _SMOKE_DATE_ID,
            "--tts",
            "openai",
            "--no-preflight",
        ],
    )
    with pytest.raises(RuntimeError, match="REACHED_TTS"):
        mp3.main()
    assert preflight_calls == []
