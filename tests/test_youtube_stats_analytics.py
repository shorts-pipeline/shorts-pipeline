"""Tests for youtube_stats.py's --analytics (retention) support.

Impressions/CTR are intentionally not covered here: they are not available
through the interactive YouTube Analytics API this module calls (confirmed
against the live API - see fetch_analytics_for_videos()'s docstring).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import youtube_stats
from youtube_stats import VideoAnalytics, VideoStats, fetch_analytics_for_videos, merge_analytics


def _fake_analytics_service(rows):
    service = MagicMock()
    service.reports.return_value.query.return_value.execute.return_value = {"rows": rows}
    return service


def test_fetch_analytics_for_videos_parses_rows(monkeypatch):
    rows = [
        ["vid1", 22.3, 61.0],
        ["vid2", 15.0, 40.5],
    ]
    service = _fake_analytics_service(rows)
    monkeypatch.setattr(youtube_stats, "get_authenticated_analytics_service", lambda: service)

    result = fetch_analytics_for_videos(["vid1", "vid2"])

    assert result["vid1"] == VideoAnalytics(
        video_id="vid1", avg_view_duration_sec=22.3, avg_view_pct=61.0
    )
    assert result["vid2"].avg_view_pct == 40.5

    query_call = service.reports.return_value.query
    _, kwargs = query_call.call_args
    assert kwargs["ids"] == "channel==MINE"
    assert kwargs["dimensions"] == "video"
    assert kwargs["filters"] == "video==vid1,vid2"
    assert "averageViewDuration" in kwargs["metrics"]
    assert "averageViewPercentage" in kwargs["metrics"]
    assert "impressions" not in kwargs["metrics"]


def test_fetch_analytics_for_videos_chunks_large_batches(monkeypatch):
    video_ids = [f"vid{i}" for i in range(250)]
    service = _fake_analytics_service([])
    monkeypatch.setattr(youtube_stats, "get_authenticated_analytics_service", lambda: service)

    fetch_analytics_for_videos(video_ids)

    query_call = service.reports.return_value.query
    assert query_call.call_count == 2
    first_filters = query_call.call_args_list[0].kwargs["filters"]
    second_filters = query_call.call_args_list[1].kwargs["filters"]
    assert first_filters.count(",") == 199  # 200 ids per batch
    assert second_filters.count(",") == 49  # remaining 50 ids


def test_fetch_analytics_for_videos_skips_failed_batch(monkeypatch):
    from googleapiclient.errors import HttpError

    service = MagicMock()
    resp = MagicMock(status=403)
    service.reports.return_value.query.return_value.execute.side_effect = HttpError(
        resp, b"forbidden"
    )
    monkeypatch.setattr(youtube_stats, "get_authenticated_analytics_service", lambda: service)

    result = fetch_analytics_for_videos(["vid1"])

    assert result == {}


def test_merge_analytics_fills_in_stats_fields():
    stats_map = {
        "vid1": VideoStats(
            video_id="vid1",
            title="Episode 1",
            published_at="2026-01-01T00:00:00Z",
            view_count=100,
            like_count=10,
            comment_count=2,
        )
    }
    analytics_map = {
        "vid1": VideoAnalytics(video_id="vid1", avg_view_duration_sec=30.0, avg_view_pct=55.0),
        "vid_not_in_stats": VideoAnalytics(
            video_id="vid_not_in_stats", avg_view_duration_sec=1.0, avg_view_pct=1.0
        ),
    }

    merge_analytics(stats_map, analytics_map)

    stats = stats_map["vid1"]
    assert stats.avg_view_duration_sec == 30.0
    assert stats.avg_view_pct == 55.0


def test_merge_analytics_leaves_unmatched_stats_none():
    stats_map = {
        "vid1": VideoStats(
            video_id="vid1",
            title="Episode 1",
            published_at="2026-01-01T00:00:00Z",
            view_count=100,
            like_count=10,
            comment_count=2,
        )
    }
    merge_analytics(stats_map, {})
    assert stats_map["vid1"].avg_view_pct is None


def test_print_analytics_aggregate_reports_weighted_retention(capsys):
    entry1 = youtube_stats.VideoEntry(
        date_id="20260101", video_id="vid1", manifest_path=__file__, youtube_url=None
    )
    entry2 = youtube_stats.VideoEntry(
        date_id="20260102", video_id="vid2", manifest_path=__file__, youtube_url=None
    )
    stats1 = VideoStats(
        video_id="vid1",
        title="A",
        published_at="2026-01-01T00:00:00Z",
        view_count=100,
        like_count=1,
        comment_count=0,
        avg_view_duration_sec=20.0,
        avg_view_pct=50.0,
    )
    stats2 = VideoStats(
        video_id="vid2",
        title="B",
        published_at="2026-01-02T00:00:00Z",
        view_count=300,
        like_count=1,
        comment_count=0,
        avg_view_duration_sec=10.0,
        avg_view_pct=30.0,
    )
    with_stats = [(entry1, stats1), (entry2, stats2)]

    youtube_stats._print_analytics_aggregate(with_stats)
    out = capsys.readouterr().out

    # weighted avg % viewed = (100*50 + 300*30) / 400 = 35.0
    assert "Avg % viewed:      35.0%" in out
    # weighted avg duration = (100*20 + 300*10) / 400 = 12.5
    assert "Avg view duration: 12.5s" in out


def test_print_analytics_aggregate_no_data_prints_nothing(capsys):
    entry1 = youtube_stats.VideoEntry(
        date_id="20260101", video_id="vid1", manifest_path=__file__, youtube_url=None
    )
    stats1 = VideoStats(
        video_id="vid1",
        title="A",
        published_at="2026-01-01T00:00:00Z",
        view_count=100,
        like_count=1,
        comment_count=0,
    )
    youtube_stats._print_analytics_aggregate([(entry1, stats1)])
    assert capsys.readouterr().out == ""


def test_print_ctr_retention_trends_groups_by_month(capsys):
    entry1 = youtube_stats.VideoEntry(
        date_id="20260101", video_id="vid1", manifest_path=__file__, youtube_url=None
    )
    entry2 = youtube_stats.VideoEntry(
        date_id="20260201", video_id="vid2", manifest_path=__file__, youtube_url=None
    )
    stats1 = VideoStats(
        video_id="vid1",
        title="A",
        published_at="2026-01-01T00:00:00Z",
        view_count=100,
        like_count=0,
        comment_count=0,
        avg_view_duration_sec=20.0,
        avg_view_pct=60.0,
    )
    stats2 = VideoStats(
        video_id="vid2",
        title="B",
        published_at="2026-02-01T00:00:00Z",
        view_count=100,
        like_count=0,
        comment_count=0,
        avg_view_duration_sec=10.0,
        avg_view_pct=30.0,
    )
    youtube_stats._print_ctr_retention_trends([(entry1, stats1), (entry2, stats2)])
    out = capsys.readouterr().out

    assert "2026-01:  1 videos, avg viewed 60.0%, avg duration 20.0s" in out
    assert "2026-02:  1 videos, avg viewed 30.0%, avg duration 10.0s" in out
