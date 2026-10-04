"""Archive all pipeline artifacts for one date_id so the day can be re-run from scratch."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_DATE_ID_RE = re.compile(r"^\d{8}$")
_OUTPUT_DATE_RE = re.compile(r"(\d{8})")


@dataclass
class DayResetResult:
    date_id: str
    journal_date: str
    backup_dir: Path | None
    dry_run: bool
    moved: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "date_id": self.date_id,
            "journal_date": self.journal_date,
            "backup_dir": str(self.backup_dir) if self.backup_dir else None,
            "dry_run": self.dry_run,
            "moved": list(self.moved),
            "skipped": list(self.skipped),
            "errors": list(self.errors),
        }


def journal_date_from_date_id(date_id: str) -> str:
    if not _DATE_ID_RE.fullmatch(date_id):
        raise ValueError(f"date_id must be eight digits: {date_id!r}")
    return f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:8]}"


def narration_artifact_paths(repo_root: Path, date_id: str) -> list[Path]:
    """Top-level narration files for one episode (not directories)."""
    narr = repo_root / "narrations"
    if not narr.is_dir():
        return []
    out: list[Path] = []
    for pattern in (
        f"narration{date_id}.json",
        f"narration{date_id}.meta.json",
        f"narration{date_id}_voice.json",
        f"narration{date_id}_visual.json",
    ):
        p = narr / pattern
        if p.is_file():
            out.append(p)
    out.extend(sorted(narr.glob(f"narration{date_id}-*.json")))
    return out


def output_artifact_paths(repo_root: Path, date_id: str) -> list[Path]:
    out_dir = repo_root / "output"
    if not out_dir.is_dir():
        return []
    paths: list[Path] = []
    for p in out_dir.iterdir():
        if not p.is_file():
            continue
        m = _OUTPUT_DATE_RE.search(p.name)
        if m and m.group(1) == date_id:
            paths.append(p)
    return sorted(paths)


def day_artifact_paths(repo_root: Path, date_id: str) -> list[Path]:
    """All repo-root paths to move for a full day reset (files and directories)."""
    if not _DATE_ID_RE.fullmatch(date_id):
        raise ValueError(f"date_id must be eight digits: {date_id!r}")
    repo_root = Path(repo_root)
    paths: list[Path] = []
    paths.extend(narration_artifact_paths(repo_root, date_id))
    for kind in ("audio", "movie-images"):
        d = repo_root / kind / date_id
        if d.exists():
            paths.append(d)
    paths.extend(output_artifact_paths(repo_root, date_id))
    return paths


def _relative_to_repo(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def archive_day_artifacts(
    repo_root: Path,
    date_id: str,
    *,
    dry_run: bool = False,
    timestamp: str | None = None,
) -> DayResetResult:
    """
    Move narration, audio/<date_id>/, movie-images/<date_id>/, and output/* for date_id
    into archive/reset_<date_id>_<timestamp>/ (mirrors repo layout).
    """
    repo_root = Path(repo_root)
    journal_date = journal_date_from_date_id(date_id)
    ts = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = repo_root / "archive" / f"reset_{date_id}_{ts}"
    sources = day_artifact_paths(repo_root, date_id)
    result = DayResetResult(
        date_id=date_id,
        journal_date=journal_date,
        backup_dir=backup_dir if sources else None,
        dry_run=dry_run,
    )
    if not sources:
        result.skipped.append("(no artifacts found for this date)")
        return result

    if not dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)

    for src in sources:
        rel = _relative_to_repo(repo_root, src)
        dest = backup_dir / rel
        if dry_run:
            result.moved.append(rel)
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                result.errors.append(f"refusing to overwrite existing backup path: {rel}")
                continue
            shutil.move(str(src), str(dest))
            result.moved.append(rel)
        except OSError as e:
            result.errors.append(f"{rel}: {e}")

    if not dry_run and result.ok and result.moved:
        manifest = {
            "date_id": date_id,
            "journal_date": journal_date,
            "archived_at": ts,
            "moved": result.moved,
        }
        (backup_dir / "reset_manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

    return result


def revert_last_run_date_if_matches(repo_root: Path, journal_date: str) -> str | None:
    """
    If state/run_daily_state.json last_date equals journal_date, set last_date to the
    latest journal-entries/*.xml date strictly before journal_date, or remove the state file.
    Returns the new last_date (or None if cleared).
    """
    repo_root = Path(repo_root)
    state_path = repo_root / "state" / "run_daily_state.json"
    if not state_path.is_file():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        last = data.get("last_date")
    except (json.JSONDecodeError, OSError):
        return None
    if last != journal_date:
        return last if isinstance(last, str) else None

    journal_dir = repo_root / "journal-entries"
    prior: list[str] = []
    if journal_dir.is_dir():
        for p in journal_dir.glob("*.xml"):
            name = p.stem
            if len(name) == 10 and name[4] == "-" and name[7] == "-" and name < journal_date:
                prior.append(name)
    new_last = max(prior) if prior else None
    if new_last:
        state_path.write_text(
            json.dumps({"last_date": new_last}, indent=2),
            encoding="utf-8",
        )
    else:
        try:
            state_path.unlink()
        except OSError:
            pass
    return new_last
