#!/usr/bin/env python3
"""
Upload a video to YouTube using OAuth 2.0.

Requires:
  - google-api-python-client, google-auth-oauthlib
  - client_secret_lewis_clark_upload.json (or client_secrets.json) from Google Cloud Console
  - YouTube Data API v3 enabled; OAuth 2.0 Client ID (Desktop app)
  - First run opens browser for OAuth; token.json is saved for future runs

Google Cloud Console: https://console.cloud.google.com/apis/credentials

Upload defaults (Lewis and Clark Shorts channel):
  - categoryId 27 (Education)
  - defaultLanguage / defaultAudioLanguage en
  - status.containsSyntheticMedia true (AI / synthetic disclosure)
  - #Shorts in description
  - English SRT captions from narration + audio/<date_id>/durations.json
"""

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import json
import sys
import threading
from datetime import datetime
from pathlib import Path

import video_manifest
from pipeline.output_naming import date_id_from_output_video_stem
from pipeline.youtube_metadata import (
    DESCRIPTION_ENGAGEMENT_QUESTION,
    ensure_shorts_hashtag,
)
from pipeline.youtube_srt import write_episode_srt

# If no OAuth callback received (e.g. 403 in browser), stop waiting after this many seconds
OAUTH_WAIT_TIMEOUT = 180

# Client secrets: prefer project file, then fallback for backwards compatibility
_PROJECT_ROOT = Path(__file__).resolve().parent
CLIENT_SECRETS = (
    _PROJECT_ROOT / "client_secret_lewis_clark_upload.json"
    if (_PROJECT_ROOT / "client_secret_lewis_clark_upload.json").exists()
    else _PROJECT_ROOT / "client_secrets.json"
)
TOKEN_FILE = _PROJECT_ROOT / "token.json"

# Shorts / education channel defaults
DEFAULT_CATEGORY_ID = "27"  # Education
DEFAULT_LANGUAGE = "en"

# SHORTS_HASHTAG, DESCRIPTION_ENGAGEMENT_QUESTION, ensure_shorts_hashtag,
# build_youtube_description now live in pipeline/youtube_metadata.py (imported above).
DEFAULT_DESCRIPTION_TEMPLATE = (
    """Daily reading from the original journals of the Lewis & Clark Expedition.

One episode per journal entry, following the expedition in calendar order.

Episode: {title}

"""
    + DESCRIPTION_ENGAGEMENT_QUESTION
)

DEFAULT_TAGS = [
    "Lewis and Clark",
    "Lewis & Clark",
    "Corps of Discovery",
    "history",
    "American history",
    "expedition",
    "documentary",
    "journals",
    "audiobook",
    "Shorts",
]


def title_from_video_path(video_path: Path) -> str | None:
    """
    Derive YouTube title from a pipeline video path when possible.
    Format: "narration title - {date}" or "Lewis & Clark Expedition: {date}" if no narration title.
    Returns None if date_id cannot be extracted from the filename.
    """
    video_path = Path(video_path)
    date_id = date_id_from_output_video_stem(video_path.stem)
    if not date_id:
        return None
    try:
        d = datetime.strptime(date_id, "%Y%m%d")
        title_date = d.strftime("%B %d, %Y")
    except ValueError:
        return None
    # Prefer narration title + date; else date-only title
    episode_title = ""
    narration_path = _PROJECT_ROOT / "narrations" / f"narration{date_id}.json"
    if narration_path.exists():
        try:
            data = json.loads(narration_path.read_text(encoding="utf-8"))
            episode_title = (data.get("title") or "").strip()
        except (json.JSONDecodeError, OSError):
            pass
    if episode_title:
        return f"{episode_title} - {title_date}"
    return f"Lewis & Clark Expedition: {title_date}"


def _oauth_scopes() -> list[str]:
    # force-ssl is required for captions.insert; upload covers videos.insert;
    # yt-analytics.readonly covers youtube_stats.py --analytics (impressions/CTR/retention).
    return [
        "https://www.googleapis.com/auth/youtube.force-ssl",
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]


def _stored_token_scopes(token_file: Path = TOKEN_FILE) -> list[str] | None:
    """Return scope list from token.json, or None when missing/unreadable."""
    if not token_file.exists():
        return None
    try:
        data = json.loads(token_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    scopes = data.get("scopes")
    return list(scopes) if isinstance(scopes, list) else []


def _missing_oauth_scopes(stored: list[str] | None, required: list[str]) -> list[str]:
    """Scopes present in required but absent from the saved token."""
    if stored is None:
        return []
    stored_set = set(stored)
    return [scope for scope in required if scope not in stored_set]


def _oauth_scope_short_name(scope: str) -> str:
    return scope.rsplit("/", 1)[-1] if scope else scope


def _warn_stale_token_scopes(missing: list[str]) -> None:
    """Explain stale token.json and remove it so the next step re-authorizes."""
    names = ", ".join(_oauth_scope_short_name(scope) for scope in missing)
    print(f"[WARN] Saved token.json is missing OAuth scope(s): {names}.")
    print(
        "Caption upload needs youtube.force-ssl (added in a recent pipeline update). "
        "Refreshing the old token cannot add new scopes."
    )
    print("Run: python youtube_upload.py --login-only")
    print("Then retry the upload.")
    TOKEN_FILE.unlink(missing_ok=True)


def _get_credentials():
    """Load (or obtain via OAuth flow) credentials covering all `_oauth_scopes()`."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES = _oauth_scopes()

    creds = None
    stored_scopes = _stored_token_scopes()
    missing_scopes = _missing_oauth_scopes(stored_scopes, SCOPES)
    if missing_scopes:
        _warn_stale_token_scopes(missing_scopes)
    elif TOKEN_FILE.exists():
        # Load with scopes actually stored so an expired token can refresh.
        load_scopes = stored_scopes if stored_scopes else SCOPES
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), load_scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                err = str(exc).lower()
                if "invalid_scope" in err:
                    print(
                        "[WARN] token.json cannot be refreshed with the current OAuth scopes "
                        "(saved token is stale)."
                    )
                    print("Run: python youtube_upload.py --login-only")
                else:
                    print(f"[WARN] Could not refresh token.json ({exc}). Re-authorizing...")
                TOKEN_FILE.unlink(missing_ok=True)
                creds = None
        if not creds or not creds.valid:
            if not CLIENT_SECRETS.exists():
                raise FileNotFoundError(
                    f"OAuth client secrets not found: {CLIENT_SECRETS}\n"
                    "Create credentials at https://console.cloud.google.com/apis/credentials\n"
                    "Enable YouTube Data API v3, create OAuth 2.0 Client ID (Desktop app),\n"
                    "download JSON, and save as client_secret_lewis_clark_upload.json or client_secrets.json"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
            print(
                "Opening browser for sign-in. Complete the sign-in there; this window will continue when you're done."
            )
            print("(If the browser doesn't open, copy the URL from below into your browser.)")
            creds_result = []
            flow_exc = []

            def _run_flow():
                try:
                    creds_result.append(flow.run_local_server(port=0))
                except Exception as e:
                    flow_exc.append(e)

            flow_thread = threading.Thread(target=_run_flow, daemon=True)
            flow_thread.start()
            flow_thread.join(timeout=OAUTH_WAIT_TIMEOUT)

            if flow_thread.is_alive():
                print(
                    f"\n[ERROR] Login timed out (no callback received in {OAUTH_WAIT_TIMEOUT // 60} minutes)."
                )
                print("If you saw 'Access blocked' or 403 in the browser:")
                print("  Add your Google account as a Test user:")
                print(
                    "  Google Cloud Console -> APIs & Services -> OAuth consent screen "
                    "-> Test users -> ADD USERS"
                )
                sys.exit(1)
            if flow_exc:
                raise flow_exc[0]
            creds = creds_result[0]
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


def get_authenticated_service():
    """Create authenticated YouTube Data API v3 client."""
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=_get_credentials())


def get_authenticated_analytics_service():
    """Create authenticated YouTube Analytics API v2 client (for youtube_stats.py --analytics)."""
    from googleapiclient.discovery import build

    return build("youtubeAnalytics", "v2", credentials=_get_credentials())


def delete_youtube_video(video_id: str) -> None:
    """Delete a video by ID (OAuth must include YouTube scope)."""
    youtube = get_authenticated_service()
    youtube.videos().delete(id=video_id).execute()


def build_video_insert_body(
    *,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
    privacy: str,
    contains_synthetic_media: bool,
    default_language: str = DEFAULT_LANGUAGE,
) -> dict:
    """Build videos.insert request body (snippet + status)."""
    return {
        "snippet": {
            "title": title[:100],
            "description": ensure_shorts_hashtag(description)[:5000],
            "tags": (tags or [])[:500],
            "categoryId": str(category_id),
            "defaultLanguage": default_language,
            "defaultAudioLanguage": default_language,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": bool(contains_synthetic_media),
        },
    }


def upload_english_captions(
    youtube,
    video_id: str,
    srt_path: Path,
    *,
    name: str = "English",
) -> str:
    """
    Upload (or replace) an English SRT caption track. Returns caption track id.
    Requires youtube.force-ssl scope.
    """
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    srt_path = Path(srt_path)
    if not srt_path.is_file():
        raise FileNotFoundError(f"SRT not found: {srt_path}")

    media = MediaFileUpload(
        str(srt_path),
        mimetype="application/octet-stream",
        resumable=False,
    )
    body = {
        "snippet": {
            "videoId": video_id,
            "language": DEFAULT_LANGUAGE,
            "name": name[:150],
            "isDraft": False,
        }
    }
    try:
        resp = youtube.captions().insert(part="snippet", body=body, media_body=media).execute()
        return str(resp.get("id") or "")
    except HttpError as e:
        # Replace existing track with same language+name.
        if getattr(e, "resp", None) is None or int(e.resp.status) != 409:
            raise
        listed = (
            youtube.captions().list(part="snippet", videoId=video_id).execute().get("items") or []
        )
        track_id = None
        for item in listed:
            sn = item.get("snippet") or {}
            if (
                str(sn.get("language") or "").lower() == DEFAULT_LANGUAGE
                and str(sn.get("name") or "") == name
            ):
                track_id = item.get("id")
                break
        if not track_id and listed:
            track_id = listed[0].get("id")
        if not track_id:
            raise
        media = MediaFileUpload(
            str(srt_path),
            mimetype="application/octet-stream",
            resumable=False,
        )
        resp = (
            youtube.captions()
            .update(
                part="snippet",
                body={"id": track_id, "snippet": {"isDraft": False}},
                media_body=media,
            )
            .execute()
        )
        return str(resp.get("id") or track_id)


def _date_id_for_upload(video_path: Path, manifest_path: Path | str | None) -> str | None:
    did = date_id_from_output_video_stem(Path(video_path).stem)
    if did:
        return did
    if manifest_path:
        data = video_manifest.load(Path(manifest_path)) or {}
        raw = str(data.get("date_id") or "").strip()
        if len(raw) == 8 and raw.isdigit():
            return raw
    return None


def upload_video(
    video_path: Path | str,
    title: str,
    description: str = "",
    category_id: str = DEFAULT_CATEGORY_ID,
    privacy: str = "public",  # public, unlisted, private
    tags: list[str] | None = None,
    manifest_path: Path | str | None = None,
    *,
    contains_synthetic_media: bool = True,
    upload_captions: bool = True,
    force: bool = False,
    allow_gap: bool = False,
) -> str:
    """
    Upload a video to YouTube. Returns the video ID.

    If manifest_path is provided, it is updated (or created if missing) with
    youtube_video_id and youtube_url. When the manifest does not exist, a minimal
    manifest is created using the video filename to derive date_id.

    contains_synthetic_media maps to YouTube status.containsSyntheticMedia (Studio
    "AI use" disclosure). Default True for this AI-generated pipeline.

    Before contacting YouTube, refuse (unless overridden) when:
      - the manifest already has a youtube_video_id (would create a duplicate) —
        pass force=True to re-upload;
      - an earlier assembled episode in output/ is not uploaded yet (would publish
        out of journal order) — pass allow_gap=True to skip that check.

    Raises FileNotFoundError if OAuth client secrets file is missing;
    DuplicateUploadError / UploadSequenceError for the guard conditions above.
    """
    from googleapiclient.http import MediaFileUpload

    from pipeline.youtube_upload_guard import check_upload_allowed

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    check_upload_allowed(
        video_path,
        manifest_path,
        repo_root=_PROJECT_ROOT,
        force=force,
        allow_gap=allow_gap,
    )

    # Apply default description if not provided
    if not description:
        description = DEFAULT_DESCRIPTION_TEMPLATE.format(title=title).strip()

    # Apply default tags if not provided; otherwise merge defaults with explicit tags
    if tags is None:
        tags = list(DEFAULT_TAGS)
    else:
        existing_lower = {t.lower() for t in tags}
        tags = tags + [t for t in DEFAULT_TAGS if t.lower() not in existing_lower]

    youtube = get_authenticated_service()

    body = build_video_insert_body(
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        privacy=privacy,
        contains_synthetic_media=contains_synthetic_media,
    )

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024,
    )

    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"   Upload progress: {int(status.progress() * 100)}%")

    video_id = response["id"]
    url = f"https://youtube.com/watch?v={video_id}"
    print(f"   Uploaded: {url}")

    if manifest_path is not None:
        if video_manifest.update_youtube(Path(manifest_path), video_id, url, video_path=video_path):
            print(f"   Saved link to {manifest_path}")

    if upload_captions:
        date_id = _date_id_for_upload(video_path, manifest_path)
        if date_id:
            try:
                srt_path = write_episode_srt(date_id, repo_root=_PROJECT_ROOT)
                cap_id = upload_english_captions(youtube, video_id, srt_path)
                print(
                    f"   Captions uploaded ({srt_path.name}"
                    + (f", id={cap_id}" if cap_id else "")
                    + ")"
                )
            except Exception as e:
                print(f"   [WARN] Caption upload skipped/failed: {e}")
        else:
            print("   [WARN] Caption upload skipped (could not derive date_id for SRT)")

    return video_id


def _iter_uploaded_manifests(*, scan_archive: bool) -> list[tuple[Path, str, str]]:
    """Return (manifest_path, date_id, youtube_video_id) with output/ winning over archive."""
    paths: list[Path] = []
    out_dir = _PROJECT_ROOT / "output"
    if out_dir.is_dir():
        paths.extend(sorted(out_dir.glob("*.manifest.json")))
    if scan_archive:
        arch = _PROJECT_ROOT / "archive"
        if arch.is_dir():
            paths.extend(sorted(arch.glob("*/output/*.manifest.json")))

    by_date: dict[str, tuple[Path, str]] = {}
    for path in paths:
        data = video_manifest.load(path) or {}
        did = str(data.get("date_id") or "").strip()
        vid = str(data.get("youtube_video_id") or "").strip()
        if not did or not vid:
            continue
        # First path wins: output/ is listed before archive/
        by_date.setdefault(did, (path, vid))
    return [(path, did, vid) for did, (path, vid) in sorted(by_date.items())]


def backfill_uploaded_videos(
    *,
    scan_archive: bool = False,
    dry_run: bool = False,
    with_captions: bool = False,
    limit: int | None = None,
    contains_synthetic_media: bool = True,
    category_id: str = DEFAULT_CATEGORY_ID,
) -> dict:
    """
    Update existing uploaded videos: synthetic-media disclosure, Education category,
    English language. Optionally upload/replace English SRT captions.
    """
    from googleapiclient.errors import HttpError

    rows = _iter_uploaded_manifests(scan_archive=scan_archive)
    if limit is not None:
        rows = rows[: max(0, int(limit))]

    youtube = None if dry_run else get_authenticated_service()
    stats = {
        "considered": len(rows),
        "updated": 0,
        "captions": 0,
        "skipped": 0,
        "errors": 0,
    }

    for _path, date_id, video_id in rows:
        label = f"{date_id} ({video_id})"
        try:
            if dry_run:
                print(
                    f"[dry-run] would update {label} category={category_id} synthetic={contains_synthetic_media}"
                )
                if with_captions:
                    print(f"[dry-run] would upload captions for {label}")
                stats["updated"] += 1
                if with_captions:
                    stats["captions"] += 1
                continue

            assert youtube is not None
            listed = (
                youtube.videos().list(part="snippet,status", id=video_id).execute().get("items")
                or []
            )
            if not listed:
                print(f"[WARN] Video not found on YouTube, skip: {label}")
                stats["skipped"] += 1
                continue
            item = listed[0]
            sn = item.get("snippet") or {}
            st = item.get("status") or {}
            title = str(sn.get("title") or "").strip() or f"Lewis & Clark {date_id}"
            description = ensure_shorts_hashtag(str(sn.get("description") or ""))
            tags = sn.get("tags") if isinstance(sn.get("tags"), list) else []
            privacy = str(st.get("privacyStatus") or "public")

            body = {
                "id": video_id,
                "snippet": {
                    "title": title[:100],
                    "description": description[:5000],
                    "tags": tags[:500],
                    "categoryId": str(category_id),
                    "defaultLanguage": DEFAULT_LANGUAGE,
                    "defaultAudioLanguage": DEFAULT_LANGUAGE,
                },
                "status": {
                    "privacyStatus": privacy,
                    "selfDeclaredMadeForKids": False,
                    "containsSyntheticMedia": bool(contains_synthetic_media),
                },
            }
            youtube.videos().update(part="snippet,status", body=body).execute()
            print(f"[OK] Updated {label}")
            stats["updated"] += 1

            if with_captions:
                try:
                    srt_path = write_episode_srt(date_id, repo_root=_PROJECT_ROOT)
                    upload_english_captions(youtube, video_id, srt_path)
                    print(f"   Captions OK ({srt_path})")
                    stats["captions"] += 1
                except Exception as e:
                    print(f"   [WARN] Captions failed for {label}: {e}")
                    stats["errors"] += 1
        except HttpError as e:
            print(f"[ERROR] {label}: {e}")
            stats["errors"] += 1
            # Quota exhaustion — stop early.
            content = getattr(e, "content", b"") or b""
            if b"quotaExceeded" in content or b"dailyLimitExceeded" in content:
                print("[ERROR] YouTube API quota exceeded; stopping backfill.")
                break
        except Exception as e:
            print(f"[ERROR] {label}: {e}")
            stats["errors"] += 1

    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Upload a video to YouTube")
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="Sign in to Google (removes existing token, opens browser, saves fresh token.json). No upload.",
    )
    parser.add_argument(
        "--backfill-synthetic-media",
        action="store_true",
        help=(
            "Update already-uploaded videos from manifests: containsSyntheticMedia, "
            f"categoryId={DEFAULT_CATEGORY_ID}, English language. Optional --with-captions."
        ),
    )
    parser.add_argument(
        "--with-captions",
        action="store_true",
        help="With --backfill-synthetic-media, also upload/replace English SRT captions.",
    )
    parser.add_argument(
        "--scan-archive",
        action="store_true",
        help="With --backfill-synthetic-media, also scan archive/*/output manifests.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --backfill-synthetic-media, print actions without calling YouTube.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="With --backfill-synthetic-media, process at most N videos.",
    )
    parser.add_argument("video", nargs="?", help="Path to MP4 file (omit when using --login-only)")
    parser.add_argument(
        "--title",
        "-t",
        help="Video title (default: from narration JSON for pipeline videos, else Lewis & Clark Expedition: date)",
    )
    parser.add_argument("--description", "-d", default="", help="Video description")
    parser.add_argument("--privacy", choices=["public", "unlisted", "private"], default="public")
    parser.add_argument("--tags", "-T", nargs="*", help="Tags (space-separated)")
    parser.add_argument(
        "--category-id",
        default=DEFAULT_CATEGORY_ID,
        help=f"YouTube categoryId (default: {DEFAULT_CATEGORY_ID} Education)",
    )
    parser.add_argument(
        "--no-synthetic-media",
        action="store_true",
        help="Do not set status.containsSyntheticMedia (default is to disclose AI/synthetic content)",
    )
    parser.add_argument(
        "--no-captions",
        action="store_true",
        help="Skip English SRT caption upload after the video upload",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Upload even if the manifest already has a youtube_video_id (creates a duplicate video).",
    )
    parser.add_argument(
        "--allow-gap",
        action="store_true",
        help="Upload even if earlier assembled episodes in output/ are not uploaded yet (out of journal order).",
    )
    parser.add_argument(
        "--manifest",
        "-m",
        type=Path,
        default=None,
        help="Path to manifest JSON to update with youtube_url (e.g. output/<prefix>_18030905_video.manifest.json)",
    )
    parser.add_argument(
        "--delete",
        metavar="VIDEO_ID",
        help="Delete a YouTube video by ID (no upload).",
    )
    args = parser.parse_args()

    if args.login_only:
        print("Signing in to Google (YouTube). A browser window will open.")
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
        get_authenticated_service()
        print("[OK] Logged in. Token saved to", TOKEN_FILE)
    elif args.backfill_synthetic_media:
        stats = backfill_uploaded_videos(
            scan_archive=args.scan_archive,
            dry_run=args.dry_run,
            with_captions=args.with_captions,
            limit=args.limit,
            contains_synthetic_media=not args.no_synthetic_media,
            category_id=args.category_id,
        )
        print(
            f"[OK] Backfill done: considered={stats['considered']} "
            f"updated={stats['updated']} captions={stats['captions']} "
            f"skipped={stats['skipped']} errors={stats['errors']}"
        )
    elif args.delete:
        delete_youtube_video(args.delete.strip())
        print(f"[OK] Deleted YouTube video {args.delete.strip()}")
    else:
        if not args.video:
            parser.error("video is required unless using --login-only / --backfill-synthetic-media")
        title = args.title
        if not title:
            title = title_from_video_path(Path(args.video))
            if not title:
                parser.error(
                    "could not derive title from filename (expected <prefix>_YYYYMMDD_video.mp4). Pass --title"
                )
        from pipeline.youtube_upload_guard import (
            DuplicateUploadError,
            UploadSequenceError,
        )

        try:
            upload_video(
                args.video,
                title=title,
                description=args.description,
                category_id=args.category_id,
                privacy=args.privacy,
                tags=args.tags,
                manifest_path=args.manifest,
                contains_synthetic_media=not args.no_synthetic_media,
                upload_captions=not args.no_captions,
                force=args.force,
                allow_gap=args.allow_gap,
            )
        except DuplicateUploadError as e:
            print(f"[SKIP] {e}")
            sys.exit(2)
        except UploadSequenceError as e:
            print(f"[ERROR] {e}")
            sys.exit(2)
