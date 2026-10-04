#!/usr/bin/env python3
"""
Create a YouTube playlist from uploaded journal-entry videos.

Data source is **local** manifest JSON only (output/*.manifest.json, and optionally
archive/*/output/*.manifest.json). Each row uses the stored youtube_video_id; that field
is whatever was written at upload time (or by hand). Stale manifests after a video is
deleted on YouTube are not detected unless you pass --verify-youtube.

Uses the same OAuth token as youtube_upload.py (run youtube_upload.py --login-only first
if needed; the token must include the youtube scope).
"""

import argparse
from pathlib import Path

from googleapiclient.errors import HttpError

import video_manifest
from youtube_upload import get_authenticated_service

_PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = _PROJECT_ROOT / "output"
ARCHIVE_DIR = _PROJECT_ROOT / "archive"

DEFAULT_TITLE = "Lewis & Clark Expedition Journal"
DEFAULT_DESCRIPTION = "Journal entries from the Lewis and Clark Expedition, one video per day. "


def _iter_manifest_paths(*, scan_archive: bool) -> list[Path]:
    """Manifest paths: repo output/ first, then archive/*/output/ (does not override dates already seen)."""
    paths: list[Path] = []
    if OUTPUT_DIR.is_dir():
        paths.extend(sorted(OUTPUT_DIR.glob("*.manifest.json")))
    if scan_archive and ARCHIVE_DIR.is_dir():
        paths.extend(sorted(ARCHIVE_DIR.glob("*/output/*.manifest.json")))
    return paths


def collect_uploaded_videos(
    *,
    scan_archive: bool = False,
    before_date_id: str | None = None,
    from_date_id: str | None = None,
    shorts_only: bool = False,
) -> list[tuple[str, str]]:
    """
    Collect (date_id, youtube_video_id) from manifests that have been uploaded.
    When the same date_id appears in output/ and archive/, output/ wins.

    before_date_id: if set, include only date_id < before_date_id (strictly before this journal day).
    from_date_id: if set, include only date_id >= from_date_id.
    shorts_only: if True, include only manifests with aspect_ratio \"9:16\".
    Sorted by date_id ascending.
    """
    # date_id -> video_id (first manifest wins: output paths come first in _iter_manifest_paths)
    seen: set[str] = set()
    entries: list[tuple[str, str]] = []
    for path in _iter_manifest_paths(scan_archive=scan_archive):
        data = video_manifest.load(path)
        if not data:
            continue
        date_id = data.get("date_id")
        video_id = data.get("youtube_video_id")
        if not date_id or not video_id:
            continue
        if not isinstance(date_id, str) or len(date_id) != 8 or not date_id.isdigit():
            continue
        if date_id in seen:
            continue
        if before_date_id and date_id >= before_date_id:
            continue
        if from_date_id and date_id < from_date_id:
            continue
        if shorts_only:
            ar = (data.get("aspect_ratio") or "").strip()
            if ar != "9:16":
                continue
        seen.add(date_id)
        entries.append((date_id, video_id))
    entries.sort(key=lambda x: x[0])
    return entries


def verify_entries_on_youtube(
    youtube,
    entries: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    """
    Batch-check video IDs with videos.list (50 per request).
    Returns (ok_entries, skipped) where each skipped item is (date_id, video_id, reason).
    IDs missing from the API response are treated as deleted, private to others, or invalid.
    """
    if not entries:
        return [], []
    unique_ids = list(dict.fromkeys(vid for _, vid in entries))
    per_id_reason: dict[str, str | None] = dict.fromkeys(unique_ids)
    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i : i + 50]
        resp = youtube.videos().list(part="id,status", id=",".join(batch)).execute()
        returned = {item["id"]: item for item in resp.get("items", [])}
        for vid in batch:
            if vid not in returned:
                per_id_reason[vid] = (
                    "not returned by API (deleted, wrong id, or inaccessible to this account)"
                )
                continue
            st = returned[vid].get("status") or {}
            us = (st.get("uploadStatus") or "").lower()
            if us in ("rejected", "failed", "deleted"):
                per_id_reason[vid] = f"uploadStatus={us}"
            else:
                per_id_reason[vid] = None
    good: list[tuple[str, str]] = []
    skipped: list[tuple[str, str, str]] = []
    for date_id, vid in entries:
        reason = per_id_reason.get(vid)
        if reason:
            skipped.append((date_id, vid, reason))
        else:
            good.append((date_id, vid))
    return good, skipped


def create_playlist(
    youtube,
    *,
    title: str = DEFAULT_TITLE,
    description: str = DEFAULT_DESCRIPTION,
    privacy: str = "public",
) -> str:
    """Create a playlist and return its ID."""
    body = {
        "snippet": {
            "title": title,
            "description": description,
        },
        "status": {
            "privacyStatus": privacy,
        },
    }
    response = youtube.playlists().insert(part="snippet,status", body=body).execute()
    return response["id"]


def add_video_to_playlist(
    youtube, playlist_id: str, video_id: str, *, position: int | None = None
) -> None:
    """Insert a video into a playlist. If position is None, omit position so YouTube appends to the end."""
    snippet: dict = {
        "playlistId": playlist_id,
        "resourceId": {
            "kind": "youtube#video",
            "videoId": video_id,
        },
    }
    if position is not None:
        snippet["position"] = position
    body = {"snippet": snippet}
    youtube.playlistItems().insert(part="snippet", body=body).execute()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a YouTube playlist from uploaded journal-entry videos"
    )
    parser.add_argument(
        "--title",
        "-t",
        default=DEFAULT_TITLE,
        help=f"Playlist title (default: {DEFAULT_TITLE!r})",
    )
    parser.add_argument(
        "--description",
        "-d",
        default=DEFAULT_DESCRIPTION,
        help="Playlist description",
    )
    parser.add_argument(
        "--privacy",
        choices=["public", "unlisted", "private"],
        default="public",
        help="Playlist privacy (default: public)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list which videos would be added; do not create the playlist",
    )
    parser.add_argument(
        "--scan-archive",
        action="store_true",
        help="Also read archive/*/output/*.manifest.json (output/ wins when the same date_id exists in both)",
    )
    parser.add_argument(
        "--before-date",
        metavar="YYYYMMDD",
        default=None,
        help="Include only journal dates strictly before this date_id (e.g. 18031212 excludes Dec 12, 1803 onward)",
    )
    parser.add_argument(
        "--from-date",
        metavar="YYYYMMDD",
        default=None,
        help="Include only journal dates on or after this date_id",
    )
    parser.add_argument(
        "--shorts-only",
        action="store_true",
        help="Include only manifests with aspect_ratio 9:16 (YouTube Shorts)",
    )
    parser.add_argument(
        "--verify-youtube",
        action="store_true",
        help="Call YouTube Data API to drop video IDs that are missing, rejected, or inaccessible "
        "(recommended if manifests may be stale after deletes)",
    )
    parser.add_argument(
        "--playlist-id",
        metavar="PLAYLIST_ID",
        default=None,
        help="Append videos to this existing playlist instead of creating a new one (use with date filters)",
    )
    args = parser.parse_args()

    entries = collect_uploaded_videos(
        scan_archive=args.scan_archive,
        before_date_id=args.before_date,
        from_date_id=args.from_date,
        shorts_only=args.shorts_only,
    )
    if not entries:
        print(
            "No uploaded videos matched (need youtube_video_id in manifests). "
            "Try --scan-archive if shorts live under archive/. Check --shorts-only / date filters."
        )
        return

    youtube = None
    if args.verify_youtube:
        youtube = get_authenticated_service()
        before_n = len(entries)
        entries, skipped = verify_entries_on_youtube(youtube, entries)
        if skipped:
            print(
                f"Skipped {len(skipped)} manifest row(s) not usable on YouTube (see reasons below):"
            )
            for date_id, video_id, reason in skipped:
                print(f"  {date_id}  https://youtube.com/watch?v={video_id}  ({reason})")
            print()
        print(
            f"After --verify-youtube: {len(entries)} of {before_n} manifest row(s) are playable on this account."
        )
        if not entries:
            print("Nothing left to add to a playlist.")
            return

    filters = []
    if args.shorts_only:
        filters.append("shorts 9:16 only")
    if args.before_date:
        filters.append(f"before {args.before_date}")
    if args.from_date:
        filters.append(f"from {args.from_date}")
    if args.scan_archive:
        filters.append("including archive/*/output/")
    if args.verify_youtube:
        filters.append("verified on YouTube")
    if args.playlist_id:
        filters.append(f"append to playlist {args.playlist_id}")
    filt_s = f" [{'; '.join(filters)}]" if filters else ""
    print(f"Playlist candidate: {len(entries)} video(s){filt_s}:")
    for date_id, video_id in entries:
        print(f"  {date_id}  https://youtube.com/watch?v={video_id}")

    if args.dry_run:
        print(
            "[DRY-RUN] Would create or update playlist and add these videos. "
            "Run without --dry-run to apply."
        )
        return

    if youtube is None:
        youtube = get_authenticated_service()
    if args.playlist_id:
        playlist_id = args.playlist_id.strip()
        playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        print(f"Appending to existing playlist: {playlist_url}")
    else:
        playlist_id = create_playlist(
            youtube,
            title=args.title,
            description=args.description,
            privacy=args.privacy,
        )
        playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        print(f"Created playlist: {playlist_url}")

    added = 0
    skipped_insert = 0
    for date_id, video_id in entries:
        try:
            add_video_to_playlist(youtube, playlist_id, video_id, position=None)
            print(f"  Added {date_id}")
            added += 1
        except HttpError as err:
            if err.resp.status == 404:
                print(
                    f"  [SKIP] {date_id}  https://youtube.com/watch?v={video_id}  (not found on YouTube)"
                )
                skipped_insert += 1
                continue
            raise

    if skipped_insert:
        print(f"[WARN] Skipped {skipped_insert} video(s) that could not be added (see above).")
    print(f"[OK] Added {added} video(s) to playlist: {playlist_url}")


if __name__ == "__main__":
    main()
