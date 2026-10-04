#!/usr/bin/env python3
"""
Sync ./archive to S3 (requires AWS CLI on PATH and a configured profile).

Uploads to s3://<bucket>/archive/ so keys mirror local paths (e.g. archive/1803-Q3/output/...).

Environment (optional overrides):
  LEWISCLARK_S3_BACKUP_BUCKET   default: lewisclark-youtube-backup-joshuaflank-291097289738-us-west-1-an
  LEWISCLARK_S3_BACKUP_PROFILE  default: AWS_PROFILE if set, else lewisclark-backup
  LEWISCLARK_S3_BACKUP_REGION   default: us-west-1

Usage (repo root):
  python scripts/backup_archive_to_s3.py --dry-run
  python scripts/backup_archive_to_s3.py
  python scripts/backup_archive_to_s3.py --delete   # also remove S3 objects missing locally; use with care
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BUCKET = "lewisclark-youtube-backup-joshuaflank-291097289738-us-west-1-an"
_DEFAULT_PROFILE_FALLBACK = "lewisclark-backup"
_DEFAULT_REGION = "us-west-1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync archive/ to S3 via aws s3 sync.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass AWS CLI --dryrun (no uploads).",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Pass --delete to aws s3 sync (removes remote keys not present under archive/).",
    )
    args = parser.parse_args()

    archive = _REPO_ROOT / "archive"
    if not archive.is_dir():
        print(f"error: missing directory {archive}", file=sys.stderr)
        sys.exit(1)

    if not shutil.which("aws"):
        print("error: aws CLI not found on PATH", file=sys.stderr)
        sys.exit(1)

    bucket = (os.environ.get("LEWISCLARK_S3_BACKUP_BUCKET") or _DEFAULT_BUCKET).strip()
    if not bucket:
        print("error: LEWISCLARK_S3_BACKUP_BUCKET is empty", file=sys.stderr)
        sys.exit(1)

    profile = (
        os.environ.get("LEWISCLARK_S3_BACKUP_PROFILE")
        or os.environ.get("AWS_PROFILE")
        or _DEFAULT_PROFILE_FALLBACK
    ).strip()

    region = (os.environ.get("LEWISCLARK_S3_BACKUP_REGION") or _DEFAULT_REGION).strip()

    dest = f"s3://{bucket}/archive/"
    cmd: list[str] = [
        "aws",
        "s3",
        "sync",
        str(archive),
        dest,
        "--profile",
        profile,
        "--region",
        region,
    ]
    if args.dry_run:
        cmd.append("--dryrun")
    if args.delete:
        cmd.append("--delete")

    print(" ".join(cmd))
    r = subprocess.run(cmd, cwd=str(_REPO_ROOT))
    sys.exit(r.returncode if r.returncode is not None else 1)


if __name__ == "__main__":
    main()
