"""Long-conversation inference for run-daily (CLI + on-disk narration)."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.narration_utils import infer_long_conversation_effective_from_cli_and_narration


def test_infer_long_true_when_file_has_mode_and_no_flags(tmp_path: Path) -> None:
    p = tmp_path / "narration18040528.json"
    p.write_text(json.dumps({"long_conversation_mode": True, "title": "t"}), encoding="utf-8")
    assert (
        infer_long_conversation_effective_from_cli_and_narration(
            long_conversation_arg=False,
            no_long_conversation_arg=False,
            narration_path=p,
        )
        is True
    )


def test_infer_long_false_when_no_long_flag(tmp_path: Path) -> None:
    p = tmp_path / "narration18040528.json"
    p.write_text(json.dumps({"long_conversation_mode": False, "title": "t"}), encoding="utf-8")
    assert not infer_long_conversation_effective_from_cli_and_narration(
        long_conversation_arg=False,
        no_long_conversation_arg=False,
        narration_path=p,
    )


def test_infer_long_false_when_explicit_no_long(tmp_path: Path) -> None:
    p = tmp_path / "narration18040528.json"
    p.write_text(json.dumps({"long_conversation_mode": True, "title": "t"}), encoding="utf-8")
    assert not infer_long_conversation_effective_from_cli_and_narration(
        long_conversation_arg=False,
        no_long_conversation_arg=True,
        narration_path=p,
    )


def test_infer_long_true_when_explicit_long_arg(tmp_path: Path) -> None:
    p = tmp_path / "missing.json"
    assert (
        infer_long_conversation_effective_from_cli_and_narration(
            long_conversation_arg=True,
            no_long_conversation_arg=False,
            narration_path=p,
        )
        is True
    )
