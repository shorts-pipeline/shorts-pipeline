"""Tests for journal voice mining (exploration script)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pipeline.narration_common import extract_entry_text_and_author, iter_entry_labeled_paragraphs

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "journal_voice_mining"
_REPO = Path(__file__).resolve().parent.parent


def test_iter_entry_labeled_paragraphs_multi() -> None:
    path = _FIXTURES / "multi_author.xml"
    labeled, header = iter_entry_labeled_paragraphs(path)
    assert "wc" in header and "ml" in header
    labels = {lbl for lbl, _ in labeled}
    assert "William Clark" in labels or "Clark" in labels
    assert any("lewis" in lbl.lower() for lbl in labels)


def test_extract_still_matches_multi_author() -> None:
    path = _FIXTURES / "multi_author.xml"
    text, author = extract_entry_text_and_author(path)
    assert author and "Multiple journal authors" in author
    assert "[William Clark]" in text or "[Clark]" in text


def test_mine_script_on_fixtures(tmp_path: Path) -> None:
    out = tmp_path / "out"
    proc = subprocess.run(
        [
            sys.executable,
            str(_REPO / "scripts" / "mine_journal_dialogue_profiles.py"),
            "--journal-dir",
            str(_FIXTURES),
            "--out-dir",
            str(out),
            "--min-term-count",
            "1",
            "--top-n",
            "10",
        ],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads((out / "lewis_clark_journal_voice_report.json").read_text(encoding="utf-8"))
    cov = report["corpus_coverage"]
    assert cov["token_count_lewis"] > 0
    assert cov["token_count_clark"] > 0
    assert "comparison_to_curated" in report
    assert (out / "lewis_clark_journal_voice_report.md").is_file()
