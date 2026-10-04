#!/usr/bin/env python3
"""
Publish a video as an Instagram Reel using the Instagram API with Instagram
Login (Business/Creator account; no linked Facebook Page required).

Requires:
  - Instagram account converted to Professional (Business or Creator).
  - A Meta developer app (https://developers.facebook.com/apps) with the
    "Instagram API with Instagram Login" product added.
  - IG_APP_ID / IG_APP_SECRET in .env. IG_USER_ID (the Instagram-scoped user
    id) is printed by --login-only the first time — add it to .env afterward.
  - Meta's docs don't clearly confirm an http://localhost exception for the
    redirect URI, so login here always uses the same paste-the-redirected-
    URL-back pattern rather than a local callback server.
  - First run: python instagram_upload.py --login-only. Long-lived (60-day)
    token saved to instagram_token.json; later runs auto-refresh it once
    past the halfway point of that window.
  - AWS CLI on PATH with a configured profile (same one used by
    scripts/backup_archive_to_s3.py) — Reels publishing needs the video
    reachable at a public URL for Meta's servers to fetch, so this script
    stages the file at a short-lived S3 presigned URL and deletes it once
    Instagram confirms the video finished processing. (The alternative
    direct-upload path requires "Facebook Login for Business", a heavier
    setup not worth it for a solo-creator pipeline.)

NOTE ON UNCERTAINTY: built from Meta's current developer docs (2026), but
those docs are inconsistent about whether Graph calls under "Instagram API
with Instagram Login" go to graph.instagram.com or graph.facebook.com, and
whether instagram_business_content_publish needs App Review (Advanced
Access) before it works for anything beyond your own developer/test
account. Expect to debug the first real --login-only + upload attempt
against the actual API response. IG_GRAPH_API_BASE is an env override if
the host turns out to be wrong.

Instagram Graph API docs: https://developers.facebook.com/docs/instagram-platform
"""

from __future__ import annotations

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

import video_manifest
from pipeline.output_naming import date_id_from_output_video_stem
from pipeline.social_metadata import social_caption

_PROJECT_ROOT = Path(__file__).resolve().parent
TOKEN_FILE = _PROJECT_ROOT / "instagram_token.json"

AUTHORIZE_URL = "https://api.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
LONG_LIVED_EXCHANGE_URL = "https://graph.instagram.com/access_token"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
GRAPH_API_BASE = os.environ.get("IG_GRAPH_API_BASE", "https://graph.instagram.com/v21.0").rstrip(
    "/"
)

SCOPES = "instagram_business_basic,instagram_business_content_publish"

STATUS_POLL_INTERVAL = 10
STATUS_POLL_TIMEOUT = 300

MAX_VIDEO_SIZE = 300 * 1024 * 1024  # 300MB (Meta's stated Reels limit)

# Same S3 backup bucket/profile/region scripts/backup_archive_to_s3.py uses by
# default; override with IG_S3_* if you'd rather stage social uploads elsewhere.
_DEFAULT_S3_BUCKET = "lewisclark-youtube-backup-joshuaflank-291097289738-us-west-1-an"
_DEFAULT_S3_PROFILE_FALLBACK = "lewisclark-backup"
_DEFAULT_S3_REGION = "us-west-1"


def _app_id() -> str:
    v = os.environ.get("IG_APP_ID", "").strip()
    if not v:
        raise RuntimeError("IG_APP_ID not set (see .env.example)")
    return v


def _app_secret() -> str:
    v = os.environ.get("IG_APP_SECRET", "").strip()
    if not v:
        raise RuntimeError("IG_APP_SECRET not set (see .env.example)")
    return v


def _redirect_uri() -> str:
    return os.environ.get("IG_REDIRECT_URI", "").strip() or "https://localhost/instagram/callback"


def _ig_user_id() -> str:
    v = os.environ.get("IG_USER_ID", "").strip()
    if not v:
        raise RuntimeError(
            "IG_USER_ID not set. Run --login-only first, note the printed user_id, "
            "and add it to .env as IG_USER_ID."
        )
    return v


def authorize_url(state: str) -> str:
    params = {
        "client_id": _app_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def extract_code_from_redirect(pasted: str) -> str:
    """Accept either the full redirected URL or a bare code; return the code."""
    s = pasted.strip()
    if s.startswith("http://") or s.startswith("https://"):
        codes = parse_qs(urlparse(s).query).get("code")
        if not codes:
            raise ValueError("No 'code' parameter found in the pasted URL")
        # Instagram appends "#_" to the final redirect; strip it if present.
        return codes[0].split("#", 1)[0]
    return s


def exchange_code_for_short_lived_token(code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": _app_id(),
            "client_secret": _app_secret(),
            "grant_type": "authorization_code",
            "redirect_uri": _redirect_uri(),
            "code": code,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def exchange_for_long_lived_token(short_lived_token: str) -> dict:
    resp = requests.get(
        LONG_LIVED_EXCHANGE_URL,
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": _app_secret(),
            "access_token": short_lived_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_long_lived_token(access_token: str) -> dict:
    resp = requests.get(
        REFRESH_URL,
        params={"grant_type": "ig_refresh_token", "access_token": access_token},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _load_token() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_token(data: dict) -> None:
    data = dict(data)
    data["saved_at"] = time.time()
    TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def login_interactive() -> dict:
    """Print the authorize URL and accept the pasted-back redirect URL."""
    state = secrets.token_urlsafe(16)
    url = authorize_url(state)
    print("Opening Instagram sign-in in your browser. If it doesn't open, visit:")
    print(url)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print()
    print("After approving access, your browser will redirect to a URL starting")
    print(f"with {_redirect_uri()} — likely an error/can't-connect page (expected,")
    print("nothing is listening there). Copy the FULL URL from the address bar")
    print("and paste it below.")
    pasted = input("Redirected URL (or just the code=... value): ").strip()
    code = extract_code_from_redirect(pasted)
    short = exchange_code_for_short_lived_token(code)
    if "access_token" not in short:
        raise RuntimeError(f"Instagram token exchange failed: {short}")
    user_id = short.get("user_id")
    long_lived = exchange_for_long_lived_token(str(short["access_token"]))
    if "access_token" not in long_lived:
        raise RuntimeError(f"Instagram long-lived token exchange failed: {long_lived}")
    long_lived["user_id"] = user_id
    _save_token(long_lived)
    if user_id:
        print(f"Instagram-scoped user_id: {user_id}")
        print("Add this to .env as IG_USER_ID if not already set.")
    return long_lived


def get_access_token() -> str:
    """Load a valid access token, refreshing once past the halfway point of the
    60-day window (refresh requires the token be at least 24h old, which
    halfway comfortably clears); raise with instructions if absent."""
    token = _load_token()
    if token is None:
        raise RuntimeError("No Instagram token found. Run: python instagram_upload.py --login-only")
    access_token = str(token["access_token"])
    saved_at = float(token.get("saved_at") or 0)
    expires_in = float(token.get("expires_in") or 0)
    if expires_in and time.time() > saved_at + expires_in / 2:
        try:
            refreshed = refresh_long_lived_token(access_token)
            if "access_token" in refreshed:
                refreshed.setdefault("user_id", token.get("user_id"))
                _save_token(refreshed)
                return str(refreshed["access_token"])
        except requests.RequestException as e:
            print(f"[WARN] Instagram token refresh failed ({e}); using existing token.")
    return access_token


def _s3_bucket() -> str:
    return (
        os.environ.get("IG_S3_BUCKET")
        or os.environ.get("LEWISCLARK_S3_BACKUP_BUCKET")
        or _DEFAULT_S3_BUCKET
    ).strip()


def _s3_profile() -> str:
    return (
        os.environ.get("IG_S3_PROFILE")
        or os.environ.get("LEWISCLARK_S3_BACKUP_PROFILE")
        or os.environ.get("AWS_PROFILE")
        or _DEFAULT_S3_PROFILE_FALLBACK
    ).strip()


def _s3_region() -> str:
    return (
        os.environ.get("IG_S3_REGION")
        or os.environ.get("LEWISCLARK_S3_BACKUP_REGION")
        or _DEFAULT_S3_REGION
    ).strip()


def stage_public_url(video_path: Path, *, expires_in: int = 3600) -> tuple[str, str]:
    """Upload video_path to S3 (social_staging/ prefix) and return (s3_key,
    presigned_url). The object stays private; the presigned URL is the only
    thing that's reachable, and only for `expires_in` seconds."""
    if not shutil.which("aws"):
        raise RuntimeError(
            "aws CLI not found on PATH (required to stage a public URL for Instagram)"
        )

    video_path = Path(video_path)
    key = f"social_staging/{video_path.name}"
    s3_uri = f"s3://{_s3_bucket()}/{key}"

    subprocess.run(
        [
            "aws",
            "s3",
            "cp",
            str(video_path),
            s3_uri,
            "--profile",
            _s3_profile(),
            "--region",
            _s3_region(),
        ],
        check=True,
    )
    result = subprocess.run(
        [
            "aws",
            "s3",
            "presign",
            s3_uri,
            "--profile",
            _s3_profile(),
            "--region",
            _s3_region(),
            "--expires-in",
            str(expires_in),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return key, result.stdout.strip()


def unstage(key: str) -> None:
    subprocess.run(
        [
            "aws",
            "s3",
            "rm",
            f"s3://{_s3_bucket()}/{key}",
            "--profile",
            _s3_profile(),
            "--region",
            _s3_region(),
        ],
        check=False,
    )


def create_media_container(access_token: str, *, video_url: str, caption: str) -> str:
    resp = requests.post(
        f"{GRAPH_API_BASE}/{_ig_user_id()}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption[:2200],
            "access_token": access_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"Instagram media container creation failed: {data}")
    return str(data["id"])


def poll_container_status(
    access_token: str,
    container_id: str,
    *,
    timeout: int = STATUS_POLL_TIMEOUT,
    interval: int = STATUS_POLL_INTERVAL,
) -> str:
    deadline = time.time() + timeout
    status = "IN_PROGRESS"
    while time.time() < deadline:
        resp = requests.get(
            f"{GRAPH_API_BASE}/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        )
        resp.raise_for_status()
        status = resp.json().get("status_code", "IN_PROGRESS")
        if status in ("FINISHED", "PUBLISHED"):
            return status
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram container processing failed: status_code={status}")
        time.sleep(interval)
    raise TimeoutError(f"Instagram container still {status} after {timeout}s")


def publish_container(access_token: str, container_id: str) -> str:
    resp = requests.post(
        f"{GRAPH_API_BASE}/{_ig_user_id()}/media_publish",
        data={"creation_id": container_id, "access_token": access_token},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"Instagram media_publish failed: {data}")
    return str(data["id"])


def fetch_permalink(access_token: str, media_id: str) -> str | None:
    try:
        resp = requests.get(
            f"{GRAPH_API_BASE}/{media_id}",
            params={"fields": "permalink", "access_token": access_token},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("permalink")
    except requests.RequestException:
        return None


def upload_video(
    video_path: Path | str,
    *,
    caption: str | None = None,
    manifest_path: Path | str | None = None,
    date_id: str | None = None,
    force: bool = False,
) -> str:
    """Publish a video as an Instagram Reel. Returns the published media id.

    If manifest_path is provided, it is updated with instagram_media_id and
    instagram_url. Refuses to re-post when the manifest already has an
    instagram_media_id unless force=True.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    if manifest_path is not None and not force:
        existing = video_manifest.load(Path(manifest_path))
        if existing and existing.get("instagram_media_id"):
            raise RuntimeError(
                f"{manifest_path} already has instagram_media_id="
                f"{existing['instagram_media_id']!r}; pass force=True to re-post."
            )

    if not caption:
        did = date_id or date_id_from_output_video_stem(video_path.stem)
        caption = social_caption(did) if did else video_path.stem

    size = video_path.stat().st_size
    if size > MAX_VIDEO_SIZE:
        raise ValueError(f"Video too large for Instagram Reels ({size} bytes > {MAX_VIDEO_SIZE})")

    access_token = get_access_token()

    print("   Staging video at a temporary presigned S3 URL...")
    key, url = stage_public_url(video_path)
    try:
        print("   Creating Instagram media container...")
        container_id = create_media_container(access_token, video_url=url, caption=caption)
        print(f"   Container: {container_id}; waiting for processing...")
        poll_container_status(access_token, container_id)
    finally:
        unstage(key)

    media_id = publish_container(access_token, container_id)
    permalink = fetch_permalink(access_token, media_id)
    print(f"   Published: {permalink or media_id}")

    if manifest_path is not None:
        if video_manifest.update_instagram(
            Path(manifest_path), media_id, permalink, video_path=video_path
        ):
            print(f"   Saved link to {manifest_path}")

    return media_id


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Publish a video as an Instagram Reel")
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="Sign in to Instagram (removes existing token, saves fresh instagram_token.json). No upload.",
    )
    parser.add_argument("video", nargs="?", help="Path to MP4 file (omit when using --login-only)")
    parser.add_argument(
        "--caption",
        "--title",
        dest="caption",
        help="Reel caption (default: derived from narration title for pipeline videos)",
    )
    parser.add_argument(
        "--manifest",
        "-m",
        type=Path,
        default=None,
        help="Path to manifest JSON to update with instagram_media_id/instagram_url",
    )
    parser.add_argument(
        "--date-id",
        default=None,
        help="Journal date id (YYYYMMDD) to derive the caption from narrations/, if not passed via --caption",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Post even if the manifest already has an instagram_media_id (creates a duplicate post)",
    )
    args = parser.parse_args()

    if args.login_only:
        print("Signing in to Instagram. A browser window will open.")
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
        login_interactive()
        print("[OK] Logged in. Token saved to", TOKEN_FILE)
    else:
        if not args.video:
            parser.error("video is required unless using --login-only")
        try:
            upload_video(
                args.video,
                caption=args.caption,
                manifest_path=args.manifest,
                date_id=args.date_id,
                force=args.force,
            )
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)
