"""
Deterministic digest of recent merged narration JSON for Phase 1 variety nudges.

Reads prior ``narrations/narration<date_id>.json`` files (newest before ``current_date_id``),
summarizes a few stable fields, and emits a short user-prompt block. Does not call an LLM.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pipeline.prompt_pack_paths import packs_root

_RE_FILE = re.compile(r"^narration(\d{8})\.json$")

# Merged narration: Seaman is never a dialogue speaker; look for VO, visuals, and portrait anchors.
_SEAMAN_NAME_PATTERNS = (
    r"\bseaman\b",
    r"\blewis['\u2019]s\s+dog\b",
    r"\blewis\s+and\s+seaman\b",
    r"\bcaptain['\u2019]s\s+dog\b",
    r"\bmeriwether['\u2019]s\s+dog\b",
)

# Low-key corps presence in b_roll video_prompt (no named star beat required).
_SEAMAN_AMBIENT_VIDEO_PATTERNS = (
    r"\bblack\s+(?:newfoundland\s+)?dog\b",
    r"\blarge\s+black\s+dog\b",
    r"\bnewfoundland\b.*\b(?:among|with|beside)\s+the\s+(?:men|corps|party)\b",
    r"\bdog\b.*\b(?:among|with|beside)\s+the\s+(?:men|corps|party)\b",
    r"\b(?:sleeping|asleep|resting|guarding|watching)\s+(?:\w+\s+){0,4}dog\b",
    r"\bdog\b.*\b(?:by the fire|near the tent|at camp|pitching tents)\b",
    r"\bcamp\s+dog\b",
    r"\bexpedition\s+dog\b",
)

# Phase 1 diversity hints (appended to user prompt and shown in Pipeline UI).
SEAMAN_STAR_DIVERSITY_HINT = (
    "Seaman (Lewis's Newfoundland) has had **no star beat** in the last 3 on-disk episodes—when "
    "today's journal allows it, give him a **headline moment**: narrator may name **Seaman, the "
    "Newfoundland dog** with a matching **video_prompt** that makes him the attraction—**perched at "
    "the prow**, **chasing squirrels**, **nosing into expedition provisions**, or otherwise **getting "
    "into good-natured trouble**. He does not speak dialogue and travels with the party."
)

SEAMAN_AMBIENT_DIVERSITY_HINT = (
    "Seaman presence has been light in recent episodes (aim for the dog in about three of every five when "
    "the journal allows): one **b_roll** `video_prompt` with a **large black Newfoundland among the men** at "
    "tents, by the fire, or guarding—background only. Describe in text; "
    "`reference_character_id`: `seaman` only when he is the shot's focus."
)

# Segment 1 / last segment bookends (narration + video_prompt on merged JSON).
_OPEN_WAKE_MORNING_PATTERNS = (
    r"\bwake\b",
    r"\bwaking\b",
    r"\bawoke\b",
    r"\bdawn\b",
    r"\bdaybreak\b",
    r"\bfirst light\b",
    r"\bmorning broke\b",
    r"\bmorning after\b",
    r"\bmorning of\b",
    r"\bgreeted\b.{0,40}\bmorning\b",
    r"\bfair morning\b",
    r"\bpleasant morning\b",
    r"\bsets?\s+out\s+early\b",
    r"\bembarked at dawn\b",
    r"\bat dawn\b",
    r"\bthe morning\b",
    r"\bearly morning\b",
    r"\bsunrise\b",
    r"\bsun rose\b",
    r"\bsun rises\b",
)

_CLOSE_EVENING_CAMP_PATTERNS = (
    r"\bset(?:ting)? up camp\b",
    r"\bmaking camp\b",
    r"\bmade camp\b",
    r"\bmake camp\b",
    r"\bencamped\b",
    r"\bcamped near\b",
    r"\bcamped at\b",
    r"\bcamping spot\b",
    r"\bat the camp\b",
    r"\bthe camp\b.{0,35}\b(?:by|near|along|beside|at)\b",
    r"\b(?:gather|gathered|sit|sitting)\b.{0,40}\b(?:at the camp|around the camp|by the camp)\b",
    r"\bsettle\b.{0,30}\bcamp\b",
    r"\bcamp\b.{0,30}\b(?:settled|settles|quiet|still|fire|tent|dusk|night|alive)\b",
    r"\b(?:quiet|still|night)\b.{0,35}\bcamp\b",
    r"\bevening camp\b",
    r"\bnight fell\b",
    r"\bday concluded\b.{0,40}\bcamp\b",
    r"\breflect\b.{0,50}\bcamp\b",
    r"\bpitch(?:ing)?\s+tents?\b",
    r"\btents?\b.{0,40}\b(?:dusk|night|evening|silhouett|darken)\b",
    r"\bsilhouett.{0,40}\b(?:tent|camp|fire)\b",
    r"\bby the (?:camp )?fire\b",
    r"\baround (?:a |the )?(?:small )?(?:camp )?fire\b",
    r"\bcampfire(?:s)?\b",
    r"\bfire(?:light| crackl)",
    r"\bcrackling fire\b",
    r"\bsmall fire\b",
    r"\b(?:soft )?evening\b.{0,50}\bcamp\b",
    r"\bsunset\b.{0,50}\b(?:camp|campfire|fire)\b",
    r"\b(?:sun sets|sunset)\b.{0,60}\b(?:camp|campfire|fire)\b",
)

OPEN_BOOKEND_DIVERSITY_HINT = (
    "Recent segment-1 hooks skew **dawn / morning wake / sets-out travel**—when the journal allows, open on "
    "the day's **stakes or main event** (trade, measure, hunt, council, hazard) or **in medias res**; put "
    "wake-and-breakfast routine in a **middle** segment if the source includes it."
)

CLOSE_BOOKEND_DIVERSITY_HINT = (
    "Recent final-segment endings skew **evening camp / tents / fireside settle**—when the source allows, "
    "close on **meaning, forward tension, or the day's central beat** (river wide shot, journal measure, "
    "horizon ahead, council echo, hazard receding) **without** another make-camp or campfire geometry; "
    "reserve camp setup for **middle** segments unless the journal ends at a new campsite."
)

# Merged narration video_prompt: carrying/hauling game to camp (Phase 2 haul default).
_HUNT_HAUL_VIDEO_PATTERNS = (
    r"\bhaul(?:ing|s|ed)?\b.{0,48}\b(?:deer|elk|game|venison|carcass|animal)\b",
    r"\b(?:deer|elk|game|venison)\b.{0,48}\bhaul",
    r"\bcarry(?:ing|ies)?\b.{0,48}\b(?:deer|elk|game|venison|carcass)\b",
    r"\b(?:deer|elk)\b.{0,48}\b(?:over\s+(?:his|her|the)\s+shoulder|across\s+(?:his|her|the)\s+shoulder)\b",
    r"\bdragging\b.{0,40}\b(?:deer|elk|game)\b",
    r"\bwhole\s+body\s+haul\b",
    r"\b(?:deer|elk)\b.{0,32}\b(?:slung|dragged)\b",
    r"\b(?:dead|fallen|downed)\s+(?:deer|elk)\b",
    r"\bcarcass\b",
    r"\breturns?\s+from\b.{0,48}\b(?:deer|elk|game|venison)\b",
)

HUNT_HAUL_DIVERSITY_HINT = (
    "Recent hunt/game **video_prompt** beats show **men carrying or hauling dead deer/elk**—do **not** "
    "illustrate kills that way. Use **active hunt** (stalking, sign-reading, loading a musket), **aim at "
    "distant live deer** (or through a spyglass), **cooking venison at camp**, or an **opaque sack/bundle** "
    "instead; never deer on shoulders or dragged carcasses."
)

# Merged narration narration/video_prompt: curated overused stock phrases/imagery (lexical sameness,
# not a structural repeat). Expand this list as more episodes surface repeated phrasing.
_STOCK_PHRASE_PATTERNS = (
    ("gentle breeze", r"\bgentle\s+(?:\w+\s+)?breeze\b"),
    ("sandbar", r"\bsand\s?bars?\b"),
    ("riverbank", r"\briverbanks?\b"),
    ("prairie stretch", r"\bprairie\s+stretch(?:es)?\b"),
)

# closing_type has exactly two values in the pack contract; nudge toward whichever is scarce.
_CLOSING_TYPE_OTHER = {
    "reflective_lift": "forward_tension",
    "forward_tension": "reflective_lift",
}


def _prior_merged_paths(
    narrations_dir: Path, current_date_id: str | None, limit: int
) -> list[tuple[str, Path]]:
    narrations_dir = Path(narrations_dir)
    if not narrations_dir.is_dir() or limit <= 0:
        return []
    rows: list[tuple[str, Path]] = []
    for p in narrations_dir.iterdir():
        if not p.is_file():
            continue
        m = _RE_FILE.match(p.name)
        if not m:
            continue
        did = m.group(1)
        if current_date_id and did >= current_date_id:
            continue
        rows.append((did, p))
    rows.sort(key=lambda t: t[0], reverse=True)
    return rows[:limit]


def _collect_cast(script: list[Any]) -> set[str]:
    out: set[str] = set()
    for seg in script:
        if not isinstance(seg, dict):
            continue
        rows = seg.get("dialogue")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("speaker_id") or "").strip().lower()
            if sid and sid != "narrator":
                out.add(sid)
    return out


def _gather_json_strings(obj: Any, out: list[str]) -> None:
    if isinstance(obj, str):
        s = obj.strip()
        if s:
            out.append(s)
    elif isinstance(obj, dict):
        for v in obj.values():
            _gather_json_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _gather_json_strings(v, out)


def _text_matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    if not low:
        return False
    return any(re.search(pat, low) for pat in patterns)


def _segment_seaman_star(seg: dict[str, Any]) -> bool:
    """Named Seaman beat: VO names him and/or he is the visual focal subject."""
    ref = str(seg.get("reference_character_id") or "").strip().lower()
    if ref in ("seaman", "lewis_seaman"):
        return True
    if _text_matches_any(str(seg.get("narration") or ""), _SEAMAN_NAME_PATTERNS):
        return True
    if _text_matches_any(str(seg.get("video_prompt") or ""), _SEAMAN_NAME_PATTERNS):
        return True
    rows = seg.get("dialogue")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _text_matches_any(str(row.get("text") or ""), _SEAMAN_NAME_PATTERNS):
                return True
    return False


def _segment_seaman_ambient(seg: dict[str, Any]) -> bool:
    """Low-key dog in b_roll video_prompt among the corps (no headline star beat)."""
    if _segment_seaman_star(seg):
        return False
    return _text_matches_any(str(seg.get("video_prompt") or ""), _SEAMAN_AMBIENT_VIDEO_PATTERNS)


def _seaman_presence(data: dict[str, Any]) -> tuple[bool, bool, bool]:
    """Return (any, star_beat, ambient_only) for merged narration."""
    star = False
    ambient = False
    script = data.get("narration_script")
    if isinstance(script, list):
        for seg in script:
            if not isinstance(seg, dict):
                continue
            if _segment_seaman_star(seg):
                star = True
            elif _segment_seaman_ambient(seg):
                ambient = True
    if not star:
        blob_parts: list[str] = []
        t = data.get("title")
        if isinstance(t, str):
            blob_parts.append(t)
        for key in ("narrative_spine", "scene_spine"):
            _gather_json_strings(data.get(key), blob_parts)
        if _text_matches_any(" ".join(blob_parts), _SEAMAN_NAME_PATTERNS):
            star = True
    return (star or ambient, star, ambient)


def _seaman_referenced(data: dict[str, Any]) -> bool:
    """True when merged narration includes a star and/or ambient Seaman visual."""
    return _seaman_presence(data)[0]


def _min_seaman_episodes_in_window(n: int) -> int:
    """Minimum Seaman presence (star or ambient) in the lookback window (3 of 5 when n=5)."""
    if n <= 0:
        return 0
    return max(1, min(n, (3 * n + 4) // 5))


def _seaman_digest_label(star: bool, ambient: bool) -> str:
    if star and ambient:
        return "star+ambient"
    if star:
        return "star"
    if ambient:
        return "ambient"
    return "no"


def _segment_narration_video_blob(seg: dict[str, Any]) -> str:
    return " ".join(str(seg.get(k) or "") for k in ("narration", "video_prompt", "stage_direction"))


def _script_edge_segments(script: list[Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not script:
        return None, None
    first = last = None
    for seg in script:
        if isinstance(seg, dict):
            first = seg
            break
    for seg in reversed(script):
        if isinstance(seg, dict):
            last = seg
            break
    return first, last


def _episode_open_wake_morning(script: list[Any]) -> bool:
    first, _ = _script_edge_segments(script)
    if not first:
        return False
    return _text_matches_any(_segment_narration_video_blob(first), _OPEN_WAKE_MORNING_PATTERNS)


def _episode_close_evening_camp(script: list[Any]) -> bool:
    _, last = _script_edge_segments(script)
    if not last:
        return False
    return _text_matches_any(_segment_narration_video_blob(last), _CLOSE_EVENING_CAMP_PATTERNS)


def _segment_hunt_haul_video(seg: dict[str, Any]) -> bool:
    vp = str(seg.get("video_prompt") or "")
    if not vp:
        return False
    return _text_matches_any(vp, _HUNT_HAUL_VIDEO_PATTERNS)


def _episode_hunt_haul_heavy(script: list[Any]) -> bool:
    """True when merged episode has multiple haul/carry game video_prompt beats."""
    haul_n = 0
    for seg in script:
        if not isinstance(seg, dict):
            continue
        if _segment_hunt_haul_video(seg):
            haul_n += 1
    return haul_n >= 2


def _episode_stock_phrase_hits(script: list[Any]) -> set[str]:
    """Curated stock-phrase labels found anywhere in this episode's narration/video_prompt text."""
    hits: set[str] = set()
    for seg in script:
        if not isinstance(seg, dict):
            continue
        blob = _segment_narration_video_blob(seg)
        for label, pat in _STOCK_PHRASE_PATTERNS:
            if label not in hits and _text_matches_any(blob, (pat,)):
                hits.add(label)
    return hits


def _talking_head_count(script: list[Any]) -> int:
    n = 0
    for seg in script:
        if not isinstance(seg, dict):
            continue
        vm = str(seg.get("visual_mode") or "b_roll").strip().lower()
        if vm == "talking_head":
            n += 1
    return n


def _snapshot_from_narration(date_id: str, data: dict[str, Any]) -> dict[str, Any]:
    from pipeline.conversation_dyad import extract_conversation_dyad, format_dyad_label

    em = data.get("episode_metadata") if isinstance(data.get("episode_metadata"), dict) else {}
    lens = str(em.get("primary_lens") or "").strip()
    tone = str(data.get("tone_register") or "").strip()
    loc = str(em.get("location_summary") or "").strip()
    closing_type = str(data.get("closing_type") or "").strip()
    energy_raw = em.get("narrative_energy")
    narrative_energy = energy_raw if isinstance(energy_raw, (int, float)) else None
    script = data.get("narration_script")
    if not isinstance(script, list):
        script = []
    any_sm, star_sm, ambient_sm = _seaman_presence(data)
    open_wake = _episode_open_wake_morning(script)
    close_camp = _episode_close_evening_camp(script)
    hunt_haul_heavy = _episode_hunt_haul_heavy(script)
    stock_phrase_hits = _episode_stock_phrase_hits(script)
    lc_mode = bool(data.get("long_conversation_mode"))
    dyad = extract_conversation_dyad(data)
    return {
        "date_id": date_id,
        "title": str(data.get("title") or "").strip(),
        "primary_lens": lens,
        "tone_register": tone,
        "location_summary": loc,
        "closing_type": closing_type,
        "narrative_energy": narrative_energy,
        "talking_head_segments": _talking_head_count(script),
        "cast": _collect_cast(script),
        "dialogue_mode": bool(data.get("dialogue_mode")),
        "long_conversation_mode": lc_mode,
        "conversation_dyad": format_dyad_label(dyad) if dyad else "",
        "seaman_referenced": any_sm,
        "seaman_star": star_sm,
        "seaman_ambient": ambient_sm,
        "seaman_label": _seaman_digest_label(star_sm, ambient_sm),
        "open_wake_morning": open_wake,
        "close_evening_camp": close_camp,
        "hunt_haul_heavy": hunt_haul_heavy,
        "stock_phrase_hits": stock_phrase_hits,
    }


def _format_line(meta: dict[str, Any]) -> str:
    loc = (meta.get("location_summary") or "").strip()
    if len(loc) > 72:
        loc = loc[:69] + "..."
    if not loc:
        loc = "—"
    lens = meta.get("primary_lens") or "—"
    tone = meta.get("tone_register") or "—"
    th = int(meta.get("talking_head_segments") or 0)
    cast = meta.get("cast") or set()
    cast_s = ",".join(sorted(cast)) if cast else "(none)"
    dm = "yes" if meta.get("dialogue_mode") else "no"
    lc = "yes" if meta.get("long_conversation_mode") else "no"
    dyad = meta.get("conversation_dyad") or "—"
    did = meta.get("date_id") or "?"
    title = (meta.get("title") or "").strip()
    if len(title) > 40:
        title = title[:37] + "..."
    if not title:
        title = "—"
    sm = meta.get("seaman_label") or ("no" if not meta.get("seaman_referenced") else "yes")
    op = "wake" if meta.get("open_wake_morning") else "other"
    cl = "camp" if meta.get("close_evening_camp") else "other"
    closing_type = meta.get("closing_type") or "—"
    energy = meta.get("narrative_energy")
    energy_s = str(energy) if energy is not None else "—"
    return (
        f"- {did}: title≈{title} | primary_lens={lens} | tone={tone} | loc≈{loc} | talking_head_segments={th} | "
        f"dialogue_mode={dm} | long_conversation={lc} | dyad={dyad} | seaman≈{sm} | open≈{op} | close≈{cl} | "
        f"closing_type={closing_type} | energy={energy_s} | cast={cast_s}"
    )


# Soft diversity hints apply only once enough prior episodes exist on disk (stable signal).
_MIN_PRIOR_EPISODES_FOR_SOFT_HINTS = 4

_CLARK_ORDWAY_DYAD = ("clark", "ordway")


def _long_conversation_metas(metas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in metas if m.get("long_conversation_mode") and m.get("conversation_dyad")]


def _long_conversation_dyad_hints(metas: list[dict[str, Any]], repo_root: Path) -> list[str]:
    """Rotation nudges for ``lewis_clark_long_conversation`` when dyads repeat."""
    from pipeline.conversation_dyad import (
        LEWIS_CLARK_DYAD,
        format_dyad_label,
        load_known_conversation_dyads,
        stale_conversation_dyads,
    )

    lc_metas = _long_conversation_metas(metas)
    if len(lc_metas) < 2:
        return []
    lc_n = len(lc_metas)
    skew_thresh = max(2, (lc_n + 1) // 2)
    lc_dyad = format_dyad_label(LEWIS_CLARK_DYAD)
    ord_dyad = format_dyad_label(_CLARK_ORDWAY_DYAD)
    lc_count = sum(1 for m in lc_metas if m.get("conversation_dyad") == lc_dyad)
    ord_count = sum(1 for m in lc_metas if m.get("conversation_dyad") == ord_dyad)
    hints: list[str] = []
    known = load_known_conversation_dyads(repo_root)
    stale = stale_conversation_dyads(lc_metas, known, exclude=LEWIS_CLARK_DYAD)
    stale_s = ", ".join(stale) if stale else "clark+york, lewis+drouillard, clark+gass"

    if lc_count >= skew_thresh:
        hints.append(
            f"Recent long-conversation episodes used **lewis+clark** as the centerpiece dyad in "
            f"{lc_count}/{lc_n} runs—when today's journal **explicitly pairs** two other roster "
            f"speakers in one shared scene, prefer that journal pair or a stale dyad absent lately "
            f"({stale_s}); otherwise **keep lewis+clark** as the default."
        )
    if ord_count >= 2:
        hints.append(
            f"Recent long-conversation runs used **clark+ordway** {ord_count} time(s)—**default to "
            f"lewis+clark** unless today's journal **explicitly puts those two together** in one "
            f"beat (joint action or named pair in the same passage); do not pick Ordway from a "
            f"separate `[John Ordway]` block alone."
        )
    return hints


def _soft_hints(metas: list[dict[str, Any]], week_arc_role: str | None = None) -> list[str]:
    hints: list[str] = []
    n = len(metas)
    if n < _MIN_PRIOR_EPISODES_FOR_SOFT_HINTS:
        return hints

    from pipeline.week_arc import expected_closing_type_for_role

    week_arc_governs_pacing = expected_closing_type_for_role(week_arc_role) is not None

    lenses = [m["primary_lens"] for m in metas if m.get("primary_lens")]
    if lenses:
        c = Counter(lenses)
        top, cnt = c.most_common(1)[0]
        if cnt >= max(2, (n + 1) // 2):
            hints.append(
                f"Recent `episode_metadata.primary_lens` skew: '{top}' appears in {cnt}/{n} prior episodes—when "
                "today's journal supports it, prefer a different lens than another repeat."
            )

    tones = [m["tone_register"] for m in metas if m.get("tone_register")]
    if tones:
        c = Counter(tones)
        top, cnt = c.most_common(1)[0]
        if cnt >= max(2, (n + 1) // 2):
            hints.append(
                f"Recent `tone_register` skew: '{top}' in {cnt}/{n} prior episodes—vary tone when the source allows."
            )

    th_vals = [int(m.get("talking_head_segments") or 0) for m in metas]
    if th_vals and sum(th_vals) >= n * 2:
        hints.append(
            "Recent episodes used many `talking_head` segments—when using dialogue mode, lean on `b_roll` + "
            "environment narration where the journal is landscape- or travel-heavy."
        )

    ord_n = sum(1 for m in metas if "ordway" in (m.get("cast") or set()))
    if ord_n >= max(2, (n + 1) // 2):
        hints.append(
            "Ordway has had dialogue in several recent episodes—do not slot another Ordway beat by habit; "
            "use him when the journal supports it, keep Lewis and Clark as leads, and follow the pack's "
            "anti–captain-trio treadmill guidance."
        )

    star_hits = sum(1 for m in metas if m.get("seaman_star"))
    any_hits = sum(1 for m in metas if m.get("seaman_referenced"))
    min_presence = _min_seaman_episodes_in_window(n)

    if star_hits == 0:
        hints.append(SEAMAN_STAR_DIVERSITY_HINT)

    # Ambient nudge only when the dog has been scarce overall (star beats count toward presence).
    if any_hits < min_presence:
        hints.append(SEAMAN_AMBIENT_DIVERSITY_HINT)

    open_wake_n = sum(1 for m in metas if m.get("open_wake_morning"))
    close_camp_n = sum(1 for m in metas if m.get("close_evening_camp"))
    skew_thresh = max(2, (n + 1) // 2)

    if open_wake_n >= skew_thresh:
        hints.append(OPEN_BOOKEND_DIVERSITY_HINT)

    if close_camp_n >= skew_thresh:
        hints.append(CLOSE_BOOKEND_DIVERSITY_HINT)

    hunt_haul_n = sum(1 for m in metas if m.get("hunt_haul_heavy"))
    if hunt_haul_n >= skew_thresh:
        hints.append(HUNT_HAUL_DIVERSITY_HINT)

    phrase_hit_counts: Counter = Counter()
    for m in metas:
        for label in m.get("stock_phrase_hits") or set():
            phrase_hit_counts[label] += 1
    if phrase_hit_counts:
        top_phrase, phrase_cnt = phrase_hit_counts.most_common(1)[0]
        if phrase_cnt >= skew_thresh:
            hints.append(
                f"Recent episodes lean on the phrase/image **'{top_phrase}'** in {phrase_cnt}/{n} prior "
                "episodes—swap in a concrete, source-specific alternative image instead of repeating it."
            )

    if not week_arc_governs_pacing:
        closing_types = [m.get("closing_type") for m in metas if m.get("closing_type")]
        if closing_types:
            top, cnt = Counter(closing_types).most_common(1)[0]
            if cnt >= skew_thresh:
                other = _CLOSING_TYPE_OTHER.get(top, "a different closing_type")
                hints.append(
                    f"Recent `closing_type` skew: '{top}' in {cnt}/{n} prior episodes—when the journal "
                    f"supports it, close with **{other}** instead so endings alternate between soft "
                    "reflection and forward-looking anticipation, rather than settling every episode the "
                    "same way."
                )

        energies = [
            m.get("narrative_energy")
            for m in metas
            if isinstance(m.get("narrative_energy"), (int, float))
        ]
        if energies and max(energies) < 4:
            hints.append(
                f"Recent `narrative_energy` scores have stayed at {max(energies)} or below across {n} prior "
                "episodes—when today's journal includes real danger, conflict, or a turning point, score it "
                "4 or 5 and give the episode the weight the pack's energy rubric calls for, instead of "
                "defaulting to the middle of the scale."
            )

    return hints


def load_prior_episode_metas(
    narrations_dir: Path | str,
    current_date_id: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Snapshots of prior merged narrations (newest first), strictly before ``current_date_id``."""
    paths = _prior_merged_paths(Path(narrations_dir), current_date_id, limit)
    metas: list[dict[str, Any]] = []
    for did, path in paths:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        metas.append(_snapshot_from_narration(did, data))
    return metas


def build_recent_episode_history_lines(metas: list[dict[str, Any]]) -> list[str]:
    """One summary line per prior episode (UI reference; not sent to Phase 1)."""
    return [_format_line(m) for m in metas]


def build_diversity_hints(
    metas: list[dict[str, Any]],
    *,
    repo_root: Path | str | None = None,
    prompt_pack: str | None = None,
    narrations_dir: Path | str | None = None,
    narration_config: dict[str, Any] | None = None,
    dyad_metas: list[dict[str, Any]] | None = None,
    week_arc_role: str | None = None,
) -> list[str]:
    """Soft variety nudges: deterministic rules plus optional cached LLM audit hints.

    ``week_arc_role`` (today's week-arc ``role``, when a week-arc plan is active) suppresses the
    ``closing_type``/``narrative_energy`` nudges when the week arc already dictates the pacing for
    this day (see ``pipeline.week_arc.ROLE_PACING_GUIDANCE``)—otherwise the two systems can push
    Phase 1 in opposite directions on the same day.
    """
    from pipeline.episode_diversity_audit import (
        dedupe_and_cap_hints,
        dynamic_hints_from_cache,
        load_audit_cache,
    )

    base = _soft_hints(metas, week_arc_role=week_arc_role)
    pack_norm = (prompt_pack or "").strip().lower()
    if repo_root and pack_norm == "lewis_clark_long_conversation":
        base = base + _long_conversation_dyad_hints(
            dyad_metas if dyad_metas is not None else metas, Path(repo_root)
        )
    if not repo_root or not prompt_pack or not narrations_dir:
        return base
    if not is_lewis_clark_prompt_pack(prompt_pack):
        return base
    ediv = resolve_episode_diversity_config(repo_root, prompt_pack, narration_config)
    if not ediv:
        return base
    llm_cfg = ediv.get("llm_audit") or {}
    max_total = int(llm_cfg.get("max_total_hints", 6) or 6)
    if not llm_cfg.get("enabled"):
        return dedupe_and_cap_hints(base, max_total=max_total)
    cache = load_audit_cache(repo_root, prompt_pack)
    fired_static = {
        r["id"]
        for r in evaluate_static_diversity_rules(metas, week_arc_role=week_arc_role)
        if r.get("fired")
    }
    dynamic = dynamic_hints_from_cache(
        cache,
        metas,
        narrations_dir,
        max_dynamic_hints=int(llm_cfg.get("max_dynamic_hints", 4) or 4),
        static_hints=base,
        fired_static_rule_ids=fired_static,
    )
    return dedupe_and_cap_hints(
        base + dynamic,
        max_total=int(llm_cfg.get("max_total_hints", 6) or 6),
    )


def format_diversity_digest(hints: list[str]) -> str:
    """Phase 1 user-prompt block from hint strings."""
    if not hints:
        return ""
    lines = ["DIVERSITY HINTS (soft; obey facts and pack rules):"]
    for h in hints:
        lines.append(f"- {h}")
    return "\n".join(lines)


LEWIS_CLARK_PROMPT_PACK_ROOT = "lewis_clark"


def is_lewis_clark_prompt_pack(prompt_pack: str) -> bool:
    """True for ``lewis_clark`` and variants (dialogue, long_conversation, future LC packs)."""
    pid = (prompt_pack or "").strip().lower()
    if not pid:
        return False
    return pid == LEWIS_CLARK_PROMPT_PACK_ROOT or pid.startswith(f"{LEWIS_CLARK_PROMPT_PACK_ROOT}_")


def resolve_episode_diversity_config(
    repo_root: Path | str,
    prompt_pack: str,
    narration_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Episode-diversity settings for this run, or ``None`` when the pack is not Lewis & Clark.

    Primary source: ``prompt_packs/<pack>/pack.json`` → ``episode_diversity``.
    Falls back to ``narration_config.json`` → ``episode_diversity`` for LC packs only.
    """
    if not is_lewis_clark_prompt_pack(prompt_pack):
        return None
    from pipeline.prompt_pack_metadata import read_pack_manifest

    manifest = read_pack_manifest(Path(repo_root), prompt_pack)
    pack_ed = manifest.get("episode_diversity")
    if isinstance(pack_ed, dict):
        from pipeline.episode_diversity_audit import parse_llm_audit_config

        return {
            "enabled": bool(pack_ed.get("enabled", True)),
            "recent_window": max(0, int(pack_ed.get("recent_window", 5) or 5)),
            "llm_audit": parse_llm_audit_config(pack_ed),
        }
    cfg = narration_config if isinstance(narration_config, dict) else {}
    ediv = cfg.get("episode_diversity") or {}
    from pipeline.episode_diversity_audit import parse_llm_audit_config

    return {
        "enabled": bool(ediv.get("enabled", True)),
        "recent_window": max(0, int(ediv.get("recent_window", 5) or 5)),
        "llm_audit": parse_llm_audit_config(ediv),
    }


def build_episode_diversity_bundle(
    repo_root: Path | str,
    narrations_dir: Path | str,
    current_date_id: str,
    prompt_pack: str,
    narration_config: dict[str, Any] | None = None,
    *,
    week_arc_role: str | None = None,
) -> dict[str, Any]:
    """
    Digest + UI fields for one narration run. Non–Lewis & Clark packs return empty hints and
    ``applicable: false``.
    """
    ediv = resolve_episode_diversity_config(repo_root, prompt_pack, narration_config)
    pack_id = (prompt_pack or "").strip()
    if not ediv:
        return {
            "applicable": False,
            "enabled": False,
            "prompt_pack": pack_id,
            "recent_window": 0,
            "digest": "",
            "recent_episodes": [],
            "hints": [],
        }
    enabled = bool(ediv.get("enabled", True))
    window = max(0, int(ediv.get("recent_window", 5) or 5))
    if not enabled:
        return {
            "applicable": True,
            "enabled": False,
            "prompt_pack": pack_id,
            "recent_window": window,
            "digest": "",
            "recent_episodes": [],
            "hints": [],
        }
    prior_metas = load_prior_episode_metas(narrations_dir, current_date_id, limit=window)
    dyad_metas = prior_metas
    if pack_id == "lewis_clark_long_conversation":
        dyad_metas = load_prior_episode_metas(
            narrations_dir, current_date_id, limit=max(window, 10)
        )
    hint_lines = (
        build_diversity_hints(
            prior_metas,
            repo_root=repo_root,
            prompt_pack=pack_id,
            narrations_dir=narrations_dir,
            narration_config=narration_config,
            dyad_metas=dyad_metas,
            week_arc_role=week_arc_role,
        )
        if prior_metas
        else []
    )
    history_lines = build_recent_episode_history_lines(prior_metas) if prior_metas else []
    digest = format_diversity_digest(hint_lines)
    llm_cfg = ediv.get("llm_audit") or {}
    from pipeline.episode_diversity_audit import llm_audit_status

    llm_status = (
        llm_audit_status(
            repo_root,
            pack_id,
            narrations_dir,
            llm_cfg,
        )
        if llm_cfg.get("enabled")
        else {"enabled": False, "stale": False, "cache_present": False}
    )
    deterministic_count = (
        len(_soft_hints(prior_metas, week_arc_role=week_arc_role)) if prior_metas else 0
    )
    llm_status["dynamic_hints_active"] = max(
        0, len(hint_lines) - min(deterministic_count, len(hint_lines))
    )
    return {
        "applicable": True,
        "enabled": True,
        "prompt_pack": pack_id,
        "recent_window": window,
        "digest": digest,
        "recent_episodes": history_lines,
        "hints": hint_lines,
        "llm_audit": llm_status,
        "week_arc_role": week_arc_role,
    }


def build_recent_episodes_diversity_digest(
    narrations_dir: Path | str,
    current_date_id: str,
    *,
    limit: int = 5,
    repo_root: Path | str | None = None,
    prompt_pack: str | None = None,
    narration_config: dict[str, Any] | None = None,
) -> str:
    """
    Multi-line block for the Phase 1 user prompt: diversity hints only (no per-episode history).

    ``limit`` is the number of prior merged narration files (strictly before ``current_date_id``).

    When ``repo_root`` and ``prompt_pack`` are set, returns ``""`` for non–Lewis & Clark packs or when
    disabled in that pack's ``pack.json``.
    """
    if repo_root is not None and prompt_pack is not None:
        bundle = build_episode_diversity_bundle(
            repo_root,
            narrations_dir,
            current_date_id,
            prompt_pack,
            narration_config,
        )
        return str(bundle.get("digest") or "")
    metas = load_prior_episode_metas(narrations_dir, current_date_id, limit=limit)
    return format_diversity_digest(build_diversity_hints(metas))


def parse_recent_episodes_diversity_digest(digest: str) -> dict[str, list[str]]:
    """Split a Phase 1 diversity block into hint bullets (and legacy history lines if present)."""
    text = (digest or "").strip()
    if not text:
        return {"recent_episodes": [], "hints": []}

    recent: list[str] = []
    hints: list[str] = []
    mode: str | None = None
    for line in text.splitlines():
        if line.startswith("RECENT EPISODES ON DISK"):
            mode = "recent"
            continue
        if line.startswith("DIVERSITY HINTS"):
            mode = "hints"
            continue
        if not line.strip():
            continue
        if not line.startswith("- "):
            continue
        content = line[2:].strip()
        if mode == "hints" or mode is None:
            hints.append(content)
        elif mode == "recent":
            recent.append(content)
    return {"recent_episodes": recent, "hints": hints}


# Synthetic date for library preview: all merged narrations on disk count as prior.
_LIBRARY_PREVIEW_DATE_ID = "99999999"


def describe_deterministic_diversity_rules() -> list[dict[str, Any]]:
    """Catalog of built-in (static) diversity checks for UI / docs."""
    skew = "≥ max(2, ⌈n/2⌉) of the lookback window (requires ≥4 prior episodes on disk)"
    return [
        {
            "id": "primary_lens_skew",
            "source": "static",
            "category": "tone_lens",
            "label": "Primary lens repetition",
            "trigger": f"Most common `episode_metadata.primary_lens` appears {skew}.",
            "hint_when_fired": (
                "Recent `episode_metadata.primary_lens` skew: '<lens>' appears in <cnt>/<n> prior "
                "episodes—when today's journal supports it, prefer a different lens than another repeat."
            ),
        },
        {
            "id": "tone_register_skew",
            "source": "static",
            "category": "tone_lens",
            "label": "Tone register repetition",
            "trigger": f"Most common `tone_register` appears {skew}.",
            "hint_when_fired": (
                "Recent `tone_register` skew: '<tone>' in <cnt>/<n> prior episodes—vary tone when the source allows."
            ),
        },
        {
            "id": "talking_head_load",
            "source": "static",
            "category": "visual_beat",
            "label": "Talking-head segment load",
            "trigger": "Sum of `talking_head` segment counts ≥ n×2 across the window (n ≥ 4).",
            "hint_when_fired": (
                "Recent episodes used many `talking_head` segments—when using dialogue mode, lean on `b_roll` + "
                "environment narration where the journal is landscape- or travel-heavy."
            ),
        },
        {
            "id": "ordway_dialogue_skew",
            "source": "static",
            "category": "character_cast",
            "label": "Ordway dialogue frequency",
            "trigger": f"Ordway appears in dialogue cast in {skew}.",
            "hint_when_fired": (
                "Ordway has had dialogue in several recent episodes—do not slot another Ordway beat by habit; "
                "use him when the journal supports it, keep Lewis and Clark as leads, and follow the pack's "
                "anti–captain-trio treadmill guidance."
            ),
        },
        {
            "id": "seaman_star_absent",
            "source": "static",
            "category": "character_cast",
            "label": "Seaman star beat absent",
            "trigger": "Zero star Seaman beats (`reference_character_id` seaman/lewis_seaman or named VO) in window.",
            "patterns": list(_SEAMAN_NAME_PATTERNS),
            "hint_when_fired": SEAMAN_STAR_DIVERSITY_HINT,
        },
        {
            "id": "seaman_ambient_light",
            "source": "static",
            "category": "character_cast",
            "label": "Seaman presence light (ambient nudge)",
            "trigger": (
                "Any Seaman presence (star or ambient) below 3 of 5 in the lookback window "
                "(star beats count toward presence)."
            ),
            "patterns": list(_SEAMAN_AMBIENT_VIDEO_PATTERNS),
            "hint_when_fired": SEAMAN_AMBIENT_DIVERSITY_HINT,
        },
        {
            "id": "open_wake_morning_bookend",
            "source": "static",
            "category": "open_bookend",
            "label": "Morning / wake segment-1 bookend",
            "trigger": f"Segment-1 wake/morning regex hits {skew}.",
            "check_type": "first_segment_regex",
            "patterns": list(_OPEN_WAKE_MORNING_PATTERNS),
            "hint_when_fired": OPEN_BOOKEND_DIVERSITY_HINT,
        },
        {
            "id": "close_evening_camp_bookend",
            "source": "static",
            "category": "close_bookend",
            "label": "Evening camp final-segment bookend",
            "trigger": f"Last-segment camp/fire/tent ending regex hits {skew}.",
            "check_type": "last_segment_regex",
            "patterns": list(_CLOSE_EVENING_CAMP_PATTERNS),
            "hint_when_fired": CLOSE_BOOKEND_DIVERSITY_HINT,
        },
        {
            "id": "hunt_haul_heavy",
            "source": "static",
            "category": "visual_beat",
            "label": "Hunt / haul visual repetition",
            "trigger": f"Hunt/haul `video_prompt` regex hits {skew}.",
            "check_type": "video_prompt_regex",
            "patterns": list(_HUNT_HAUL_VIDEO_PATTERNS),
            "hint_when_fired": HUNT_HAUL_DIVERSITY_HINT,
        },
        {
            "id": "stock_phrase_repetition",
            "source": "static",
            "category": "language_imagery",
            "label": "Stock phrase / imagery repetition",
            "trigger": f"Most-repeated curated stock phrase (e.g. 'gentle breeze', 'sandbar', 'riverbank', "
            f"'prairie stretch') appears in {skew}.",
            "patterns": [pat for _, pat in _STOCK_PHRASE_PATTERNS],
            "hint_when_fired": (
                "Recent episodes lean on the phrase/image '<phrase>' in <cnt>/<n> prior episodes—swap in a "
                "concrete, source-specific alternative image instead of repeating it."
            ),
        },
        {
            "id": "closing_type_skew",
            "source": "static",
            "category": "tone_lens",
            "label": "Closing type repetition",
            "trigger": f"Most common top-level `closing_type` appears {skew}.",
            "hint_when_fired": (
                "Recent `closing_type` skew: '<type>' in <cnt>/<n> prior episodes—when the journal "
                "supports it, alternate toward '<other>' so endings vary between soft reflection and "
                "forward-looking anticipation."
            ),
        },
        {
            "id": "narrative_energy_stagnant",
            "source": "static",
            "category": "tone_lens",
            "label": "Narrative energy stagnation",
            "trigger": (
                "Max `episode_metadata.narrative_energy` across the lookback window stays below 4 "
                "(requires ≥4 prior episodes)."
            ),
            "hint_when_fired": (
                "Recent `narrative_energy` scores have stayed at <max> or below across <n> prior "
                "episodes—when today's journal includes real danger, conflict, or a turning point, "
                "score it 4 or 5 and weight the episode accordingly instead of defaulting to the "
                "middle of the scale."
            ),
        },
        {
            "id": "lewis_clark_conversation_dyad_skew",
            "source": "static",
            "category": "character_cast",
            "label": "Lewis+Clark long-conversation dyad repetition",
            "trigger": (
                f"Among prior episodes with `long_conversation_mode` and a recorded dyad, "
                f"lewis+clark appears {skew} (requires ≥2 such episodes)."
            ),
            "hint_when_fired": (
                "Recent long-conversation episodes used **lewis+clark** as the centerpiece dyad in "
                "<cnt>/<lc_n> runs—when today's journal **explicitly pairs** two other roster "
                "speakers in one shared scene, prefer that journal pair or a stale dyad absent lately; "
                "otherwise **keep lewis+clark** as the default."
            ),
        },
        {
            "id": "clark_ordway_conversation_dyad_skew",
            "source": "static",
            "category": "character_cast",
            "label": "Clark+Ordway long-conversation dyad repetition",
            "trigger": (
                "clark+ordway is the long-conversation dyad in ≥2 prior long-conversation episodes "
                "in the lookback window."
            ),
            "hint_when_fired": (
                "Recent long-conversation runs used **clark+ordway** repeatedly—**default to lewis+clark** "
                "unless today's journal **explicitly puts those two together** in one beat; do not pick "
                "Ordway from a separate `[John Ordway]` block alone."
            ),
        },
    ]


def evaluate_static_diversity_rules(
    metas: list[dict[str, Any]],
    *,
    week_arc_role: str | None = None,
) -> list[dict[str, Any]]:
    """Static rule catalog with fired/detail for a prior-episode window."""
    catalog = describe_deterministic_diversity_rules()
    n = len(metas)
    if n < _MIN_PRIOR_EPISODES_FOR_SOFT_HINTS:
        return [
            {
                **r,
                "fired": False,
                "detail": f"need ≥{_MIN_PRIOR_EPISODES_FOR_SOFT_HINTS} priors (have {n})",
            }
            for r in catalog
        ]

    from pipeline.week_arc import expected_closing_type_for_role

    week_arc_governs_pacing = expected_closing_type_for_role(week_arc_role) is not None
    skew_thresh = max(2, (n + 1) // 2)
    fired_ids: set[str] = set()

    lenses = [m["primary_lens"] for m in metas if m.get("primary_lens")]
    if lenses:
        top, cnt = Counter(lenses).most_common(1)[0]
        if cnt >= skew_thresh:
            fired_ids.add("primary_lens_skew")

    tones = [m["tone_register"] for m in metas if m.get("tone_register")]
    if tones:
        top, cnt = Counter(tones).most_common(1)[0]
        if cnt >= skew_thresh:
            fired_ids.add("tone_register_skew")

    th_vals = [int(m.get("talking_head_segments") or 0) for m in metas]
    if th_vals and sum(th_vals) >= n * 2:
        fired_ids.add("talking_head_load")

    ord_n = sum(1 for m in metas if "ordway" in (m.get("cast") or set()))
    if ord_n >= skew_thresh:
        fired_ids.add("ordway_dialogue_skew")

    star_hits = sum(1 for m in metas if m.get("seaman_star"))
    any_hits = sum(1 for m in metas if m.get("seaman_referenced"))
    if star_hits == 0:
        fired_ids.add("seaman_star_absent")
    if any_hits < _min_seaman_episodes_in_window(n):
        fired_ids.add("seaman_ambient_light")

    open_wake_n = sum(1 for m in metas if m.get("open_wake_morning"))
    if open_wake_n >= skew_thresh:
        fired_ids.add("open_wake_morning_bookend")

    close_camp_n = sum(1 for m in metas if m.get("close_evening_camp"))
    if close_camp_n >= skew_thresh:
        fired_ids.add("close_evening_camp_bookend")

    hunt_haul_n = sum(1 for m in metas if m.get("hunt_haul_heavy"))
    if hunt_haul_n >= skew_thresh:
        fired_ids.add("hunt_haul_heavy")

    phrase_hit_counts: Counter = Counter()
    for m in metas:
        for label in m.get("stock_phrase_hits") or set():
            phrase_hit_counts[label] += 1
    top_phrase, top_phrase_cnt = (
        phrase_hit_counts.most_common(1)[0] if phrase_hit_counts else ("", 0)
    )
    if top_phrase_cnt >= skew_thresh:
        fired_ids.add("stock_phrase_repetition")

    closing_types = [m.get("closing_type") for m in metas if m.get("closing_type")]
    if closing_types and not week_arc_governs_pacing:
        top_closing, closing_cnt = Counter(closing_types).most_common(1)[0]
        if closing_cnt >= skew_thresh:
            fired_ids.add("closing_type_skew")

    energies = [
        m.get("narrative_energy")
        for m in metas
        if isinstance(m.get("narrative_energy"), (int, float))
    ]
    if energies and max(energies) < 4 and not week_arc_governs_pacing:
        fired_ids.add("narrative_energy_stagnant")

    lc_metas = _long_conversation_metas(metas)
    if len(lc_metas) >= 2:
        lc_n = len(lc_metas)
        lc_skew = max(2, (lc_n + 1) // 2)
        lc_count = sum(1 for m in lc_metas if m.get("conversation_dyad") == "clark+lewis")
        ord_count = sum(1 for m in lc_metas if m.get("conversation_dyad") == "clark+ordway")
        if lc_count >= lc_skew:
            fired_ids.add("lewis_clark_conversation_dyad_skew")
        if ord_count >= 2:
            fired_ids.add("clark_ordway_conversation_dyad_skew")

    details = {
        "primary_lens_skew": f"{Counter(lenses).most_common(1)[0][1]}/{n}" if lenses else f"0/{n}",
        "tone_register_skew": f"{Counter(tones).most_common(1)[0][1]}/{n}" if tones else f"0/{n}",
        "talking_head_load": f"{sum(th_vals)} talking_head segments across {n} episodes",
        "ordway_dialogue_skew": f"{ord_n}/{n}",
        "seaman_star_absent": f"{star_hits} star beats in window",
        "seaman_ambient_light": f"{any_hits}/{n} episodes with any Seaman presence",
        "open_wake_morning_bookend": f"{open_wake_n}/{n}",
        "close_evening_camp_bookend": f"{close_camp_n}/{n}",
        "hunt_haul_heavy": f"{hunt_haul_n}/{n}",
        "stock_phrase_repetition": (
            f"'{top_phrase}' {top_phrase_cnt}/{n}" if top_phrase else f"0/{n}"
        ),
        "closing_type_skew": (
            f"suppressed: week-arc role '{week_arc_role}' governs pacing"
            if week_arc_governs_pacing
            else (
                f"{Counter(closing_types).most_common(1)[0][1]}/{n}" if closing_types else f"0/{n}"
            )
        ),
        "narrative_energy_stagnant": (
            f"suppressed: week-arc role '{week_arc_role}' governs pacing"
            if week_arc_governs_pacing
            else (f"max={max(energies)}/5 across {n}" if energies else f"no scores/{n}")
        ),
        "lewis_clark_conversation_dyad_skew": (
            f"{sum(1 for m in lc_metas if m.get('conversation_dyad') == 'clark+lewis')}/{len(lc_metas)}"
            if len(lc_metas) >= 2
            else f"need ≥2 long-conversation priors (have {len(lc_metas)})"
        ),
        "clark_ordway_conversation_dyad_skew": (
            f"{sum(1 for m in lc_metas if m.get('conversation_dyad') == 'clark+ordway')}/{len(lc_metas)}"
            if len(lc_metas) >= 2
            else f"need ≥2 long-conversation priors (have {len(lc_metas)})"
        ),
    }
    out: list[dict[str, Any]] = []
    for row in catalog:
        rid = row["id"]
        out.append({**row, "fired": rid in fired_ids, "detail": details.get(rid, "")})
    return out


def build_library_diversity_payload(
    repo_root: Path | str,
    narrations_dir: Path | str,
    *,
    prompt_pack: str = "lewis_clark",
    narration_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only diversity catalog + live preview for Pipeline UI Library tab."""
    from pipeline.episode_diversity_audit import (
        audit_cache_path,
        dynamic_hints_from_cache,
        load_audit_cache,
        newest_merged_date_id,
    )

    repo_root = Path(repo_root)
    narrations_dir = Path(narrations_dir)
    pack_id = (prompt_pack or "lewis_clark").strip()
    ediv = resolve_episode_diversity_config(repo_root, pack_id, narration_config)
    window = max(0, int((ediv or {}).get("recent_window", 5) or 5))
    llm_cfg = (ediv or {}).get("llm_audit") or {}

    prior_metas = load_prior_episode_metas(narrations_dir, _LIBRARY_PREVIEW_DATE_ID, limit=window)
    static_eval = evaluate_static_diversity_rules(prior_metas)
    static_hints = _soft_hints(prior_metas)
    fired_static = {r["id"] for r in static_eval if r.get("fired")}

    cache = load_audit_cache(repo_root, pack_id) if is_lewis_clark_prompt_pack(pack_id) else None
    dynamic_hints: list[str] = []
    dynamic_patterns: list[dict[str, Any]] = []
    if cache and llm_cfg.get("enabled"):
        dynamic_hints = dynamic_hints_from_cache(
            cache,
            prior_metas,
            narrations_dir,
            max_dynamic_hints=int(llm_cfg.get("max_dynamic_hints", 4) or 4),
            static_hints=static_hints,
            fired_static_rule_ids=fired_static,
        )
        for row in cache.get("patterns") or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or "")
            checker = None
            for c in cache.get("recommended_checkers") or []:
                if isinstance(c, dict) and str(c.get("id") or "") == pid:
                    checker = c
                    break
            hint = str(row.get("suggested_hint") or "").strip()
            from pipeline.episode_diversity_audit import pattern_suppressed_by_static_rules

            suppressed = pattern_suppressed_by_static_rules(row, fired_static)
            active = hint in dynamic_hints
            dynamic_patterns.append(
                {
                    **row,
                    "checker": checker,
                    "active": active,
                    "suppressed_by_static": suppressed,
                }
            )

    merged_hints = build_diversity_hints(
        prior_metas,
        repo_root=repo_root,
        prompt_pack=pack_id,
        narrations_dir=narrations_dir,
        narration_config=narration_config,
    )

    lc_packs: list[dict[str, Any]] = []
    packs_dir = packs_root(repo_root)
    if packs_dir.is_dir():
        for pack_dir in sorted(packs_dir.iterdir(), key=lambda p: p.name.lower()):
            if not pack_dir.is_dir() or not is_lewis_clark_prompt_pack(pack_dir.name):
                continue
            cfg = resolve_episode_diversity_config(repo_root, pack_dir.name, narration_config)
            lc_packs.append({"id": pack_dir.name, "episode_diversity": cfg})

    newest = newest_merged_date_id(narrations_dir)
    from pipeline.episode_diversity_audit import llm_audit_status

    llm_status = (
        llm_audit_status(repo_root, pack_id, narrations_dir, llm_cfg)
        if llm_cfg.get("enabled")
        else {"enabled": False, "stale": False, "cache_present": False}
    )
    return {
        "ok": True,
        "prompt_pack": pack_id,
        "newest_merged_date_id": newest,
        "preview_note": (
            "Live preview assumes the next narration after the newest on-disk episode "
            f"(lookback window = {window} prior files)."
        ),
        "cache_path": str(audit_cache_path(repo_root, pack_id).relative_to(repo_root)).replace(
            "\\", "/"
        ),
        "episode_diversity": ediv,
        "lc_pack_configs": lc_packs,
        "static_rules": static_eval,
        "static_hints_active": static_hints,
        "dynamic_patterns": dynamic_patterns,
        "dynamic_hints_active": dynamic_hints,
        "merged_hints_active": merged_hints,
        "recent_episodes": build_recent_episode_history_lines(prior_metas),
        "cache": cache,
        "llm_audit": llm_status,
    }
