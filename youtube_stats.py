#!/usr/bin/env python3
"""
Fetch and summarize YouTube viewership stats for Lewis & Clark videos.

Reads output/*.manifest.json for manifests that have youtube_video_id, then
calls the YouTube Data API v3 to retrieve statistics for each video.
With --scan-archive, also reads archive/*/output/*.manifest.json (output/ wins
on duplicate date_id).

Usage (from project root, with venv activated and OAuth token already set up):

  python youtube_stats.py
  python youtube_stats.py --scan-archive
  python youtube_stats.py --scan-archive --brief
  python youtube_stats.py --csv stats.csv --scan-archive

Prints per-video stats plus aggregate trends (by publish month/week, video vendor,
talking-head vs narrator-only from narrations/narration{DATE}.json). Use --brief
for a weekly snapshot without the full per-video table.

With --analytics, also pulls average view duration and average percent viewed
(retention) from the YouTube Analytics API, and prints retention trends by
publish month. NOTE: impressions/click-through-rate are NOT included - YouTube
only exposes those through the separate, bulk YouTube Reporting API (scheduled
report jobs + downloadable CSVs), not the interactive Analytics API used here.
See fetch_analytics_for_videos()'s docstring and
ai-plans/narration-engagement-followups-2026-09.md item 1.

Requires:
  - requirements-youtube.txt installed
  - youtube_upload.py --login-only run at least once (to create token.json)
  - For --analytics: token.json must include the yt-analytics.readonly scope.
    If it predates that scope, re-run `youtube_upload.py --login-only` once.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import video_manifest
from pipeline.narration_visual_mode import (
    VISUAL_MODE_TALKING_HEAD,
    visual_modes_for_narration_script,
)
from youtube_upload import get_authenticated_analytics_service, get_authenticated_service

_PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = _PROJECT_ROOT / "output"
ARCHIVE_DIR = _PROJECT_ROOT / "archive"
NARRATIONS_DIR = _PROJECT_ROOT / "narrations"


@dataclass
class VideoEntry:
    date_id: str
    video_id: str
    manifest_path: Path
    youtube_url: str | None
    aspect_ratio: str | None = None


@dataclass
class VideoStats:
    video_id: str
    title: str
    published_at: str
    view_count: int
    like_count: int
    comment_count: int
    # Populated only when --analytics is used (YouTube Analytics API).
    avg_view_duration_sec: float | None = None
    avg_view_pct: float | None = None  # percent of video watched on average


@dataclass
class VideoAnalytics:
    video_id: str
    avg_view_duration_sec: float
    avg_view_pct: float


def _manifest_paths(*, scan_archive: bool) -> list[Path]:
    paths: list[Path] = []
    if OUTPUT_DIR.is_dir():
        paths.extend(sorted(OUTPUT_DIR.glob("*.manifest.json")))
    if scan_archive and ARCHIVE_DIR.is_dir():
        paths.extend(sorted(ARCHIVE_DIR.glob("*/output/*.manifest.json")))
    return paths


def _iter_uploaded_entries(*, scan_archive: bool = False) -> list[VideoEntry]:
    """Collect uploaded videos (with youtube_video_id); output/ manifests win on duplicate date_id."""
    seen: set[str] = set()
    entries: list[VideoEntry] = []
    for path in _manifest_paths(scan_archive=scan_archive):
        data = video_manifest.load(path)
        if not data:
            continue
        date_id = data.get("date_id")
        video_id = data.get("youtube_video_id")
        youtube_url = data.get("youtube_url")
        if not (date_id and video_id):
            continue
        date_id = str(date_id)
        if date_id in seen:
            continue
        seen.add(date_id)
        ar = data.get("aspect_ratio")
        entries.append(
            VideoEntry(
                date_id=date_id,
                video_id=str(video_id),
                manifest_path=path,
                youtube_url=youtube_url if isinstance(youtube_url, str) else None,
                aspect_ratio=str(ar).strip() if ar else None,
            )
        )

    entries.sort(key=lambda e: e.date_id)
    return entries


def _chunked(iterable: Iterable[str], size: int) -> Iterable[list[str]]:
    """Yield lists of at most `size` items from iterable."""
    batch: list[str] = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def fetch_stats_for_videos(video_ids: list[str]) -> dict[str, VideoStats]:
    """
    Fetch statistics for the given YouTube video IDs.

    Returns a mapping video_id -> VideoStats.
    """
    youtube = get_authenticated_service()
    result: dict[str, VideoStats] = {}

    # YouTube API allows up to 50 IDs per videos().list call
    for batch in _chunked(video_ids, 50):
        response = (
            youtube.videos()
            .list(
                part="snippet,statistics",
                id=",".join(batch),
            )
            .execute()
        )
        for item in response.get("items", []):
            vid = item["id"]
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            title = snippet.get("title", "")
            published_at = snippet.get("publishedAt", "")
            view_count = int(stats.get("viewCount", 0))
            like_count = int(stats.get("likeCount", 0))
            comment_count = int(stats.get("commentCount", 0))

            result[vid] = VideoStats(
                video_id=vid,
                title=title,
                published_at=published_at,
                view_count=view_count,
                like_count=like_count,
                comment_count=comment_count,
            )

    return result


_ANALYTICS_METRICS = "averageViewDuration,averageViewPercentage"


def fetch_analytics_for_videos(
    video_ids: list[str], *, start_date: str = "2020-01-01"
) -> dict[str, VideoAnalytics]:
    """
    Fetch retention metrics (avg view duration/percent) from the interactive
    YouTube Analytics API (`youtubeAnalytics.reports().query`).

    NOTE: impressions / click-through-rate are NOT available through this API.
    YouTube only exposes those (as `videoThumbnailImpressions` /
    `videoThumbnailImpressionsClickRate`) through the separate, bulk *YouTube
    Reporting API* (scheduled report jobs + downloadable CSVs, e.g. the
    `channel_reach_basic_a1` report type) - a materially bigger integration
    (async job creation, polling, ~2-day data lag). Not built yet; see
    ai-plans/narration-engagement-followups-2026-09.md item 1.

    Requires the yt-analytics.readonly OAuth scope (re-run
    `python youtube_upload.py --login-only` once if token.json predates it).
    Videos with too little traffic for YouTube to report a metric are simply
    absent from the result rather than erroring.
    """
    from googleapiclient.errors import HttpError

    youtube_analytics = get_authenticated_analytics_service()
    end_date = datetime.now(UTC).strftime("%Y-%m-%d")
    result: dict[str, VideoAnalytics] = {}

    for batch in _chunked(video_ids, 200):
        try:
            response = (
                youtube_analytics.reports()
                .query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=end_date,
                    metrics=_ANALYTICS_METRICS,
                    dimensions="video",
                    filters="video==" + ",".join(batch),
                    maxResults=len(batch),
                )
                .execute()
            )
        except HttpError as e:
            print(
                f"[WARN] YouTube Analytics query failed for a batch of {len(batch)} video(s): {e}"
            )
            continue

        for row in response.get("rows", []):
            vid, avg_duration, avg_pct = row
            result[str(vid)] = VideoAnalytics(
                video_id=str(vid),
                avg_view_duration_sec=float(avg_duration),
                avg_view_pct=float(avg_pct),
            )

    return result


def merge_analytics(
    stats_map: dict[str, VideoStats], analytics_map: dict[str, VideoAnalytics]
) -> None:
    """Fill in the analytics fields of VideoStats in stats_map, in place."""
    for video_id, analytics in analytics_map.items():
        stats = stats_map.get(video_id)
        if stats is None:
            continue
        stats.avg_view_duration_sec = analytics.avg_view_duration_sec
        stats.avg_view_pct = analytics.avg_view_pct


def _avg_views(bucket: list[tuple[VideoEntry, VideoStats]]) -> float:
    if not bucket:
        return 0.0
    return sum(s.view_count for _, s in bucket) / len(bucket)


def _engagement_pct(likes: int, views: int) -> float:
    return (likes / views * 100) if views else 0.0


def _print_growth_trends(
    with_stats: list[tuple[VideoEntry, VideoStats]], *, recent_weeks: int = 8
) -> None:
    by_month: dict[str, dict[str, int]] = defaultdict(lambda: {"views": 0, "likes": 0, "count": 0})
    by_week: dict[str, dict[str, int]] = defaultdict(lambda: {"views": 0, "likes": 0, "count": 0})
    for _, stats in with_stats:
        dt = datetime.fromisoformat(stats.published_at.replace("Z", "+00:00"))
        for bucket, key in ((by_month, dt.strftime("%Y-%m")), (by_week, dt.strftime("%Y-W%W"))):
            bucket[key]["views"] += stats.view_count
            bucket[key]["likes"] += stats.like_count
            bucket[key]["count"] += 1

    print("\nViews by publish month:")
    for month in sorted(by_month):
        d = by_month[month]
        avg = d["views"] / d["count"] if d["count"] else 0.0
        eng = _engagement_pct(d["likes"], d["views"])
        print(
            f"  {month}: {d['count']:>2} videos, {d['views']:>6,} views, "
            f"avg {avg:,.0f}/video, likes/views {eng:.2f}%"
        )

    recent = sorted(by_week)[-recent_weeks:]
    if recent:
        print("\nRecent publish weeks (avg views per video published that week):")
        for week in recent:
            d = by_week[week]
            avg = d["views"] / d["count"] if d["count"] else 0.0
            print(f"  {week}: {d['count']} videos, avg {avg:,.0f} views")


def _print_analytics_aggregate(with_stats: list[tuple[VideoEntry, VideoStats]]) -> None:
    have = [(e, s) for e, s in with_stats if s.avg_view_pct is not None]
    if not have:
        return

    total_views = sum(s.view_count for _, s in have)
    weighted_view_pct = sum((s.avg_view_pct or 0.0) * s.view_count for _, s in have)
    weighted_duration = sum((s.avg_view_duration_sec or 0.0) * s.view_count for _, s in have)

    avg_view_pct = weighted_view_pct / total_views if total_views else 0.0
    avg_duration = weighted_duration / total_views if total_views else 0.0

    print(f"\nAnalytics (retention, {len(have)} video(s) with data):")
    print(f"  Avg % viewed:      {avg_view_pct:.1f}%")
    print(f"  Avg view duration: {avg_duration:.1f}s")


def _print_ctr_retention_trends(
    with_stats: list[tuple[VideoEntry, VideoStats]], *, recent_months: int = 8
) -> None:
    """
    Retention (avg % viewed / avg view duration) by publish month, to see whether
    retention is trending down alongside a views decline (pacing/narration problem)
    or holding steady (points at a CTR/impressions problem instead - not yet
    available; see fetch_analytics_for_videos docstring).
    """
    have = [(e, s) for e, s in with_stats if s.avg_view_pct is not None]
    if not have:
        return

    by_month: dict[str, dict[str, float]] = defaultdict(
        lambda: {"views": 0.0, "view_pct_weighted": 0.0, "duration_weighted": 0.0, "count": 0}
    )
    for _, stats in have:
        dt = datetime.fromisoformat(stats.published_at.replace("Z", "+00:00"))
        d = by_month[dt.strftime("%Y-%m")]
        d["views"] += stats.view_count
        d["view_pct_weighted"] += (stats.avg_view_pct or 0.0) * stats.view_count
        d["duration_weighted"] += (stats.avg_view_duration_sec or 0.0) * stats.view_count
        d["count"] += 1

    print("\nRetention by publish month:")
    for month in sorted(by_month)[-recent_months:]:
        d = by_month[month]
        avg_view_pct = d["view_pct_weighted"] / d["views"] if d["views"] else 0.0
        avg_duration = d["duration_weighted"] / d["views"] if d["views"] else 0.0
        print(
            f"  {month}: {int(d['count']):>2} videos, avg viewed {avg_view_pct:.1f}%, "
            f"avg duration {avg_duration:.1f}s"
        )


def _print_manifest_breakdowns(
    with_stats: list[tuple[VideoEntry, VideoStats]],
) -> None:
    by_vendor: dict[str, dict[str, int]] = defaultdict(lambda: {"views": 0, "likes": 0, "count": 0})
    for entry, stats in with_stats:
        data = video_manifest.load(entry.manifest_path) or {}
        vendor = str(data.get("video_vendor") or "unknown")
        by_vendor[vendor]["views"] += stats.view_count
        by_vendor[vendor]["likes"] += stats.like_count
        by_vendor[vendor]["count"] += 1

    if len(by_vendor) > 1 or next(iter(by_vendor)) != "unknown":
        print("\nBy video vendor (manifest):")
        for vendor, d in sorted(by_vendor.items(), key=lambda x: -x[1]["views"]):
            avg = d["views"] / d["count"] if d["count"] else 0.0
            eng = _engagement_pct(d["likes"], d["views"])
            print(f"  {vendor}: n={d['count']:>3}  avg views {avg:,.0f}  likes/views {eng:.2f}%")


def _load_narration(date_id: str) -> dict | None:
    path = NARRATIONS_DIR / f"narration{date_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _talking_head_bucket(th_count: int, total_segments: int) -> str:
    if th_count == 0:
        return "no_talking_head"
    ratio = th_count / (total_segments or 1)
    if ratio >= 0.5:
        return "mostly_talking_head"
    return "some_talking_head"


def _print_narration_breakdown(
    with_stats: list[tuple[VideoEntry, VideoStats]],
    *,
    top_n: int = 8,
    bottom_n: int = 0,
) -> None:
    by_th: dict[str, dict[str, int]] = defaultdict(lambda: {"views": 0, "likes": 0, "count": 0})
    by_mode: dict[str, dict[str, int]] = defaultdict(lambda: {"views": 0, "likes": 0, "count": 0})
    top_rows: list[tuple[int, str, int, int, str]] = []
    matched = 0

    for entry, stats in with_stats:
        data = _load_narration(entry.date_id)
        if not data:
            continue
        matched += 1
        modes = visual_modes_for_narration_script(data)
        th_count = sum(1 for m in modes if m == VISUAL_MODE_TALKING_HEAD)
        total_segments = len(modes)

        th_key = _talking_head_bucket(th_count, total_segments)
        by_th[th_key]["views"] += stats.view_count
        by_th[th_key]["likes"] += stats.like_count
        by_th[th_key]["count"] += 1

        mode_key = (
            "dialogue"
            if data.get("dialogue_mode") or data.get("long_conversation_mode")
            else "narrator_only"
        )
        by_mode[mode_key]["views"] += stats.view_count
        by_mode[mode_key]["likes"] += stats.like_count
        by_mode[mode_key]["count"] += 1

        top_rows.append((stats.view_count, entry.date_id, th_count, total_segments, stats.title))

    if matched == 0:
        print(
            "\nNarration breakdown: no narrations/narration{DATE}.json files matched uploaded episodes."
        )
        top = sorted(with_stats, key=lambda x: x[1].view_count, reverse=True)[:top_n]
        if top:
            print(f"\nTop {len(top)} episodes by views:")
            for entry, stats in top:
                eng = _engagement_pct(stats.like_count, stats.view_count)
                print(
                    f"  {entry.date_id}  {stats.view_count:>8,} views  "
                    f"likes/views {eng:4.2f}%  {stats.title[:60]}"
                )
        if bottom_n:
            bottom = sorted(with_stats, key=lambda x: x[1].view_count)[:bottom_n]
            print(f"\nBottom {len(bottom)} episodes by views:")
            for entry, stats in bottom:
                eng = _engagement_pct(stats.like_count, stats.view_count)
                print(
                    f"  {entry.date_id}  {stats.view_count:>8,} views  "
                    f"likes/views {eng:4.2f}%  {stats.title[:60]}"
                )
        return

    print(f"\nTalking-head mix ({matched} episodes with narration JSON):")
    labels = {
        "no_talking_head": "No talking head",
        "some_talking_head": "Some talking head (1-49% of segments)",
        "mostly_talking_head": "Mostly talking head (>=50% of segments)",
    }
    for key in ("no_talking_head", "some_talking_head", "mostly_talking_head"):
        d = by_th.get(key)
        if not d or not d["count"]:
            continue
        avg = d["views"] / d["count"]
        eng = _engagement_pct(d["likes"], d["views"])
        print(f"  {labels[key]}: n={d['count']:>3}  avg views {avg:,.0f}  likes/views {eng:.2f}%")

    print("\nDialogue vs narrator-only episodes:")
    mode_labels = {"dialogue": "Dialogue / long-conversation", "narrator_only": "Narrator-only"}
    for key in ("dialogue", "narrator_only"):
        d = by_mode.get(key)
        if not d or not d["count"]:
            continue
        avg = d["views"] / d["count"]
        eng = _engagement_pct(d["likes"], d["views"])
        print(
            f"  {mode_labels[key]}: n={d['count']:>3}  avg views {avg:,.0f}  likes/views {eng:.2f}%"
        )

    print(
        f"\nTop {min(top_n, len(top_rows))} episodes by views (talking_head segments / total segments):"
    )
    for views, date_id, th_count, total_segments, title in sorted(top_rows, reverse=True)[:top_n]:
        print(
            f"  {date_id}  {views:>8,} views  {th_count}/{total_segments} talking_head  {title[:55]}"
        )

    if bottom_n:
        th_by_date = {date_id: (th, tot) for _, date_id, th, tot, _ in top_rows}
        bottom = sorted(with_stats, key=lambda x: x[1].view_count)[:bottom_n]
        print(f"\nBottom {len(bottom)} episodes by views:")
        for entry, stats in bottom:
            th_info = th_by_date.get(entry.date_id)
            th_suffix = f"  {th_info[0]}/{th_info[1]} talking_head" if th_info else ""
            print(f"  {entry.date_id}  {stats.view_count:>8,} views{th_suffix}  {stats.title[:55]}")


def _print_aggregate(
    entries: list[VideoEntry],
    with_stats: list[tuple[VideoEntry, VideoStats]],
) -> None:
    total_views = sum(s.view_count for _, s in with_stats)
    total_likes = sum(s.like_count for _, s in with_stats)
    total_comments = sum(s.comment_count for _, s in with_stats)
    print("\nAggregate:")
    print(f"  Total views:    {total_views:,}")
    print(f"  Total likes:    {total_likes:,}")
    print(f"  Total comments: {total_comments:,}")
    if entries:
        print(f"  Avg views/video: {total_views / len(entries):.1f}")
        print(f"  Likes/views:     {_engagement_pct(total_likes, total_views):.2f}%")


def print_brief_summary(entries: list[VideoEntry], stats_map: dict[str, VideoStats]) -> None:
    """Print aggregate trends and top/bottom episodes without the per-video table."""
    if not entries:
        print("No uploaded videos found in output/*.manifest.json (need youtube_video_id).")
        return

    with_stats = [(e, stats_map[e.video_id]) for e in entries if e.video_id in stats_map]
    print(f"Found {len(entries)} uploaded video(s) with youtube_video_id (brief summary).\n")
    _print_aggregate(entries, with_stats)

    if len(with_stats) >= 5:
        _print_growth_trends(with_stats, recent_weeks=4)
        _print_narration_breakdown(with_stats, top_n=5, bottom_n=5)

    _print_analytics_aggregate(with_stats)
    if len(with_stats) >= 5:
        _print_ctr_retention_trends(with_stats, recent_months=4)


def print_summary(entries: list[VideoEntry], stats_map: dict[str, VideoStats]) -> None:
    """Print a human-readable summary of per-video and aggregate stats."""
    if not entries:
        print("No uploaded videos found in output/*.manifest.json (need youtube_video_id).")
        return

    with_stats = [(e, stats_map[e.video_id]) for e in entries if e.video_id in stats_map]

    print(f"Found {len(entries)} uploaded video(s) with youtube_video_id.\n")
    print(f"{'date_id':<10}  {'fmt':>5}  {'views':>8}  {'likes':>6}  {'comments':>9}  {'title'}")
    print("-" * 88)

    for entry in entries:
        stats = stats_map.get(entry.video_id)
        fmt = (entry.aspect_ratio or "?").replace("16:9", "land").replace("9:16", "short")
        if not stats:
            print(
                f"{entry.date_id:<10}  {fmt:>5}  {'-':>8}  {'-':>6}  {'-':>9}  "
                f"(no stats for {entry.video_id})"
            )
            continue

        print(
            f"{entry.date_id:<10}  {fmt:>5}  {stats.view_count:>8}  {stats.like_count:>6}  "
            f"{stats.comment_count:>9}  {stats.title}"
        )

    _print_aggregate(entries, with_stats)

    if len(with_stats) >= 5:
        _print_growth_trends(with_stats)
        _print_manifest_breakdowns(with_stats)
        _print_narration_breakdown(with_stats)

    _print_analytics_aggregate(with_stats)
    if len(with_stats) >= 5:
        _print_ctr_retention_trends(with_stats)

    if len(with_stats) >= 5:
        shorts = [(e, s) for e, s in with_stats if e.aspect_ratio == "9:16"]
        land = [(e, s) for e, s in with_stats if e.aspect_ratio == "16:9"]
        if shorts and land:
            print("\nBy format (from manifest aspect_ratio):")
            print(f"  Shorts (9:16):  n={len(shorts):3}  avg views {_avg_views(shorts):,.0f}")
            print(f"  Landscape:      n={len(land):3}  avg views {_avg_views(land):,.0f}")


def write_csv(csv_path: Path, entries: list[VideoEntry], stats_map: dict[str, VideoStats]) -> None:
    """Write detailed stats to a CSV file. Analytics columns are blank unless --analytics was used."""
    rows: list[tuple] = []
    for entry in entries:
        stats = stats_map.get(entry.video_id)
        if not stats:
            continue
        rows.append(
            (
                entry.date_id,
                entry.video_id,
                entry.youtube_url or "",
                entry.aspect_ratio or "",
                stats.title,
                stats.view_count,
                stats.like_count,
                stats.comment_count,
                stats.published_at,
                f"{stats.avg_view_duration_sec:.1f}"
                if stats.avg_view_duration_sec is not None
                else "",
                f"{stats.avg_view_pct:.1f}" if stats.avg_view_pct is not None else "",
            )
        )

    if not rows:
        print("No stats to write to CSV (no matching videos).")
        return

    csv_path = csv_path.resolve()
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "date_id",
                "video_id",
                "youtube_url",
                "aspect_ratio",
                "title",
                "view_count",
                "like_count",
                "comment_count",
                "published_at",
                "avg_view_duration_sec",
                "avg_view_pct",
            ]
        )
        writer.writerows(rows)

    print(f"[OK] Wrote stats for {len(rows)} video(s) to {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch and summarize YouTube viewership stats for Lewis & Clark videos."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="Optional path to write detailed stats as CSV.",
    )
    parser.add_argument(
        "--scan-archive",
        action="store_true",
        help="Include archive/*/output/*.manifest.json (output/ wins on duplicate date_id)",
    )
    parser.add_argument(
        "--brief",
        action="store_true",
        help="Skip per-video table; print aggregate, growth, narration mix, top/bottom 5",
    )
    parser.add_argument(
        "--analytics",
        action="store_true",
        help=(
            "Also fetch retention (avg view duration/percent) via the YouTube Analytics API, "
            "and print retention trends by publish month. Does NOT include impressions/CTR "
            "(only available via the separate bulk YouTube Reporting API - not built). "
            "Requires the yt-analytics.readonly scope; re-run "
            "`python youtube_upload.py --login-only` once if token.json predates it."
        ),
    )
    args = parser.parse_args()

    entries = _iter_uploaded_entries(scan_archive=args.scan_archive)
    video_ids = [e.video_id for e in entries]
    if not video_ids:
        print("No uploaded videos found in output/*.manifest.json (need youtube_video_id).")
        return

    stats_map = fetch_stats_for_videos(video_ids)
    if args.analytics:
        analytics_map = fetch_analytics_for_videos(video_ids)
        merge_analytics(stats_map, analytics_map)

    if args.brief:
        print_brief_summary(entries, stats_map)
    else:
        print_summary(entries, stats_map)

    if args.csv:
        write_csv(args.csv, entries, stats_map)


if __name__ == "__main__":
    main()
