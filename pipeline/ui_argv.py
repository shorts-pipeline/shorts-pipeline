"""Build the ``run-daily.py`` argv from Pipeline UI form values + ``options.json``.

Pure logic extracted from ``pipeline_ui/server.py`` so it is unit-testable without
the HTTP server. ``build_argv`` takes ``repo_root`` and a resolved
``python_executable`` explicitly instead of reading server module globals.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from pipeline.narration_utils import (
    narration_json_expects_dialogue_mode,
    narration_json_expects_long_conversation_mode,
)

_PIPELINE_WORKFLOWS = frozenset({"narration", "video", "full"})

# Journal dates only ever fall in the expedition years; mirrors pipeline_ui.server.
_JOURNAL_YEAR_MIN = 1803
_JOURNAL_YEAR_MAX = 1806


def boolish(val: Any, default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _safe_journal_date(date_part: str) -> str | None:
    """Return normalized YYYY-MM-DD if valid and in expedition-year range, else None."""
    s = unquote((date_part or "").strip())
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return None
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    if not (_JOURNAL_YEAR_MIN <= dt.year <= _JOURNAL_YEAR_MAX):
        return None
    return s


def _narration_json_for_form_date(narrations_dir: Path, date_raw: Any) -> dict[str, Any] | None:
    """Load ``narrations/narration<date_id>.json`` for a form ``date`` value, or None.

    Returns None for a missing/``next``/invalid date or an unreadable file.
    """
    if date_raw is None:
        return None
    s = str(date_raw).strip()
    if not s or s.lower() == "next":
        return None
    safe = _safe_journal_date(s)
    if not safe:
        return None
    did = safe.replace("-", "")
    path = narrations_dir / f"narration{did}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _week_arc_defers_mode_flags(values: dict[str, Any]) -> bool:
    """
    When Use week arc is on and dialogue/long-conversation checkboxes are unchecked,
    omit --no-dialogue / --no-long-conversation so week arc (or existing narration JSON)
    can choose the mode.
    """
    if not boolish(values.get("use_week_arc"), False):
        return False
    if boolish(values.get("dialogue_mode"), False):
        return False
    if boolish(values.get("long_conversation_mode"), False):
        return False
    return True


def _user_explicit_dialogue_opt_out(values: dict[str, Any]) -> bool:
    """True when the UI/form sent dialogue_mode and it is off (regenerate without dialogue)."""
    if "dialogue_mode" not in values:
        return False
    # Week arc owns mode selection when enabled and the user did not force dialogue/long on.
    if _week_arc_defers_mode_flags(values):
        return False
    return not boolish(values.get("dialogue_mode"), default=False)


def _user_explicit_long_conversation_opt_out(values: dict[str, Any]) -> bool:
    """True when the form sent long_conversation_mode off (force not-long)."""
    if "long_conversation_mode" not in values:
        return False
    if _week_arc_defers_mode_flags(values):
        return False
    return not boolish(values.get("long_conversation_mode"), default=False)


def _apply_dialogue_mode_from_narration_file(
    merged: dict[str, Any],
    *,
    source_values: dict[str, Any],
    narrations_dir: Path,
) -> None:
    """
    When ``narrations/narration<date_id>.json`` has ``dialogue_mode: true`` (or legacy
    dialogue segments), promote ``dialogue_mode`` to True so run-daily gets ``--dialogue``.

    If the form already turned dialogue off, do not override—user is regenerating without
    dialogue even when the saved JSON used dialogue.
    """
    if _user_explicit_dialogue_opt_out(source_values):
        merged["dialogue_mode"] = False
        return
    data = _narration_json_for_form_date(narrations_dir, merged.get("date"))
    if data is None:
        return
    if narration_json_expects_dialogue_mode(data):
        merged["dialogue_mode"] = True


def _apply_long_conversation_mode_from_narration_file(
    merged: dict[str, Any],
    *,
    source_values: dict[str, Any],
    narrations_dir: Path,
) -> None:
    """Promote long_conversation_mode from narration JSON unless the user forced it off."""
    if _user_explicit_long_conversation_opt_out(source_values):
        merged["long_conversation_mode"] = False
        return
    data = _narration_json_for_form_date(narrations_dir, merged.get("date"))
    if data is None:
        return
    if narration_json_expects_long_conversation_mode(data):
        merged["long_conversation_mode"] = True
        if not _user_explicit_dialogue_opt_out(source_values):
            merged["dialogue_mode"] = True


def _field_allowed_for_workflow(field: dict[str, Any], workflow: str) -> bool:
    wfs = field.get("workflows")
    if not wfs:
        return True
    if not isinstance(wfs, (list, tuple)):
        return True
    return workflow in wfs


def build_argv(
    values: dict[str, Any],
    spec: dict[str, Any],
    workflow: str | None = None,
    *,
    repo_root: Path,
    python_executable: str,
    narrations_dir: Path | None = None,
) -> list[str]:
    """Build argv for run-daily.py from form values and options.json."""
    repo_root = Path(repo_root)
    narrations_dir = (
        Path(narrations_dir) if narrations_dir is not None else repo_root / "narrations"
    )

    wf = (workflow or "full").strip().lower()
    if wf not in _PIPELINE_WORKFLOWS:
        wf = "full"

    merged: dict[str, Any] = dict(values)
    # Long conversation prompt pack always implies dialogue (unless user explicitly opted out).
    if boolish(merged.get("long_conversation_mode"), False) and not _user_explicit_dialogue_opt_out(
        values
    ):
        merged["dialogue_mode"] = True
    _apply_long_conversation_mode_from_narration_file(
        merged, source_values=values, narrations_dir=narrations_dir
    )
    _apply_dialogue_mode_from_narration_file(
        merged, source_values=values, narrations_dir=narrations_dir
    )
    if wf == "video" and "ambient" not in merged:
        merged["ambient"] = True

    backend = spec.get("backend") or {}
    script = backend.get("script", "run-daily.py")
    py = python_executable
    script_path = repo_root / script
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")
    cmd: list[str] = [py, str(script_path)]

    fields: list[dict[str, Any]] = spec.get("fields") or []

    def v(fid: str) -> Any:
        return merged.get(fid)

    for field in fields:
        if not _field_allowed_for_workflow(field, wf):
            continue
        fid = field.get("id")
        if not fid:
            continue
        only_if = field.get("only_if")
        if only_if:
            dep = only_if.get("field")
            need = only_if.get("equals")
            dep_val = v(dep)
            if isinstance(need, bool):
                if boolish(dep_val) != need:
                    continue
            elif dep_val != need:
                continue

        ftype = field.get("type", "text")
        raw = v(fid)

        if ftype == "text":
            omit = field.get("omit_if_empty", True)
            arg = field.get("arg")
            if not arg:
                continue
            s = (raw if isinstance(raw, str) else str(raw or "")).strip()
            if not s and omit:
                continue
            cmd.extend([arg, s])

        elif ftype == "select":
            arg = field.get("arg")
            if not arg:
                continue
            default = field.get("default")
            val = raw if raw is not None and raw != "" else default
            if val is None or val == "":
                continue
            cmd.extend([arg, str(val)])

        elif ftype == "checkbox":
            default = bool(field.get("default", False))
            checked = boolish(raw, default)

            if "false_only_arg" in field:
                if not checked:
                    cmd.append(field["false_only_arg"])
            if "true_only_arg" in field:
                if checked:
                    cmd.append(field["true_only_arg"])

        elif ftype == "radio":
            default = field.get("default")
            choice_val = raw if raw not in (None, "") else default
            for ch in field.get("choices") or []:
                if ch.get("value") == choice_val:
                    extra = ch.get("arg")
                    if extra:
                        cmd.append(extra)
                    break

        elif ftype == "number":
            arg = field.get("arg")
            if not arg:
                continue
            default = field.get("default")
            try:
                if raw in (None, ""):
                    val = int(default) if default is not None else None
                else:
                    val = int(raw)
            except (TypeError, ValueError):
                val = int(default) if default is not None else None
            if val is None:
                continue
            nmin = field.get("min")
            nmax = field.get("max")
            if nmin is not None and val < int(nmin):
                val = int(nmin)
            if nmax is not None and val > int(nmax):
                val = int(nmax)
            omit_if_default = field.get("omit_if_default", False)
            if omit_if_default and default is not None and val == int(default):
                continue
            cmd.extend([arg, str(val)])

        else:
            continue

    # Long conversation and dialogue are not independent in run-daily:
    # --long-conversation implies dialogue and conflicts with --no-dialogue.
    if "--long-conversation" in cmd:
        cmd = [a for a in cmd if a != "--no-dialogue"]
        # De-duplicate if options/file drift produced repeated flags.
        seen = set()
        deduped: list[str] = []
        for a in cmd:
            if a in ("--long-conversation", "--dialogue"):
                if a in seen:
                    continue
                seen.add(a)
            deduped.append(a)
        cmd = deduped

    # Journal / Narration / Video tabs merge the picker into `values["date"]`, but the `date` form
    # field is only defined for workflow "full" in options.json—so the field loop skips it here.
    # Without --date, run-daily uses today's calendar month/day, ignoring the picker.
    date_merged = merged.get("date")
    if date_merged is not None:
        date_s = str(date_merged).strip()
        if date_s and "--date" not in cmd:
            cmd.extend(["--date", date_s])

    if wf == "narration" and "--narration-only" not in cmd:
        cmd.append("--narration-only")

    # Journal tab sends workflow "narration" and may set no_focus_topic, but that checkbox is
    # Full-tab-only in options.json, so the field loop never adds this flag for wf "narration".
    want_no_focus = boolish(merged.get("no_focus_topic"), False) or boolish(
        merged.get("no_theme_selector"), False
    )
    if wf == "narration" and want_no_focus:
        if "--no-focus-topic" not in cmd and "--no-theme-selector" not in cmd:
            cmd.append("--no-focus-topic")

    if wf == "narration" and boolish(merged.get("use_week_arc"), False):
        if "--use-week-arc" not in cmd:
            cmd.append("--use-week-arc")
        if boolish(merged.get("refresh_week_arc"), False) and "--refresh-week-arc" not in cmd:
            cmd.append("--refresh-week-arc")

    # B-roll materialize can set this in the client even when options.json predates the field.
    if boolish(merged.get("force_assembly"), False) and "--force-assembly" not in cmd:
        cmd.append("--force-assembly")

    # Week arc decides mode: do not force --no-dialogue / --no-long-conversation when those
    # checkboxes are merely unchecked alongside Use week arc.
    if _week_arc_defers_mode_flags(values):
        cmd = [a for a in cmd if a not in ("--no-dialogue", "--no-long-conversation")]

    # Never pass both --dialogue and --no-dialogue (run-daily exits with an error).
    if _user_explicit_dialogue_opt_out(values):
        cmd = [a for a in cmd if a != "--dialogue"]
        if "--no-dialogue" not in cmd:
            cmd.append("--no-dialogue")

    return cmd
