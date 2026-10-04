"""Resolve effective dialogue / long-conversation mode for a ``run-daily`` run.

Extracted verbatim from ``run-daily.py`` ``main()`` so the decision is one
testable unit. Composes the existing helpers in :mod:`pipeline.week_arc` and
:mod:`pipeline.narration_utils`.

Precedence:

1. **Week arc** — when a week-arc plan is active and the CLI set no mode flag,
   the day plan's ``recommended_mode`` wins.
2. **Manual** — any of ``--dialogue`` / ``--no-dialogue`` / ``--long-conversation``
   / ``--no-long-conversation``: explicit flags win; an unset half is inferred
   from the existing narration JSON.
3. **Neither** — infer both from the existing narration JSON.

``--long-conversation`` always implies dialogue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.narration_utils import (
    infer_long_conversation_effective_from_cli_and_narration,
    narration_json_expects_dialogue_mode,
)
from pipeline.week_arc import cli_specifies_mode, modes_from_day_plan


@dataclass
class ModeDecision:
    dialogue_effective: bool
    long_conversation_effective: bool
    # Populated only when a week-arc day plan selected the mode; run-daily prints it.
    week_arc_recommended_label: str | None = None
    notes: list[str] = field(default_factory=list)


def resolve_run_daily_modes(
    *,
    dialogue_arg: bool,
    no_dialogue_arg: bool,
    long_conversation_arg: bool,
    no_long_conversation_arg: bool,
    narration_path: Path,
    week_arc_active: bool = False,
    week_arc_day_plan: dict | None = None,
) -> ModeDecision:
    """Return the effective modes. ``week_arc_active`` means: week arc is on, a
    plan doc loaded, and we are (re)generating narration this run."""
    manual_mode = cli_specifies_mode(
        dialogue=dialogue_arg,
        no_dialogue=no_dialogue_arg,
        long_conversation=long_conversation_arg,
        no_long_conversation=no_long_conversation_arg,
    )
    apply_week_arc_modes = week_arc_active and not manual_mode
    label: str | None = None

    if apply_week_arc_modes:
        dialogue_effective, long_conversation_effective = modes_from_day_plan(week_arc_day_plan)
        if dialogue_effective or long_conversation_effective:
            label = "long_conversation" if long_conversation_effective else "dialogue"
    elif manual_mode:
        if long_conversation_arg:
            long_conversation_effective = True
        elif no_long_conversation_arg:
            long_conversation_effective = False
        else:
            long_conversation_effective = infer_long_conversation_effective_from_cli_and_narration(
                long_conversation_arg=False,
                no_long_conversation_arg=False,
                narration_path=narration_path,
            )
        if dialogue_arg:
            dialogue_effective = True
        elif no_dialogue_arg:
            dialogue_effective = False
        else:
            dialogue_effective = long_conversation_effective
    else:
        long_conversation_effective = infer_long_conversation_effective_from_cli_and_narration(
            long_conversation_arg=bool(long_conversation_arg),
            no_long_conversation_arg=bool(no_long_conversation_arg),
            narration_path=narration_path,
        )
        dialogue_effective = False
        if narration_path.exists():
            try:
                nar = json.loads(narration_path.read_text(encoding="utf-8"))
                if narration_json_expects_dialogue_mode(nar):
                    dialogue_effective = True
            except (json.JSONDecodeError, OSError):
                pass

    if long_conversation_effective:
        dialogue_effective = True

    return ModeDecision(
        dialogue_effective=dialogue_effective,
        long_conversation_effective=long_conversation_effective,
        week_arc_recommended_label=label,
    )
