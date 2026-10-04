"""video_manifest.write_pipeline_manifest — shared manifest write for run-daily + UI."""

from __future__ import annotations

import json
from pathlib import Path

import video_manifest

DATE_ID = "18040528"


def test_write_pipeline_manifest_fields(tmp_path: Path):
    (tmp_path / "narrations").mkdir()
    (tmp_path / "narrations" / f"narration{DATE_ID}.meta.json").write_text(
        json.dumps({"prompt_tokens": 1_000_000, "completion_tokens": 0}), encoding="utf-8"
    )
    (tmp_path / "audio" / DATE_ID).mkdir(parents=True)
    (tmp_path / "audio" / DATE_ID / "durations.json").write_text(
        json.dumps([{"duration": 10}]), encoding="utf-8"
    )
    video = tmp_path / "output" / f"lewis_clark_{DATE_ID}_video.mp4"
    video.parent.mkdir()
    video.write_bytes(b"not really a video")  # ffmpeg.probe fails -> length_seconds None

    manifest_path = video_manifest.write_pipeline_manifest(
        video,
        date_id=DATE_ID,
        aspect_ratio="9:16",
        narration_vendor="openai",
        tts_vendor="pyttsx3",
        video_vendor="fal",
        repo_root=tmp_path,
    )

    assert manifest_path == video.with_suffix(".manifest.json")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["date_id"] == DATE_ID
    assert data["output"] == video.name
    assert data["aspect_ratio"] == "9:16"
    assert data["length_seconds"] is None
    assert data["video_vendor"] == "fal"
    assert data["tts_vendor"] == "pyttsx3"
    # cost estimate wired through pipeline.cost_estimate
    assert data["cost_estimate"]["narration_usd"] == 0.15  # 1M input tokens @ 0.15/1M
    assert data["cost_estimate"]["video_usd"] == 0.5  # 10s * 0.05 (fal 480p)


def test_write_pipeline_manifest_tolerates_missing_cost_inputs(tmp_path: Path):
    video = tmp_path / "output" / f"lewis_clark_{DATE_ID}_video.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"x")
    manifest_path = video_manifest.write_pipeline_manifest(
        video,
        date_id=DATE_ID,
        aspect_ratio="16:9",
        narration_vendor="openai",
        tts_vendor="openai",
        video_vendor="sora",
        repo_root=tmp_path,
    )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["cost_estimate"]["total_usd"] is None
    assert data["aspect_ratio"] == "16:9"


def test_update_instagram_creates_manifest_when_missing(tmp_path: Path):
    video = tmp_path / "output" / f"lewis_clark_{DATE_ID}_video.mp4"
    video.parent.mkdir(parents=True)
    manifest_path = video.with_suffix(".manifest.json")
    assert video_manifest.update_instagram(
        manifest_path, "media1", "https://instagram.com/reel/1", video_path=video
    )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["date_id"] == DATE_ID
    assert data["instagram_media_id"] == "media1"
    assert data["instagram_url"] == "https://instagram.com/reel/1"


def test_update_instagram_missing_manifest_no_video_path_returns_false(tmp_path: Path):
    assert video_manifest.update_instagram(tmp_path / "missing.manifest.json", "media1") is False
