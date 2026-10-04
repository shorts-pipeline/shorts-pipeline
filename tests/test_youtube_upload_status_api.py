"""Tests for pipeline_ui.server.youtube_upload_status_for_ui."""

from __future__ import annotations

import pytest

import video_manifest


@pytest.fixture
def ui_repo(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    arch = tmp_path / "archive"
    arch.mkdir()
    import pipeline_ui.server as srv

    monkeypatch.setattr(srv, "_REPO_ROOT", tmp_path)
    return srv, out, arch


def test_youtube_upload_status_empty(ui_repo):
    srv, _out, _arch = ui_repo
    data = srv.youtube_upload_status_for_ui(scan_archive=True)
    assert data["ok"] is True
    assert data["last_date_id"] is None
    assert data["pending_count"] == 0


def test_youtube_upload_status_last_and_pending(ui_repo):
    srv, out, _arch = ui_repo
    for date_id in ("18030830", "18030901"):
        mpath = out / f"lewis_clark_{date_id}_video.manifest.json"
        vpath = out / f"lewis_clark_{date_id}_video.mp4"
        vpath.write_bytes(b"x")
        video_manifest.save(
            mpath,
            {
                "date_id": date_id,
                "output": vpath.name,
            },
        )
    video_manifest.update_youtube(
        out / "lewis_clark_18030830_video.manifest.json",
        "vid_old",
        "https://youtu.be/old",
    )
    data = srv.youtube_upload_status_for_ui(scan_archive=False)
    assert data["last_date_id"] == "18030830"
    assert data["last_journal_date"] == "1803-08-30"
    assert data["uploaded_count"] == 1
    assert data["pending_count"] == 1
    assert data["oldest_pending_journal_date"] == "1803-09-01"


def test_youtube_upload_status_archive_included(ui_repo):
    srv, out, arch = ui_repo
    date_id = "18031001"
    arch_out = arch / "run1" / "output"
    arch_out.mkdir(parents=True)
    mpath = arch_out / f"lewis_clark_{date_id}_video.manifest.json"
    video_manifest.save(
        mpath,
        {"date_id": date_id, "output": f"lewis_clark_{date_id}_video.mp4"},
    )
    video_manifest.update_youtube(mpath, "vid_arch", "https://youtu.be/arch")
    data = srv.youtube_upload_status_for_ui(scan_archive=True)
    assert data["last_date_id"] == date_id
