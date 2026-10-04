"""
Week-arc planning: bundle consecutive journal dates into a variable-length narrative arc
(the LLM picks the length), LLM plan, daily slice.

Plans live in ``state/week_arcs/week_<start_date_id>.json`` (machine-local).
Config: ``config/week_arc.json`` (``first_anchor_date_id``, ``week_journal_days_min``,
``week_journal_days_max``).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from pipeline.narration_common import clean_json_reply, extract_entry_text

_SCHEMA_VERSION = 1
_VALID_MODES = frozenset({"narration", "dialogue", "long_conversation"})
_DATE_ID_RE = re.compile(r"^\d{8}$")
_JOURNAL_STEM_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAVED_ARC_FILENAME_RE = re.compile(r"^week_\d{8}\.json$")
_MAX_ENTRY_CHARS = 2400

# Maps each week-arc role to the closing_type/energy shape it calls for, so Phase 1 gets one
# coherent pacing signal for the day instead of the role and the global episode-diversity nudges
# (closing_type / narrative_energy skew, see pipeline/recent_episode_diversity.py) pulling apart.
ROLE_PACING_GUIDANCE: dict[str, tuple[str, str]] = {
    "setup": (
        "forward_tension",
        "low-to-moderate `narrative_energy`—establish the week's stakes without spending the payoff early.",
    ),
    "deepen": (
        "forward_tension",
        "moderate `narrative_energy`, building investment in the through-line.",
    ),
    "build": (
        "forward_tension",
        "rising `narrative_energy` toward the week's peak.",
    ),
    "contrast": (
        "forward_tension",
        "energy that sharpens the tension between the week's two threads.",
    ),
    "hold": (
        "forward_tension",
        "sustained tension—do not release it yet; keep `narrative_energy` high without resolving.",
    ),
    "payoff": (
        "reflective_lift",
        "the week's highest `narrative_energy` (4-5) when the journal supports it—this is the dramatic high point.",
    ),
    "release": (
        "reflective_lift",
        "lower `narrative_energy`—a calm comedown after the payoff.",
    ),
}


def expected_closing_type_for_role(role: str | None) -> str | None:
    """The `closing_type` this week-arc role calls for, or None for an unrecognized/missing role."""
    guidance = ROLE_PACING_GUIDANCE.get(str(role or "").strip().lower())
    return guidance[0] if guidance else None


def role_pacing_hint(role: str | None) -> str | None:
    """One-line closing_type/energy expectation for this role, or None when unrecognized."""
    key = str(role or "").strip().lower()
    guidance = ROLE_PACING_GUIDANCE.get(key)
    if not guidance:
        return None
    closing_type, energy_note = guidance
    return (
        f"Closing/energy shape for '{key}': prefer `closing_type`: '{closing_type}'; {energy_note}"
    )


_WEEK_ARC_SYSTEM = """You plan a multi-day narrative arc for Lewis & Clark YouTube Shorts, spanning
consecutive journal days.

The user payload's "candidate_window" gives you a run of consecutive journal days
("date_ids"), plus "min_days" and "max_days". Decide how many of those days actually belong
to one coherent arc, starting from the first date_id in the window -- do not default to using
the whole window. End the arc wherever a payoff/release genuinely lands, even if that's
earlier than the window's end; never end mid-tension, and never stretch a thin week past its
natural close just to fill the window. Your choice must be between min_days and max_days days
(inclusive) and must be a contiguous run starting at the window's first date_id -- do not skip
days or start partway through the window.

Return ONLY a JSON object with this shape:
{
  "through_line": "1-2 sentences: the arc's narrative shape across the days you chose",
  "avoid_this_week": ["short bullets of beats or hooks to NOT repeat within this arc"],
  "days": {
    "<date_id>": {
      "role": "setup|deepen|contrast|build|payoff|release|hold",
      "recommended_mode": "narration|dialogue|long_conversation",
      "speakers": null or ["speaker_a", "speaker_b"],
      "day_focus": "what this episode should emphasize",
      "reason": "why this mode and role fit the journal"
    }
  }
}

Rules:
- Include only the date_ids you chose under "days" -- a contiguous prefix of the candidate
  window, min_days to max_days long. Omit the rest of the window entirely.
- Use "narration" for thin entries, solo observation, or breather days.
- Use "dialogue" when 1-3 cast beats fit (narrator-led); at most ~3 dialogue days per arc.
- Use "long_conversation" for at most 1-2 days with sustained two-speaker journal material (lewis+clark or other roster pairs).
- "speakers" must be two lowercase roster ids when recommended_mode is long_conversation; one or two for dialogue; null for narration.
- Vary roles across the arc; one clear payoff day is ideal.
- Do not invent journal facts; base mode choices on supplied excerpts only.
"""


def _repo_root(repo_root: Path | str | None) -> Path:
    if repo_root is not None:
        return Path(repo_root)
    return Path(__file__).resolve().parent.parent


def load_week_arc_config(repo_root: Path | str | None = None) -> dict[str, Any]:
    path = _repo_root(repo_root) / "config" / "week_arc.json"
    defaults = {
        "first_anchor_date_id": "18040705",
        "week_journal_days_min": 4,
        "week_journal_days_max": 10,
        "plan_model": "gpt-4o",
    }
    if not path.is_file():
        return dict(defaults)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError):
        return dict(defaults)
    out = dict(defaults)
    if isinstance(raw, dict):
        if raw.get("first_anchor_date_id"):
            out["first_anchor_date_id"] = str(raw["first_anchor_date_id"]).strip()
        if raw.get("week_journal_days_min"):
            out["week_journal_days_min"] = int(raw["week_journal_days_min"])
        if raw.get("week_journal_days_max"):
            out["week_journal_days_max"] = int(raw["week_journal_days_max"])
        if raw.get("plan_model"):
            out["plan_model"] = str(raw["plan_model"]).strip()
    return out


def journal_date_to_date_id(journal_date: str) -> str:
    return journal_date.replace("-", "")


def date_id_to_journal_date(date_id: str) -> str:
    return f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:8]}"


def list_journal_date_ids(
    journal_dir: Path | str,
    *,
    from_anchor: str | None = None,
) -> list[str]:
    """Sorted YYYYMMDD date ids for ``*.xml`` stems under journal_dir."""
    root = Path(journal_dir)
    if not root.is_dir():
        return []
    out: list[str] = []
    for p in root.glob("*.xml"):
        stem = p.stem
        if not _JOURNAL_STEM_RE.match(stem):
            continue
        try:
            datetime.strptime(stem, "%Y-%m-%d")
        except ValueError:
            continue
        did = journal_date_to_date_id(stem)
        if from_anchor and did < from_anchor:
            continue
        out.append(did)
    return sorted(set(out))


def week_arc_dir(repo_root: Path | str | None = None) -> Path:
    return _repo_root(repo_root) / "state" / "week_arcs"


def _saved_arc_docs(repo_root: Path | str | None) -> list[dict[str, Any]]:
    """All saved week-arc docs (``week_<start_date_id>.json``), oldest filename first.

    Excludes ``week_arc.example.json`` and anything else that isn't the exact saved-arc
    filename shape. Best-effort; skips unreadable files.
    """
    if repo_root is None:
        return []
    d = week_arc_dir(repo_root)
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("week_*.json")):
        if not _SAVED_ARC_FILENAME_RE.match(p.name):
            continue
        doc = load_week_arc(p)
        if doc and isinstance(doc.get("journal_date_ids"), list):
            out.append(doc)
    return out


def find_saved_arc_for_date(date_id: str, repo_root: Path | str | None) -> dict[str, Any] | None:
    """The saved week-arc doc whose journal_date_ids already include date_id, if any."""
    for doc in _saved_arc_docs(repo_root):
        if date_id in (doc.get("journal_date_ids") or []):
            return doc
    return None


def list_saved_week_arcs(repo_root: Path | str | None) -> list[dict[str, Any]]:
    """All saved week-arc docs, newest (latest ``week_start_date_id``) first."""
    return sorted(
        _saved_arc_docs(repo_root),
        key=lambda d: str(d.get("week_start_date_id") or ""),
        reverse=True,
    )


def _next_planning_frontier(dates_all: list[str], repo_root: Path | str | None) -> str | None:
    """The date_id that should start the next not-yet-planned arc, or None if unavailable."""
    if not dates_all:
        return None
    saved_ends = [
        str(doc.get("week_end_date_id"))
        for doc in _saved_arc_docs(repo_root)
        if doc.get("week_end_date_id")
    ]
    if not saved_ends:
        return dates_all[0]
    latest_end = max(saved_ends)
    if latest_end not in dates_all:
        return None
    idx = dates_all.index(latest_end)
    return dates_all[idx + 1] if idx + 1 < len(dates_all) else None


def week_journal_dates_for(
    date_id: str,
    journal_dir: Path | str,
    *,
    repo_root: Path | str | None = None,
    config: dict[str, Any] | None = None,
) -> list[str]:
    """The candidate journal date_ids for this date's arc.

    If ``date_id`` already belongs to a saved arc, returns a fresh ``week_journal_days_max``-day
    window starting at that arc's original start date (so a refresh can re-choose the length).
    Otherwise ``date_id`` must be the next date awaiting a plan (right after the latest saved
    arc's end, or the configured anchor); returns a candidate window from there for the planner
    to pick an actual arc length from -- see ``_WEEK_ARC_SYSTEM``. Returns ``[]`` when
    ``date_id`` is before the anchor, missing from the journal, or not yet reachable in
    planning order.
    """
    cfg = config or {}
    anchor = str(cfg.get("first_anchor_date_id") or "18040705").strip()
    max_days = max(1, int(cfg.get("week_journal_days_max") or 10))

    dates_all = list_journal_date_ids(journal_dir, from_anchor=anchor)
    if date_id not in dates_all:
        return []

    existing = find_saved_arc_for_date(date_id, repo_root)
    if existing:
        start = str(existing.get("week_start_date_id"))
    else:
        start = _next_planning_frontier(dates_all, repo_root)
        if start != date_id:
            return []

    if start not in dates_all:
        return []
    idx = dates_all.index(start)
    window = dates_all[idx : idx + max_days]
    return window if date_id in window else []


def week_start_date_id(week_dates: list[str]) -> str:
    if not week_dates:
        raise ValueError("week_dates required")
    return week_dates[0]


def week_arc_path(repo_root: Path | str | None, week_start: str) -> Path:
    return week_arc_dir(repo_root) / f"week_{week_start}.json"


def load_week_arc(path: Path | str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_week_arc(path: Path | str, data: dict[str, Any]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def normalize_recommended_mode(raw: Any) -> str:
    mode = str(raw or "narration").strip().lower().replace("-", "_")
    if mode in ("long", "longconversation"):
        mode = "long_conversation"
    if mode not in _VALID_MODES:
        return "narration"
    return mode


def get_day_plan(week_arc: dict[str, Any], date_id: str) -> dict[str, Any] | None:
    days = week_arc.get("days")
    if not isinstance(days, dict):
        return None
    row = days.get(date_id)
    return row if isinstance(row, dict) else None


def modes_from_day_plan(
    day_plan: dict[str, Any] | None,
    *,
    focus_topic: str | None = None,
) -> tuple[bool, bool]:
    """Return (dialogue_effective, long_conversation_effective). Focus topic forces narration."""
    if focus_topic:
        return False, False
    if not day_plan:
        return False, False
    mode = normalize_recommended_mode(day_plan.get("recommended_mode"))
    if mode == "long_conversation":
        return True, True
    if mode == "dialogue":
        return True, False
    return False, False


def requires_talking_head(
    day_plan: dict[str, Any] | None,
    *,
    focus_topic: str | None = None,
) -> bool:
    """True when this week-arc day plan calls for at least one on-camera talking-head segment.

    Mirrors :func:`modes_from_day_plan`'s eligibility (``dialogue``/``long_conversation``
    days)—those are the modes where Phase 1 may emit ``visual_mode: talking_head`` segments,
    so a planned dialogue/long_conversation day should not silently render as all-``b_roll``.
    """
    dialogue_effective, long_conversation_effective = modes_from_day_plan(
        day_plan, focus_topic=focus_topic
    )
    return dialogue_effective or long_conversation_effective


def cli_specifies_mode(
    *,
    dialogue: bool = False,
    no_dialogue: bool = False,
    long_conversation: bool = False,
    no_long_conversation: bool = False,
) -> bool:
    return bool(dialogue or no_dialogue or long_conversation or no_long_conversation)


def build_week_arc_prompt_block(week_arc: dict[str, Any], date_id: str) -> str:
    day = get_day_plan(week_arc, date_id)
    if not day:
        return ""
    lines = ["WEEK ARC (editorial plan for this multi-day narrative bundle):"]
    tl = str(week_arc.get("through_line") or "").strip()
    if tl:
        lines.append(f"- Through-line: {tl}")
    avoid = week_arc.get("avoid_this_week")
    if isinstance(avoid, list) and avoid:
        bullets = "; ".join(str(a).strip() for a in avoid if str(a).strip())
        if bullets:
            lines.append(f"- Avoid this week: {bullets}")
    role = str(day.get("role") or "").strip()
    focus = str(day.get("day_focus") or "").strip()
    reason = str(day.get("reason") or "").strip()
    mode = normalize_recommended_mode(day.get("recommended_mode"))
    if role:
        lines.append(f"- Today's role in the week: {role}")
    pacing = role_pacing_hint(role)
    if pacing:
        lines.append(f"- {pacing}")
    if focus:
        lines.append(f"- Today's focus: {focus}")
    if mode != "narration":
        speakers = day.get("speakers")
        sp = ""
        if isinstance(speakers, list) and speakers:
            sp = f" (speakers: {', '.join(str(s) for s in speakers)})"
        lines.append(f"- Planned mode for this day: {mode}{sp}")
    if reason:
        lines.append(f"- Planner note: {reason}")
    lines.append(
        "Honor this week's shape while staying faithful to today's journal; do not contradict source facts."
    )
    return "\n".join(lines)


def week_arc_ref_for_merge(week_arc: dict[str, Any], date_id: str) -> dict[str, Any] | None:
    day = get_day_plan(week_arc, date_id)
    if not day:
        return None
    return {
        "week_id": week_arc.get("week_id"),
        "week_start_date_id": week_arc.get("week_start_date_id"),
        "week_end_date_id": week_arc.get("week_end_date_id"),
        "role": day.get("role"),
        "recommended_mode": normalize_recommended_mode(day.get("recommended_mode")),
        "day_focus": day.get("day_focus"),
    }


def _journal_excerpt(journal_dir: Path, date_id: str, max_chars: int = _MAX_ENTRY_CHARS) -> str:
    xml_path = journal_dir / f"{date_id_to_journal_date(date_id)}.xml"
    if not xml_path.is_file():
        return "(journal XML missing)"
    try:
        text = extract_entry_text(xml_path)
    except (OSError, ValueError):
        return "(could not read journal)"
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text or "(empty entry)"
    return text[: max_chars - 3].rstrip() + "..."


def _recent_mode_digest(narrations_dir: Path, before_date_id: str, limit: int = 8) -> str:
    if not narrations_dir.is_dir():
        return ""
    rows: list[str] = []
    for p in sorted(narrations_dir.glob("narration*.json"), reverse=True):
        m = re.match(r"^narration(\d{8})\.json$", p.name)
        if not m:
            continue
        did = m.group(1)
        if did >= before_date_id:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, JSONDecodeError):
            continue
        dm = "dialogue" if data.get("dialogue_mode") else "narration"
        if data.get("long_conversation_mode"):
            dm = "long_conversation"
        title = str(data.get("title") or "")[:48]
        rows.append(f"- {did}: mode={dm} title≈{title or '—'}")
        if len(rows) >= limit:
            break
    if not rows:
        return ""
    return "Recent published modes (newest first):\n" + "\n".join(rows)


def _validate_plan_payload(
    payload: dict[str, Any],
    candidate_dates: list[str],
    *,
    min_days: int,
    max_days: int,
) -> None:
    days = payload.get("days")
    if not isinstance(days, dict):
        raise ValueError("days object required")
    if not days:
        raise ValueError("days must include at least one date")
    extra = [d for d in days if d not in candidate_dates]
    if extra:
        raise ValueError(f"plan has unexpected days: {extra}")
    chosen = [d for d in candidate_dates if d in days]
    if chosen != candidate_dates[: len(chosen)]:
        raise ValueError("days must be a contiguous prefix of the candidate window")
    if not (min_days <= len(chosen) <= max_days):
        raise ValueError(f"arc length {len(chosen)} outside allowed range [{min_days}, {max_days}]")
    lc_count = 0
    for did in chosen:
        row = days.get(did)
        if not isinstance(row, dict):
            raise ValueError(f"day {did} must be an object")
        mode = normalize_recommended_mode(row.get("recommended_mode"))
        if mode == "long_conversation":
            lc_count += 1
        speakers = row.get("speakers")
        if mode == "long_conversation":
            if not isinstance(speakers, list) or len(speakers) != 2:
                raise ValueError(f"day {did}: long_conversation needs two speakers")
        elif mode == "dialogue":
            if speakers is not None and not isinstance(speakers, list):
                raise ValueError(f"day {did}: speakers must be a list or null")
        elif speakers not in (None, []):
            pass
    if lc_count > 2:
        raise ValueError("at most two long_conversation days per week")


def build_week_plan_user_prompt(
    week_dates: list[str],
    journal_dir: Path,
    narrations_dir: Path,
    *,
    before_date_id: str,
    min_days: int,
    max_days: int,
) -> str:
    journals = []
    for did in week_dates:
        journals.append(
            {
                "date_id": did,
                "journal_date": date_id_to_journal_date(did),
                "excerpt": _journal_excerpt(journal_dir, did),
            }
        )
    payload: dict[str, Any] = {
        "candidate_window": {
            "date_ids": week_dates,
            "min_days": min_days,
            "max_days": max_days,
            "note": (
                "Choose a contiguous prefix of date_ids, min_days to max_days long, that "
                "forms one coherent arc. Do not just use the whole window."
            ),
        },
        "journals": journals,
        "recent_modes": _recent_mode_digest(narrations_dir, before_date_id),
        "roster_speaker_ids": [
            "lewis",
            "clark",
            "ordway",
            "york",
            "drouillard",
            "gass",
            "colter",
            "potts",
            "collins",
            "shannon",
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def run_week_plan_llm(
    week_dates: list[str],
    journal_dir: Path,
    narrations_dir: Path,
    *,
    model: str = "gpt-4o",
    client: Any = None,
    min_days: int = 4,
    max_days: int = 10,
) -> dict[str, Any]:
    from openai import OpenAI

    from pipeline_logging import log_api_call_with_bodies

    before = week_dates[0]
    user = build_week_plan_user_prompt(
        week_dates,
        journal_dir,
        narrations_dir,
        before_date_id=before,
        min_days=min_days,
        max_days=max_days,
    )
    messages = [
        {"role": "system", "content": _WEEK_ARC_SYSTEM},
        {"role": "user", "content": user},
    ]
    oai = client or OpenAI()
    response = oai.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.5,
        max_tokens=4096,
    )
    raw = response.choices[0].message.content or ""
    log_api_call_with_bodies(
        "openai",
        "chat.completions.create",
        request_body=messages,
        response_body=raw,
        model=model,
        extra={"message_count": 2, "purpose": "week_arc_plan"},
    )
    cleaned = clean_json_reply(raw)
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("week arc LLM response must be a JSON object")
    _validate_plan_payload(payload, week_dates, min_days=min_days, max_days=max_days)
    return payload


def assemble_week_arc_document(
    plan_payload: dict[str, Any],
    week_dates: list[str],
    *,
    model: str,
) -> dict[str, Any]:
    start = week_start_date_id(week_dates)
    end = week_dates[-1]
    days_in = plan_payload.get("days")
    if not isinstance(days_in, dict):
        raise ValueError("days required")
    days_out: dict[str, Any] = {}
    for did in week_dates:
        row = dict(days_in.get(did) or {})
        row["recommended_mode"] = normalize_recommended_mode(row.get("recommended_mode"))
        days_out[did] = row
    return {
        "schema_version": _SCHEMA_VERSION,
        "week_id": start,
        "week_start_date_id": start,
        "week_end_date_id": end,
        "journal_date_ids": list(week_dates),
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model,
        "through_line": str(plan_payload.get("through_line") or "").strip(),
        "avoid_this_week": [
            str(x).strip() for x in (plan_payload.get("avoid_this_week") or []) if str(x).strip()
        ],
        "days": days_out,
    }


def ensure_week_arc(
    date_id: str,
    *,
    repo_root: Path | str | None = None,
    journal_dir: Path | str | None = None,
    narrations_dir: Path | str | None = None,
    model: str | None = None,
    refresh: bool = False,
    client: Any = None,
    dry_run_plan: bool = False,
) -> dict[str, Any] | None:
    """
    Load the arc for ``date_id``'s bundle, creating it when missing (unless dry_run_plan).

    The arc's actual length (within the configured min/max) is chosen by the LLM, not fixed.
    Returns None when the date is before the anchor, has no journal entry, or isn't yet
    reachable in planning order (see :func:`week_journal_dates_for`).
    """
    if not _DATE_ID_RE.match(date_id):
        raise ValueError(f"invalid date_id: {date_id!r}")
    root = _repo_root(repo_root)
    cfg = load_week_arc_config(root)
    jdir = Path(journal_dir) if journal_dir else root / "journal-entries"
    ndir = Path(narrations_dir) if narrations_dir else root / "narrations"
    candidate = week_journal_dates_for(date_id, jdir, repo_root=root, config=cfg)
    if not candidate:
        return None
    start = week_start_date_id(candidate)
    path = week_arc_path(root, start)
    if not refresh:
        existing = load_week_arc(path)
        if existing and isinstance(existing.get("days"), dict):
            return existing
    min_days = max(1, int(cfg.get("week_journal_days_min") or 4))
    max_days = max(min_days, int(cfg.get("week_journal_days_max") or 10))
    min_days = min(min_days, len(candidate))
    plan_model = (model or cfg.get("plan_model") or "gpt-4o").strip()
    if dry_run_plan:
        return assemble_week_arc_document(
            {
                "through_line": "(dry-run placeholder)",
                "avoid_this_week": [],
                "days": {
                    did: {
                        "role": "setup",
                        "recommended_mode": "narration",
                        "speakers": None,
                        "day_focus": "placeholder",
                        "reason": "dry-run",
                    }
                    for did in candidate
                },
            },
            candidate,
            model=plan_model,
        )
    plan_payload = run_week_plan_llm(
        candidate,
        jdir,
        ndir,
        model=plan_model,
        client=client,
        min_days=min_days,
        max_days=max_days,
    )
    chosen = sorted(plan_payload["days"].keys())
    doc = assemble_week_arc_document(plan_payload, chosen, model=plan_model)
    save_week_arc(week_arc_path(root, chosen[0]), doc)
    return doc
