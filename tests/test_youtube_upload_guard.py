"""Tests for pipeline.youtube_upload_guard (duplicate + out-of-order upload guards)."""

from __future__ import annotations

import pytest

import video_manifest
from pipeline.youtube_upload_guard import (
    DuplicateUploadError,
    UploadSequenceError,
    already_uploaded_video_id,
    check_upload_allowed,
    earlier_unuploaded_episodes,
)


def _episode(output_dir, date_id: str, *, uploaded: bool):
    vpath = output_dir / f"lewis_clark_{date_id}_video.mp4"
    vpath.write_bytes(b"x")
    mpath = video_manifest.manifest_path_for_video(vpath)
    video_manifest.save(mpath, {"date_id": date_id, "output": vpath.name})
    if uploaded:
        video_manifest.update_youtube(mpath, f"vid_{date_id}", f"https://youtu.be/{date_id}")
    return vpath, mpath


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "output").mkdir()
    return tmp_path


def test_already_uploaded_video_id_reads_manifest(repo):
    _v, m = _episode(repo / "output", "18040703", uploaded=True)
    assert already_uploaded_video_id(m) == "vid_18040703"


def test_already_uploaded_video_id_none_when_absent_or_missing(repo):
    _v, m = _episode(repo / "output", "18040704", uploaded=False)
    assert already_uploaded_video_id(m) is None
    assert already_uploaded_video_id(None) is None
    assert already_uploaded_video_id(repo / "output" / "nope.manifest.json") is None


def test_duplicate_upload_raises_without_force(repo):
    vpath, mpath = _episode(repo / "output", "18040703", uploaded=True)
    with pytest.raises(DuplicateUploadError) as ei:
        check_upload_allowed(vpath, mpath, repo_root=repo)
    assert ei.value.video_id == "vid_18040703"
    # force overrides
    check_upload_allowed(vpath, mpath, repo_root=repo, force=True)


def test_sequence_gap_between_high_water_mark_and_target(repo):
    out = repo / "output"
    _episode(out, "18040703", uploaded=True)
    _episode(out, "18040704", uploaded=False)
    _episode(out, "18040705", uploaded=False)
    vpath, mpath = _episode(out, "18040707", uploaded=False)

    assert earlier_unuploaded_episodes("18040707", repo_root=repo) == ["18040704", "18040705"]

    with pytest.raises(UploadSequenceError) as ei:
        check_upload_allowed(vpath, mpath, repo_root=repo)
    assert ei.value.missing == ["18040704", "18040705"]
    # allow_gap overrides
    check_upload_allowed(vpath, mpath, repo_root=repo, allow_gap=True)


def test_old_back_catalogue_gap_below_mark_is_ignored(repo):
    out = repo / "output"
    _episode(out, "18040106", uploaded=False)  # ancient un-uploaded, below the mark
    _episode(out, "18040703", uploaded=True)
    vpath, mpath = _episode(out, "18040704", uploaded=False)

    assert earlier_unuploaded_episodes("18040704", repo_root=repo) == []
    check_upload_allowed(vpath, mpath, repo_root=repo)  # no raise


def test_no_baseline_means_no_sequence_check(repo):
    out = repo / "output"
    _episode(out, "18040704", uploaded=False)
    vpath, mpath = _episode(out, "18040707", uploaded=False)
    assert earlier_unuploaded_episodes("18040707", repo_root=repo) == []
    # still not "uploaded", so no DuplicateUploadError either
    check_upload_allowed(vpath, mpath, repo_root=repo)


def test_anchor_preview_outputs_are_not_treated_as_episodes(repo):
    out = repo / "output"
    _episode(out, "18040703", uploaded=True)
    # an anchor-preview MP4 with a later-looking date id must not count as a gap
    prev = out / "lewis_clark_anchor_preview_18040705_video.mp4"
    prev.write_bytes(b"x")
    video_manifest.save(
        video_manifest.manifest_path_for_video(prev),
        {"date_id": "18040705", "output": prev.name},
    )
    assert earlier_unuploaded_episodes("18040707", repo_root=repo) == []


def test_target_below_mark_does_not_flag_later_gaps(repo):
    out = repo / "output"
    _episode(out, "18040703", uploaded=True)
    _episode(out, "18040705", uploaded=False)
    _episode(out, "18040707", uploaded=True)
    # re-uploading 704 (below the 707 mark): window is (703, 704) -> empty
    vpath, mpath = _episode(out, "18040704", uploaded=False)
    assert earlier_unuploaded_episodes("18040704", repo_root=repo) == []
