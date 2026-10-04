"""Structured per-run video generation reports (FAL Wan + talking-head)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUN_REPORT_VERSION = "1.0"


@dataclass
class ClipReportRow:
    segment: int
    kind: str
    action: str
    detail: str | None = None
    model: str | None = None
    reason_code: str | None = None


@dataclass
class RunReportDocument:
    date_id: str
    vendor: str
    b_roll_rows: list[ClipReportRow] = field(default_factory=list)
    talking_head_rows: list[ClipReportRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "run_report_version": RUN_REPORT_VERSION,
            "completed_at_utc": datetime.now(UTC).isoformat(),
            "date_id": self.date_id,
            "vendor": self.vendor,
            "b_roll": [asdict(r) for r in self.b_roll_rows],
            "talking_head": [asdict(r) for r in self.talking_head_rows],
            "notes": list(self.notes),
        }


def write_run_report(output_dir: Path, doc: RunReportDocument) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "run_report.json"
    path.write_text(
        json.dumps(doc.to_json_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_run_report(output_dir: Path) -> dict[str, Any] | None:
    p = output_dir / "run_report.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
