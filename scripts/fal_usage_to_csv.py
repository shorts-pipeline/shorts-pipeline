#!/usr/bin/env python3
"""
Download fal.ai platform usage (GET /v1/models/usage) into CSV.

Incremental runs: if state/fal_usage_state.json exists, fetches only [last_end, now)
so new rows append without re-downloading the full history.

Optional --compare-output matches final pipeline MP4 mtimes (UTC calendar day)
against summed usage for that day, plus video count that day and average cost per video.

Requires an Admin-scoped fal key for GET /v1/models/usage (API-only keys return 403).
Set FAL_ADMIN_KEY, or put the admin key in FAL_KEY / FAL_API_KEY. See:
https://docs.fal.ai/platform-apis/v1/models/usage

Examples:
  .\\.venv\\Scripts\\python.exe scripts/fal_usage_to_csv.py
  .\\.venv\\Scripts\\python.exe scripts/fal_usage_to_csv.py --full --days 120
  .\\.venv\\Scripts\\python.exe scripts/fal_usage_to_csv.py --compare-output
  .\\.venv\\Scripts\\python.exe scripts/fal_usage_to_csv.py --compare-only
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
except ImportError:
    pass

import requests

USAGE_URL = "https://api.fal.ai/v1/models/usage"
DEFAULT_STATE = _REPO / "state" / "fal_usage_state.json"
DEFAULT_CSV = _REPO / "logs" / "fal_usage.csv"
DEFAULT_COMPARE_CSV = _REPO / "logs" / "fal_usage_vs_outputs.csv"
OUTPUT_GLOB = "*_*_video*.mp4"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    s = dt.isoformat()
    return s.replace("+00:00", "Z")


def _parse_bucket_utc_date(bucket: str) -> str | None:
    """Return YYYY-MM-DD in UTC for a usage API bucket string."""
    if not bucket or not str(bucket).strip():
        return None
    s = str(bucket).strip()
    try:
        # fromisoformat handles offsets e.g. 2025-01-15T00:00:00-05:00
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).date().isoformat()
    except ValueError:
        return None


def _fal_key() -> str:
    return (
        os.environ.get("FAL_ADMIN_KEY")
        or os.environ.get("FAL_KEY")
        or os.environ.get("FAL_API_KEY")
        or ""
    ).strip()


def _fetch_usage_page(
    key: str,
    *,
    start: str | None,
    end: str,
    cursor: str | None,
    limit: int,
    timeout: float,
) -> dict[str, Any]:
    headers = {"Authorization": f"Key {key}"}
    params: list[tuple[str, str]] = [
        ("expand", "time_series"),
        ("expand", "auth_method"),
        ("bound_to_timeframe", "false"),
        ("limit", str(limit)),
    ]
    if start:
        params.append(("start", start))
    params.append(("end", end))
    if cursor:
        params.append(("cursor", cursor))
    r = requests.get(USAGE_URL, headers=headers, params=params, timeout=timeout)
    if r.status_code == 401:
        raise SystemExit("fal usage API returned 401. Check your key (FAL_ADMIN_KEY or FAL_KEY).")
    if r.status_code == 403:
        raise SystemExit(
            "fal usage API returned 403 Forbidden. "
            "Create an Admin-scoped key at https://fal.ai/dashboard/keys and set FAL_ADMIN_KEY "
            "(or replace FAL_KEY with that admin key). Inference-only API keys cannot call /v1/models/usage."
        )
    r.raise_for_status()
    return r.json()


def _flatten_time_series(data: dict[str, Any], fetched_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ts in data.get("time_series") or []:
        bucket = ts.get("bucket")
        for res in ts.get("results") or []:
            rows.append(
                {
                    "fetched_at_utc": fetched_at,
                    "bucket": bucket or "",
                    "endpoint_id": res.get("endpoint_id") or "",
                    "unit": res.get("unit") or "",
                    "quantity": res.get("quantity"),
                    "unit_price": res.get("unit_price"),
                    "cost": res.get("cost"),
                    "currency": res.get("currency") or "",
                    "auth_method": res.get("auth_method") or "",
                }
            )
    return rows


def _write_csv_rows(path: Path, rows: list[dict[str, Any]], write_header: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "fetched_at_utc",
        "bucket",
        "endpoint_id",
        "unit",
        "quantity",
        "unit_price",
        "cost",
        "currency",
        "auth_method",
    ]
    new_file = not path.exists()
    mode = "w" if new_file else "a"
    with path.open(mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if write_header and (new_file or path.stat().st_size == 0):
            w.writeheader()
        for row in rows:
            w.writerow(row)


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_usage_csv_for_daily_costs(csv_path: Path) -> dict[str, float]:
    """Sum cost by UTC calendar day (YYYY-MM-DD) from bucket column."""
    by_day: dict[str, float] = defaultdict(float)
    if not csv_path.exists():
        return dict(by_day)
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            b = row.get("bucket") or ""
            d = _parse_bucket_utc_date(b)
            if not d:
                continue
            try:
                c = float(row.get("cost") or 0)
            except (TypeError, ValueError):
                c = 0.0
            by_day[d] += c
    return dict(by_day)


def _mtime_utc_iso(path: Path) -> str:
    ts = path.stat().st_mtime
    dt = datetime.fromtimestamp(ts, tz=UTC)
    return _iso_z(dt)


def _date_id_from_video_name(name: str) -> str | None:
    from pipeline.output_naming import date_id_from_output_video_stem

    return date_id_from_output_video_stem(Path(name).stem)


def _compare_outputs(
    repo: Path,
    usage_csv: Path,
    compare_csv: Path,
) -> None:
    out_dir = repo / "output"
    by_day = _read_usage_csv_for_daily_costs(usage_csv)
    if not out_dir.is_dir():
        print(f"No {out_dir} directory; skipping comparison.", file=sys.stderr)
        return
    files = sorted(out_dir.glob(OUTPUT_GLOB))
    if not files:
        print(f"No files matching {OUTPUT_GLOB!r} under {out_dir}", file=sys.stderr)
        return
    compare_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "video_path",
        "date_id",
        "file_mtime_utc",
        "file_mtime_utc_date",
        "usage_cost_sum_usd_on_mtime_utc_date",
        "videos_on_mtime_utc_date",
        "usage_cost_avg_usd_per_video_on_mtime_utc_date",
    ]
    entries: list[tuple[Path, str, str, str]] = []
    for p in files:
        mtime_iso = _mtime_utc_iso(p)
        try:
            dt = datetime.fromisoformat(mtime_iso.replace("Z", "+00:00"))
            day = dt.astimezone(UTC).date().isoformat()
        except ValueError:
            day = ""
        did = _date_id_from_video_name(p.name) or ""
        entries.append((p, mtime_iso, day, did))

    videos_per_day: dict[str, int] = defaultdict(int)
    for _, _, day, _ in entries:
        if day:
            videos_per_day[day] += 1

    rows_out: list[dict[str, str]] = []
    for p, mtime_iso, day, did in entries:
        cost = by_day.get(day, 0.0)
        n = videos_per_day.get(day, 0)
        avg = (cost / n) if n else 0.0
        rows_out.append(
            {
                "video_path": str(p.relative_to(repo)),
                "date_id": did,
                "file_mtime_utc": mtime_iso,
                "file_mtime_utc_date": day,
                "usage_cost_sum_usd_on_mtime_utc_date": f"{cost:.6f}",
                "videos_on_mtime_utc_date": str(n),
                "usage_cost_avg_usd_per_video_on_mtime_utc_date": f"{avg:.6f}",
            }
        )
    with compare_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows_out)
    print(f"Wrote {compare_csv} ({len(rows_out)} rows).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download fal.ai usage to CSV (incremental).")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Usage CSV path (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE,
        help=f"State JSON for incremental window (default: {DEFAULT_STATE})",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ignore saved state; fetch from now--days through now.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        metavar="N",
        help="With --full or first run: start = now - N days (default: 90).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Page size for usage API (default: 500).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout seconds (default: 60).",
    )
    parser.add_argument(
        "--compare-output",
        action="store_true",
        help="After fetch, write comparison CSV vs output/ MP4 mtimes (see --compare-csv).",
    )
    parser.add_argument(
        "--compare-only",
        action="store_true",
        help="Skip API: read existing usage CSV and write comparison report only (no API key).",
    )
    parser.add_argument(
        "--compare-csv",
        type=Path,
        default=DEFAULT_COMPARE_CSV,
        help=f"Path for comparison report (default: {DEFAULT_COMPARE_CSV})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print window and row count only; do not write CSV or state.",
    )
    args = parser.parse_args()

    if args.compare_only:
        if not args.output.exists():
            raise SystemExit(f"No usage CSV at {args.output}; run without --compare-only first.")
        _compare_outputs(_REPO, args.output, args.compare_csv)
        return

    key = _fal_key()
    if not key:
        raise SystemExit(
            "Set FAL_ADMIN_KEY (recommended) or FAL_KEY / FAL_API_KEY in the environment or .env"
        )

    now = _utc_now()
    end_iso = _iso_z(now)

    state = None if args.full else _load_state(args.state)
    if state and state.get("last_end_exclusive_utc") and not args.full:
        start_iso = str(state["last_end_exclusive_utc"])
    else:
        start_dt = now - timedelta(days=max(1, args.days))
        start_iso = _iso_z(start_dt)

    fetched_at = _iso_z(now)
    all_rows: list[dict[str, Any]] = []
    cursor: str | None = None

    if args.dry_run:
        print(f"Window: start={start_iso} end={end_iso} (end exclusive per API)")
        # One page sample
        data = _fetch_usage_page(
            key,
            start=start_iso,
            end=end_iso,
            cursor=None,
            limit=min(args.limit, 50),
            timeout=args.timeout,
        )
        sample = _flatten_time_series(data, fetched_at)
        print(f"First page: {len(sample)} rows, has_more={data.get('has_more')}")
        return

    while True:
        data = _fetch_usage_page(
            key,
            start=start_iso,
            end=end_iso,
            cursor=cursor,
            limit=args.limit,
            timeout=args.timeout,
        )
        chunk = _flatten_time_series(data, fetched_at)
        all_rows.extend(chunk)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break

    out_path: Path = args.output
    write_header = not out_path.exists() or out_path.stat().st_size == 0
    _write_csv_rows(out_path, all_rows, write_header=write_header)

    try:
        csv_rel = str(out_path.relative_to(_REPO))
    except ValueError:
        csv_rel = str(out_path)
    _save_state(
        args.state,
        {
            "version": 1,
            "last_end_exclusive_utc": end_iso,
            "last_fetch_utc": fetched_at,
            "csv_path": csv_rel,
            "rows_appended": len(all_rows),
        },
    )

    print(f"Appended {len(all_rows)} usage rows to {out_path}")
    print(f"State updated: {args.state} (next start = {end_iso})")

    if args.compare_output:
        _compare_outputs(_REPO, out_path, args.compare_csv)


if __name__ == "__main__":
    main()
