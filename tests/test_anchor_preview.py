"""Tests for pipeline.anchor_preview (no FAL / ffmpeg)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.anchor_preview import (
    AnchorPreviewReport,
    assemble_anchor_preview_with_missing_anchors,
    plan_preview_clip,
    scene_anchor_eligible_segments,
    shorten_render_error_message,
    summarize_anchor_preview_issues,
)
from video_vendors.fal import FalVendor


@pytest.fixture
def repo_with_narration(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path
    did = "18030101"
    narr_dir = repo / "narrations"
    narr_dir.mkdir(parents=True)
    script = [
        {
            "segment_index": 1,
            "narration": "First line.",
            "video_prompt": "River bank at dawn.",
            "reference_character_id": "lewis",
            "visual_mode": "b_roll",
        },
        {
            "segment_index": 2,
            "narration": "Second.",
            "video_prompt": "Camp fire.",
            "visual_mode": "talking_head",
            "talking_head_subject": "lewis",
        },
        {
            "segment_index": 3,
            "narration": "Wide shot.",
            "video_prompt": "Mountains.",
            "visual_mode": "b_roll",
        },
    ]
    (narr_dir / f"narration{did}.json").write_text(
        json.dumps(
            {
                "narration_version": "2.0",
                "narration_script": script,
                "scene_spine": {"core_location": "Missouri River"},
            }
        ),
        encoding="utf-8",
    )
    portraits = repo / "character-portraits"
    portraits.mkdir()
    (portraits / "lewis.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return repo, did


def test_scene_anchor_eligible_all_narration_segments(
    repo_with_narration: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, did = repo_with_narration
    monkeypatch.setattr(
        "pipeline.broll_scene_anchor.load_narration_config",
        lambda *a, **k: {"fal_broll_scene_anchor": {"enabled": True}},
    )
    eligible = scene_anchor_eligible_segments(did, repo_root=repo)
    assert len(eligible) == 3
    by_idx = {e.segment_index: e for e in eligible}
    assert by_idx[1].character_id == "lewis"
    assert by_idx[1].anchor_method == "i2i"
    assert by_idx[1].anchor_exists is False
    assert by_idx[3].anchor_method == "t2i"
    assert by_idx[3].character_id == ""


def test_scene_anchor_eligible_portrait_b_roll_without_global_flag(
    repo_with_narration: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, did = repo_with_narration
    monkeypatch.setattr(
        "pipeline.broll_scene_anchor.load_narration_config",
        lambda *a, **k: {"fal_broll_scene_anchor": {"enabled": False}},
    )
    eligible = scene_anchor_eligible_segments(did, repo_root=repo)
    assert [e.segment_index for e in eligible] == [1, 2]


def test_plan_preview_clip_slate_without_anchor(
    repo_with_narration: tuple[Path, str],
) -> None:
    repo, did = repo_with_narration
    narr = json.loads((repo / "narrations" / f"narration{did}.json").read_text())
    plan = plan_preview_clip(
        repo_root=repo,
        date_id=did,
        segment_index=3,
        duration_sec=5.0,
        narr=narr,
    )
    assert plan.source == "slate"
    assert plan.reason == "not_eligible"


def test_plan_preview_clip_missing_talking_head(
    repo_with_narration: tuple[Path, str],
) -> None:
    repo, did = repo_with_narration
    narr = json.loads((repo / "narrations" / f"narration{did}.json").read_text())
    plan = plan_preview_clip(
        repo_root=repo,
        date_id=did,
        segment_index=2,
        duration_sec=8.0,
        narr=narr,
    )
    assert plan.source == "missing_th"


def test_plan_preview_clip_uses_existing_segment_mp4(
    repo_with_narration: tuple[Path, str],
) -> None:
    repo, did = repo_with_narration
    mi = repo / "movie-images" / did
    mi.mkdir(parents=True)
    (mi / "03.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    narr = json.loads((repo / "narrations" / f"narration{did}.json").read_text())
    plan = plan_preview_clip(
        repo_root=repo,
        date_id=did,
        segment_index=3,
        duration_sec=4.0,
        narr=narr,
    )
    assert plan.source == "clip"


def test_shorten_render_error_windows_exit_code() -> None:
    raw = "render_failed: Command '['ffmpeg', ...]' returned non-zero exit status 3221225477."
    msg = shorten_render_error_message(raw)
    assert "drawtext" in msg.lower() or "Windows" in msg


def test_summarize_anchor_preview_issues_from_report() -> None:
    report = {
        "clip_plans": [
            {
                "segment_index": 1,
                "visual_mode": "b_roll",
                "source": "slate",
                "reason": "render_failed: exit 3221225477",
            },
            {
                "segment_index": 2,
                "visual_mode": "b_roll",
                "source": "anchor",
                "reason": "scene_anchor_still",
            },
        ],
        "preview_video_rel": "",
    }
    issues = summarize_anchor_preview_issues(report)
    assert len(issues) >= 2
    kinds = {i["kind"] for i in issues}
    assert "clip_render" in kinds
    assert "assembly" in kinds


def test_assemble_preview_builds_missing_anchors_first(
    repo_with_narration: tuple[Path, str],
) -> None:
    repo, did = repo_with_narration
    (repo / "audio" / did).mkdir(parents=True)
    (repo / "audio" / did / "final.mp3").write_bytes(b"ID3")
    (repo / "audio" / did / "durations.json").write_text(
        json.dumps([{"file": "01.mp3", "duration": 1.0}]),
        encoding="utf-8",
    )
    out_mp4 = repo / "output" / "preview.mp4"
    out_mp4.parent.mkdir(parents=True)
    out_mp4.write_bytes(b"\x00")

    anchor_report = AnchorPreviewReport(date_id=did, eligible_count=1)
    clip_report = AnchorPreviewReport(date_id=did)
    clip_report.anchor_build = anchor_report.anchor_build

    with (
        patch(
            "pipeline.anchor_preview.check_tts_prerequisites",
            return_value=(True, ""),
        ),
        patch(
            "pipeline.anchor_preview.build_scene_anchors_batch",
            return_value=anchor_report,
        ) as batch,
        patch(
            "pipeline.anchor_preview.render_anchor_preview_clips",
            return_value=clip_report,
        ) as render,
        patch(
            "pipeline.anchor_preview.assemble_anchor_preview_video",
            return_value=out_mp4,
        ),
        patch(
            "pipeline.anchor_preview.load_report",
            return_value=clip_report,
        ),
    ):
        report, out = assemble_anchor_preview_with_missing_anchors(
            did, repo_root=repo, skip_existing=True, force=False
        )

    batch.assert_called_once()
    assert batch.call_args.kwargs["skip_existing"] is True
    assert batch.call_args.kwargs["force"] is False
    render.assert_called_once()
    assert out == out_mp4
    assert report.date_id == did


def test_fal_markers_for_eligible_segment(
    repo_with_narration: tuple[Path, str],
) -> None:
    from video_vendors import build_prompts

    repo, did = repo_with_narration
    prompts = build_prompts(did, narrations_dir=repo / "narrations", vendor="fal")
    rest, cid, scene_anchor = FalVendor._parse_markers(prompts[0])
    assert scene_anchor is True
    assert cid == "lewis"
    assert "River" in rest or "bank" in rest
