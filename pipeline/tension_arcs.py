"""Expedition tension milestones from config/narration_tension-arcs.json for Phase 1 prompts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from json import JSONDecodeError
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
TENSION_ARCS_PATH = _REPO_ROOT / "config" / "narration_tension-arcs.json"


def resolve_tension_context(date_id: str) -> tuple[str | None, float]:
    """Return (tension_prompt, tension_weight_0_to_1) for this date.

    - Uses narration_tension-arcs.json expedition_milestones.
    - For each arc, derives a piecewise-linear weight based on distance to end_date:
      * 0 before the window
      * ramp from ~0.25 up to 1.0 between start_date and end_date
      * linear decay from 1.0 back to 0 over 4 days after end_date
    - Returns the prompt from the strongest arc (if any) plus the max weight across arcs.
    """
    if not TENSION_ARCS_PATH.exists():
        return (None, 0.0)
    try:
        data = json.loads(TENSION_ARCS_PATH.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError):
        return (None, 0.0)

    milestones = data.get("expedition_milestones") or []
    try:
        current = datetime.strptime(date_id, "%Y%m%d").date()
    except ValueError:
        return (None, 0.0)

    best_prompt: str | None = None
    best_weight: float = 0.0

    for arc in milestones:
        if not isinstance(arc, dict):
            continue
        start_s = arc.get("start_date")
        end_s = arc.get("end_date")
        if not start_s or not end_s:
            continue
        try:
            start = datetime.strptime(start_s, "%Y-%m-%d").date()
            end = datetime.strptime(end_s, "%Y-%m-%d").date()
        except ValueError:
            continue

        weight = 0.0
        prompt: str | None = None

        if current < start:
            weight = 0.0
        elif start <= current <= end:
            total_days = max(1, (end - start).days)
            progressed = (current - start).days
            frac = min(1.0, max(0.0, progressed / total_days))
            weight = 0.25 + 0.75 * frac
            if current == end:
                prompt = arc.get("arrival_release_prompt")
            else:
                prompt = arc.get("approach_prompt")
        elif end < current <= end + timedelta(days=4):
            days_after = (current - end).days
            decay_frac = min(1.0, max(0.0, days_after / 4.0))
            weight = 1.0 * (1.0 - decay_frac)
            prompt = arc.get("post_event_shift_prompt")
        else:
            weight = 0.0

        if not prompt or weight <= 0.0:
            continue

        if weight > best_weight:
            best_weight = float(weight)
            best_prompt = str(prompt)

    return best_prompt, best_weight


def tension_weight_to_urgency(weight: float) -> str:
    """Map tension weight (0–1) to a discrete urgency level for the prompt."""
    if weight >= 0.7:
        return "high"
    if weight >= 0.3:
        return "moderate"
    return "low"


def urgency_instruction(urgency: str) -> str:
    """Single-line instruction for the given urgency level."""
    if urgency == "high":
        return (
            "Increase urgency in phrasing and pacing (shorter sentences, more momentum); "
            "use forward-looking language about immediate consequences that occur in this entry; "
            "do not invent or foreshadow specific future events."
        )
    if urgency == "moderate":
        return (
            "Add subtle cues of pressure or unease; keep tone anchored in this entry’s events. "
            "Do not invent or foreshadow specific future events."
        )
    return "Keep the expedition-arc influence very light; only faint hints of the larger context."


def load_tension_prompt_for_date(date_id: str) -> str | None:
    """Resolve a single tension prompt for this journal date from expedition milestones."""
    prompt, _weight = resolve_tension_context(date_id)
    return prompt
