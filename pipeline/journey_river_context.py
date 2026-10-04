"""Journal-date → expedition boating / river-travel defaults for videogen prompts.

Milestones align with ``location_data/location-dates.json`` (approximate watershed phase).
Purely deterministic—no LLM—so rerunning video on an existing narration JSON picks up cues
from ``date_id`` without regenerating Phase 2.
"""

from __future__ import annotations

from datetime import date
from typing import Any

# Inclusive-start calendar boundaries (journal calendar, not airing date).
_BOUND_DEPART_MISSOURI = date(1804, 5, 14)
_BOUND_DIVIDE = date(1805, 8, 12)
_BOUND_PACIFIC_COAST = date(1805, 11, 15)
_BOUND_RETURN_START = date(1806, 3, 23)


def parse_journal_date_id(date_id: str) -> date | None:
    """Parse ``YYYYMMDD`` (e.g. journal id ``18040525``); return ``None`` if invalid."""
    s = "".join(c for c in str(date_id).strip() if c.isdigit())
    if len(s) != 8:
        return None
    try:
        y, m, d = int(s[0:4]), int(s[4:6]), int(s[6:8])
        return date(y, m, d)
    except ValueError:
        return None


def _hint_prep() -> str:
    return (
        "Early expedition staging and approach: boating can include arduous **upstream** pulls on Ohio "
        "or Mississippi stretches—do **not** default interior shots to careless **Missouri downstream** "
        "drift unless narration states it. Against current: slow progress and hard crew effort—no sleek "
        "bow cutting a sharp wake."
    )


def _hint_missouri_outbound() -> str:
    return (
        "**Outbound Missouri-system travel:** default heavy **upstream / against-current** work—pole, "
        "tow from shore (cordelle), strenuous paddling at near-full exertion—not lazy **downstream** "
        "drifting unless narration explicitly says so. The bow advances **slowly** against the current—"
        "do **not** show a sleek bow **cutting** a sharp wake as if under easy power. When the clip is "
        "a **keelboat or barge deck** shot, rowers stay **aboard** with blades in the river **beside** "
        "the hull—do not mix cordelle or men walking the bank into that same composition."
    )


def _hint_west_slope_to_ocean() -> str:
    return (
        "**West of the continental divide:** major runnable rivers typically **fall toward** the ocean—"
        "show slope-appropriate **downstream-current** cues for those waters; avoid recycling an "
        "**eastward Missouri downstream** rafting look unless narration places you back on that river."
    )


def _hint_pacific_littoral() -> str:
    return (
        "**Pacific coast / Columbia estuary:** tides, surf, bays, rainforest river mouths—distinct from "
        "interior Missouri boating defaults."
    )


def _hint_homeward() -> str:
    return (
        "**Homeward journey:** once back on the **Missouri main stem** toward St. Louis, favour **travel "
        "with the current** (**downstream** ease, faster drift) where narration matches open-channel "
        "progress; still obey explicit journal beats about halts or contrary maneuvers."
    )


def expedition_route_hint_for_journal_date(cal: date) -> str:
    """Return vendor-sized expedition river-travel instruction, or \"\" for unknown edge cases."""
    if cal < _BOUND_DEPART_MISSOURI:
        return _hint_prep()
    if cal < _BOUND_DIVIDE:
        return _hint_missouri_outbound()
    if cal < _BOUND_PACIFIC_COAST:
        return _hint_west_slope_to_ocean()
    if cal < _BOUND_RETURN_START:
        return _hint_pacific_littoral()
    return _hint_homeward()


def expedition_route_hint_for_date_id(date_id: str) -> str:
    """Public helper: ``date_id`` string → hint, or ``\"\"`` if unparsable."""
    cal = parse_journal_date_id(date_id)
    if cal is None:
        return ""
    return expedition_route_hint_for_journal_date(cal)


def resolved_expedition_route_hint(
    date_id: str,
    episode_metadata: dict[str, Any] | None = None,
) -> str:
    """
    Honor ``episode_metadata.suppress_expedition_route_hint`` when operators need to skip
    calendar defaults (e.g. highly atypical episode mapping).
    """
    if isinstance(episode_metadata, dict) and episode_metadata.get(
        "suppress_expedition_route_hint"
    ):
        return ""
    return expedition_route_hint_for_date_id(date_id)
