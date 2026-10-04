"""Tests for instagram_upload.py's pure logic and mocked-network/subprocess helpers."""

from __future__ import annotations

import json
import time
import unittest.mock as mock

import pytest
import requests

import instagram_upload as ig


def test_extract_code_from_redirect_full_url():
    url = "https://localhost/instagram/callback?code=abc123#_"
    assert ig.extract_code_from_redirect(url) == "abc123"


def test_extract_code_from_redirect_bare_code():
    assert ig.extract_code_from_redirect("  abc123  ") == "abc123"


def test_extract_code_from_redirect_missing_code_raises():
    with pytest.raises(ValueError):
        ig.extract_code_from_redirect("https://localhost/instagram/callback?state=xyz")


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    path = tmp_path / "instagram_token.json"
    monkeypatch.setattr(ig, "TOKEN_FILE", path)
    return path


def test_get_access_token_returns_cached_when_fresh(token_file):
    token_file.write_text(
        json.dumps({"access_token": "cached", "expires_in": 5184000, "saved_at": time.time()}),
        encoding="utf-8",
    )
    assert ig.get_access_token() == "cached"


def test_get_access_token_missing_raises(token_file):
    with pytest.raises(RuntimeError, match="--login-only"):
        ig.get_access_token()


@mock.patch("instagram_upload.refresh_long_lived_token")
def test_get_access_token_refreshes_past_halfway(m_refresh, token_file):
    expires_in = 5184000  # 60 days
    token_file.write_text(
        json.dumps(
            {
                "access_token": "stale",
                "expires_in": expires_in,
                "saved_at": time.time() - (expires_in * 0.75),
                "user_id": "u1",
            }
        ),
        encoding="utf-8",
    )
    m_refresh.return_value = {"access_token": "fresh", "expires_in": expires_in}
    assert ig.get_access_token() == "fresh"
    assert json.loads(token_file.read_text())["user_id"] == "u1"


@mock.patch("instagram_upload.refresh_long_lived_token")
def test_get_access_token_keeps_existing_on_refresh_failure(m_refresh, token_file):
    expires_in = 5184000
    token_file.write_text(
        json.dumps(
            {
                "access_token": "stale",
                "expires_in": expires_in,
                "saved_at": time.time() - (expires_in * 0.75),
            }
        ),
        encoding="utf-8",
    )
    m_refresh.side_effect = requests.RequestException("network down")
    assert ig.get_access_token() == "stale"


@mock.patch("instagram_upload.subprocess.run")
@mock.patch("instagram_upload.shutil.which", return_value="/usr/bin/aws")
def test_stage_public_url_uploads_and_presigns(m_which, m_run, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    m_run.side_effect = [
        mock.Mock(),  # aws s3 cp
        mock.Mock(stdout="https://presigned.example/x\n"),  # aws s3 presign
    ]
    key, url = ig.stage_public_url(video)
    assert key == "social_staging/v.mp4"
    assert url == "https://presigned.example/x"
    assert m_run.call_count == 2
    assert m_run.call_args_list[0].args[0][:3] == ["aws", "s3", "cp"]
    assert m_run.call_args_list[1].args[0][:3] == ["aws", "s3", "presign"]


@mock.patch("instagram_upload.shutil.which", return_value=None)
def test_stage_public_url_raises_without_aws_cli(m_which, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="aws CLI not found"):
        ig.stage_public_url(video)


@mock.patch("instagram_upload.requests.post")
def test_create_media_container(m_post, monkeypatch):
    monkeypatch.setenv("IG_USER_ID", "u1")
    m_post.return_value = mock.Mock(
        json=lambda: {"id": "container1"}, raise_for_status=lambda: None
    )
    container_id = ig.create_media_container("token", video_url="https://x", caption="hi")
    assert container_id == "container1"


@mock.patch("instagram_upload.time.sleep", autospec=True)
@mock.patch("instagram_upload.requests.get")
def test_poll_container_status_finished(m_get, _sleep):
    m_get.return_value = mock.Mock(
        json=lambda: {"status_code": "FINISHED"}, raise_for_status=lambda: None
    )
    assert ig.poll_container_status("token", "c1") == "FINISHED"


@mock.patch("instagram_upload.time.sleep", autospec=True)
@mock.patch("instagram_upload.requests.get")
def test_poll_container_status_error_raises(m_get, _sleep):
    m_get.return_value = mock.Mock(
        json=lambda: {"status_code": "ERROR"}, raise_for_status=lambda: None
    )
    with pytest.raises(RuntimeError, match="processing failed"):
        ig.poll_container_status("token", "c1")


@mock.patch("instagram_upload.requests.post")
def test_publish_container(m_post, monkeypatch):
    monkeypatch.setenv("IG_USER_ID", "u1")
    m_post.return_value = mock.Mock(json=lambda: {"id": "media1"}, raise_for_status=lambda: None)
    assert ig.publish_container("token", "c1") == "media1"


def test_upload_video_refuses_duplicate_without_force(tmp_path):
    manifest = tmp_path / "m.manifest.json"
    manifest.write_text(json.dumps({"date_id": "18030830", "instagram_media_id": "existing"}))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="already has instagram_media_id"):
        ig.upload_video(video, manifest_path=manifest)


def test_upload_video_unstages_even_on_container_failure(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(ig, "get_access_token", lambda: "token")
    monkeypatch.setattr(ig, "stage_public_url", lambda path: ("social_staging/v.mp4", "https://x"))
    unstaged = []
    monkeypatch.setattr(ig, "unstage", lambda key: unstaged.append(key))

    def boom(*a, **k):
        raise RuntimeError("container creation failed")

    monkeypatch.setattr(ig, "create_media_container", boom)

    with pytest.raises(RuntimeError, match="container creation failed"):
        ig.upload_video(video, caption="hi")

    assert unstaged == ["social_staging/v.mp4"]
