"""Tests for pipeline.day_reset — archive day artifacts before a fresh run."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.day_reset import (
    archive_day_artifacts,
    day_artifact_paths,
    journal_date_from_date_id,
    revert_last_run_date_if_matches,
)


def test_journal_date_from_date_id():
    assert journal_date_from_date_id("18040531") == "1804-05-31"


def test_day_artifact_paths_lists_all_kinds(tmp_path: Path):
    did = "18040531"
    (tmp_path / "narrations").mkdir(parents=True)
    (tmp_path / "narrations" / f"narration{did}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "narrations" / f"narration{did}_voice.json").write_text("{}", encoding="utf-8")
    (tmp_path / "narrations" / f"narration{did}-backup.json").write_text("{}", encoding="utf-8")
    (tmp_path / "audio" / did / "final.mp3").parent.mkdir(parents=True)
    (tmp_path / "audio" / did / "final.mp3").write_bytes(b"x")
    (tmp_path / "movie-images" / did / "01.mp4").parent.mkdir(parents=True)
    (tmp_path / "movie-images" / did / "01.mp4").write_bytes(b"v")
    (tmp_path / "output").mkdir(parents=True)
    (tmp_path / "output" / f"lewis_clark_{did}_video.mp4").write_bytes(b"o")
    (tmp_path / "output" / f"lewis_clark_{did}_video.manifest.json").write_text(
        "{}", encoding="utf-8"
    )

    paths = day_artifact_paths(tmp_path, did)
    names = {p.name for p in paths if p.is_file()}
    assert f"narration{did}.json" in names
    assert f"narration{did}_voice.json" in names
    assert any(p.is_dir() and p.name == did for p in paths)
    assert len(paths) >= 6


def test_archive_day_artifacts_moves_and_writes_manifest(tmp_path: Path):
    did = "18040531"
    (tmp_path / "narrations").mkdir(parents=True)
    (tmp_path / "narrations" / f"narration{did}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "audio" / did).mkdir(parents=True)
    (tmp_path / "audio" / did / "final.mp3").write_bytes(b"a")
    (tmp_path / "movie-images" / did).mkdir(parents=True)
    (tmp_path / "movie-images" / did / "01.mp4").write_bytes(b"v")
    (tmp_path / "output").mkdir(parents=True)
    (tmp_path / "output" / f"lewis_clark_{did}_video.mp4").write_bytes(b"o")

    result = archive_day_artifacts(tmp_path, did, timestamp="20260522-120000")
    assert result.ok
    assert result.backup_dir is not None
    assert not (tmp_path / "narrations" / f"narration{did}.json").exists()
    assert not (tmp_path / "audio" / did).exists()
    assert not (tmp_path / "movie-images" / did).exists()
    assert not (tmp_path / "output" / f"lewis_clark_{did}_video.mp4").exists()
    assert (result.backup_dir / "narrations" / f"narration{did}.json").is_file()
    assert (result.backup_dir / "reset_manifest.json").is_file()
    manifest = json.loads((result.backup_dir / "reset_manifest.json").read_text(encoding="utf-8"))
    assert manifest["date_id"] == did


def test_archive_day_artifacts_dry_run_no_moves(tmp_path: Path):
    did = "18040101"
    nar = tmp_path / "narrations" / f"narration{did}.json"
    nar.parent.mkdir(parents=True)
    nar.write_text("{}", encoding="utf-8")
    result = archive_day_artifacts(tmp_path, did, dry_run=True, timestamp="20260522-120000")
    assert result.ok
    assert nar.is_file()
    assert result.moved == ["narrations/narration18040101.json"]


def test_revert_last_run_date_if_matches(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "run_daily_state.json").write_text(
        json.dumps({"last_date": "1804-05-31"}),
        encoding="utf-8",
    )
    je = tmp_path / "journal-entries"
    je.mkdir()
    (je / "1804-05-20.xml").write_text("<x/>", encoding="utf-8")
    (je / "1804-05-31.xml").write_text("<x/>", encoding="utf-8")

    new_last = revert_last_run_date_if_matches(tmp_path, "1804-05-31")
    assert new_last == "1804-05-20"
    data = json.loads((state / "run_daily_state.json").read_text(encoding="utf-8"))
    assert data["last_date"] == "1804-05-20"


def test_revert_last_run_date_no_op_when_different(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "run_daily_state.json").write_text(
        json.dumps({"last_date": "1804-06-01"}),
        encoding="utf-8",
    )
    assert revert_last_run_date_if_matches(tmp_path, "1804-05-31") == "1804-06-01"
