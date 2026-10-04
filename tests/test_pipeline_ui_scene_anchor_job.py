"""Pipeline UI scene-anchor async job helpers."""

from __future__ import annotations

from unittest.mock import patch

from pipeline_ui.server import fal_scene_anchor_i2i_job_status, start_fal_scene_anchor_i2i_job


def test_start_job_returns_job_id_for_polling():
    with patch("pipeline_ui.server._run_scene_anchor_job"):
        out = start_fal_scene_anchor_i2i_job(
            {"journal_date": "1804-06-01", "segment_index": 3, "aspect_ratio": "9:16"}
        )
    assert out["ok"] is True
    assert out["async"] is True
    assert out["job_id"]
    assert out["status"] == "running"
    poll = fal_scene_anchor_i2i_job_status(out["job_id"])
    assert poll["ok"] is True
    assert poll["status"] == "running"
