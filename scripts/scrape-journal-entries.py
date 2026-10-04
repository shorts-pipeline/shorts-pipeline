"""
Batch-fetch Lewis & Clark TEI XML journal entries from CDRH.
Run from project root: python scripts/scrape-journal-entries.py YYYY-MM-DD
Writes to journal-entries/ at project root (one year starting at the given date).
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

BASE_URL = "https://cdrhmedia.unl.edu/data/lewisandclark/source/tei/lc.jrn.{}.xml"


def project_root() -> Path:
    """Project root (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def output_dir() -> Path:
    return project_root() / "journal-entries"


def fetch_and_save(date_str: str) -> None:
    url = BASE_URL.format(date_str)
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            output_dir().mkdir(exist_ok=True)
            output_path = output_dir() / f"{date_str}.xml"
            output_path.write_bytes(response.content)
            print(f"✅ Saved: {date_str} -> {output_path}")
        else:
            print(f"❌ Skipped (not found): {date_str} [Status: {response.status_code}]")
    except Exception as e:
        print(f"❌ Error fetching {date_str}: {e}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/scrape-journal-entries.py YYYY-MM-DD")
        sys.exit(1)

    try:
        start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d")
    except ValueError:
        print("Invalid date format. Use YYYY-MM-DD.")
        sys.exit(1)

    for i in range(365):
        current_date = start_date + timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")
        fetch_and_save(date_str)
        time.sleep(1)  # Be polite to the server


if __name__ == "__main__":
    main()
