"""Per-segment visual routing: B-roll (Wan) vs talking-head (FAL avatar)."""

from __future__ import annotations

from typing import Any

VISUAL_MODE_B_ROLL = "b_roll"
VISUAL_MODE_TALKING_HEAD = "talking_head"
_VALID_MODES = frozenset({VISUAL_MODE_B_ROLL, VISUAL_MODE_TALKING_HEAD})

# First talking-head must be at 1-based segment index >= this (after at least two b_roll beats).
MIN_SEGMENT_INDEX_FIRST_TALKING_HEAD = 3
# At most this many talking_head segments in a row.
MAX_CONSECUTIVE_TALKING_HEAD = 2
# Long-conversation pack: several short alternating talking_head clips in one exchange (e.g. A/B/A/B…).
# Lowered from 12 to 8: paired with the MAX_TOTAL_SPOKEN_WORDS episode cap, a 12-clip run left no
# room for the b_roll bookends without blowing the word budget (observed: a 12-segment episode at
# roughly this pack's old targets ran 3:25, well past YouTube's 3-minute Shorts limit).
MAX_CONSECUTIVE_TALKING_HEAD_LONG = 8
# Long pack: at most this many cast dialogue lines per talking_head segment (ping-pong, not monologue blocks).
MAX_TALKING_HEAD_CAST_LINES_LONG = 1


def normalize_visual_mode(seg: dict[str, Any]) -> str:
    raw = (seg.get("visual_mode") or "").strip().lower()
    if not raw:
        return VISUAL_MODE_B_ROLL
    if raw not in _VALID_MODES:
        return raw  # caller validates
    return raw


def visual_modes_for_narration_script(data: dict[str, Any]) -> list[str]:
    """
    Return visual_mode per narration_script row in order (length == len(narration_script)).
    Missing visual_mode defaults to b_roll.
    """
    script = data.get("narration_script") or []
    out: list[str] = []
    for seg in script:
        if not isinstance(seg, dict):
            out.append(VISUAL_MODE_B_ROLL)
            continue
        m = normalize_visual_mode(seg)
        out.append(m if m in _VALID_MODES else VISUAL_MODE_B_ROLL)
    return out


def _is_animal_subject(character_id: str) -> bool:
    cid = (character_id or "").strip().lower()
    if not cid:
        return False
    if cid in ("seaman",):
        return True
    try:
        from pipeline.narration_characters import load_characters

        animal_role_markers = frozenset({"dog", "animal", "horse", "mule", "ox", "livestock"})
        for c in load_characters():
            if c.id.lower() != cid:
                continue
            for r in c.roles:
                if str(r).strip().lower() in animal_role_markers:
                    return True
            return False
    except Exception:
        return False


def _non_narrator_speaker_sequence(seg: dict[str, Any]) -> list[str]:
    """Ordered speaker_id list for non-narrator dialogue rows with non-empty text."""
    rows = seg.get("dialogue")
    if not isinstance(rows, list):
        return []
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("speaker_id") or "").strip().lower()
        txt = str(row.get("text") or "").strip()
        if not sid or sid == "narrator" or not txt:
            continue
        out.append(sid)
    return out


def b_roll_two_speaker_lines_strictly_alternate(
    seg: dict[str, Any], *, min_nonempty_lines: int
) -> bool:
    """
    True when ``dialogue`` (non-narrator lines only) shows strict A/B/A/B alternation between
    exactly two expedition speakers—no back-to-back lines from the same cast id.

    Used for long-conversation ``b_roll`` centerpiece beats so the exchange reads as
    characters responding to each other, not paired monologues.
    """
    if normalize_visual_mode(seg) != VISUAL_MODE_B_ROLL:
        return True
    seq = _non_narrator_speaker_sequence(seg)
    if len(seq) < int(min_nonempty_lines):
        return False
    if len(set(seq)) != 2:
        return False
    for i in range(len(seq) - 1):
        if seq[i] == seq[i + 1]:
            return False
    return True


def exchange_dialogue_pair_for_long_conversation(
    seg: dict[str, Any], *, min_nonempty_lines: int
) -> tuple[str, str] | None:
    """If dialogue has >= min_nonempty_lines non-narrator lines and exactly two distinct speakers, return sorted pair."""
    rows = seg.get("dialogue")
    if not isinstance(rows, list) or len(rows) < min_nonempty_lines:
        return None
    speakers: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("speaker_id") or "").strip().lower()
        txt = str(row.get("text") or "").strip()
        if not sid or sid == "narrator" or not txt:
            continue
        speakers.append(sid)
    if len(speakers) < min_nonempty_lines:
        return None
    uniq = sorted(set(speakers))
    if len(uniq) != 2:
        return None
    return (uniq[0], uniq[1])


def _segment_non_narrator_speakers(seg: dict[str, Any]) -> set[str]:
    rows = seg.get("dialogue")
    if not isinstance(rows, list):
        return set()
    out: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("speaker_id") or "").strip().lower()
        txt = str(row.get("text") or "").strip()
        if not sid or sid == "narrator" or not txt:
            continue
        out.add(sid)
    return out


def _non_narrator_nonempty_line_count(seg: dict[str, Any]) -> int:
    rows = seg.get("dialogue")
    if not isinstance(rows, list):
        return 0
    n = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("speaker_id") or "").strip().lower()
        txt = str(row.get("text") or "").strip()
        if not sid or sid == "narrator" or not txt:
            continue
        n += 1
    return n


def min_lines_for_long_conversation_segment(
    seg: dict[str, Any],
    *,
    min_lines_talking_head: int,
    min_lines_b_roll: int,
) -> int:
    """Per-segment line floor for sustained-exchange validation (mode-dependent)."""
    if normalize_visual_mode(seg) == VISUAL_MODE_TALKING_HEAD:
        return max(1, int(min_lines_talking_head))
    return max(2, int(min_lines_b_roll))


def parse_long_conversation_mode_config(convo_cfg: dict[str, Any] | None) -> dict[str, int]:
    """Resolve long-pack conversation thresholds from ``dialogue_conversation_mode`` config."""
    cfg = convo_cfg if isinstance(convo_cfg, dict) else {}
    min_segments = max(4, int(cfg.get("min_segments", 6) or 6))
    min_lines_th = int(
        cfg.get("min_lines_per_talking_head_segment") or cfg.get("min_lines_per_segment") or 1
    )
    min_lines_broll = int(cfg.get("min_lines_per_b_roll_segment", 4) or 4)
    max_lines_th = int(
        cfg.get("max_lines_per_talking_head_segment", MAX_TALKING_HEAD_CAST_LINES_LONG)
        or MAX_TALKING_HEAD_CAST_LINES_LONG
    )
    return {
        "min_segments": min_segments,
        "min_lines_talking_head": max(1, min_lines_th),
        "min_lines_b_roll": max(2, min_lines_broll),
        "max_lines_talking_head": max(1, max_lines_th),
    }


def segment_qualifies_long_conversation_two_speaker_beat(
    seg: dict[str, Any], *, min_nonempty_lines: int
) -> bool:
    """
    True when this segment counts toward a sustained two-speaker exchange:

    - b_roll: at least ``min_nonempty_lines`` non-narrator lines from exactly **two**
      distinct expedition speakers, with **strict alternation** (A,B,A,B, …) on those
      cast lines—no two consecutive lines from the same non-``narrator`` speaker.
    - talking_head: at least ``min_nonempty_lines`` non-narrator lines from **only**
      ``talking_head_subject`` (one primary speaker per clip), portrait on disk,
      and ``reference_character_id`` must **not** be a pair-portrait composite id.
    """
    if _non_narrator_nonempty_line_count(seg) < int(min_nonempty_lines):
        return False
    mode = normalize_visual_mode(seg)
    if mode == VISUAL_MODE_B_ROLL:
        if (
            exchange_dialogue_pair_for_long_conversation(seg, min_nonempty_lines=min_nonempty_lines)
            is None
        ):
            return False
        return b_roll_two_speaker_lines_strictly_alternate(
            seg, min_nonempty_lines=min_nonempty_lines
        )

    if mode != VISUAL_MODE_TALKING_HEAD:
        return False
    sub = str(seg.get("talking_head_subject") or "").strip().lower()
    if not sub:
        return False
    cast = _segment_non_narrator_speakers(seg)
    if cast != {sub}:
        return False

    ref = str(seg.get("reference_character_id") or "").strip().lower()
    try:
        from pipeline.narration_characters.storage import (
            character_portrait_asset_exists,
            load_pair_portrait_rules,
        )
    except Exception:
        return False

    if ref:
        if any(rule.composite_id == ref for rule in load_pair_portrait_rules()):
            return False

    if not character_portrait_asset_exists(sub):
        return False
    return True


def _talking_head_same_subject_adjoins(prev: dict[str, Any], nxt: dict[str, Any]) -> bool:
    """True when two consecutive segments are talking_head clips for the same on-camera subject."""
    if normalize_visual_mode(prev) != VISUAL_MODE_TALKING_HEAD:
        return False
    if normalize_visual_mode(nxt) != VISUAL_MODE_TALKING_HEAD:
        return False
    a = (prev.get("talking_head_subject") or "").strip().lower()
    b = (nxt.get("talking_head_subject") or "").strip().lower()
    return bool(a and b and a == b)


def has_long_conversation_sustained_exchange(
    segments: list[Any],
    *,
    min_segments: int,
    min_lines_per_segment: int,
    min_lines_talking_head: int | None = None,
    min_lines_b_roll: int | None = None,
    require_talking_head_in_window: bool = False,
) -> bool:
    """
    True when some window of consecutive qualifying segments establishes one stable
    two-person conversation: union of non-narrator speakers across the window is
    exactly two ids, ``len(window) >= min_segments``, and each segment satisfies
    :func:`segment_qualifies_long_conversation_two_speaker_beat`.

    Additionally, two consecutive **talking_head** clips must not use the same
    ``talking_head_subject`` (forces Lewis/Clark/… to ping-pong across clips rather
    than stacking soliloquies).

    ``require_talking_head_in_window`` (week-arc-driven days, see
    ``pipeline.week_arc.requires_talking_head``) additionally requires the qualifying
    window itself to contain **at least one** ``talking_head`` segment—an unrelated
    talking_head clip elsewhere in the episode does not count, so a "planned long
    conversation" can't be satisfied by an all-``b_roll`` exchange plus a stray face shot.
    """
    ms = max(1, int(min_segments))
    ml_th = max(
        1,
        int(
            min_lines_talking_head if min_lines_talking_head is not None else min_lines_per_segment
        ),
    )
    ml_br = max(2, int(min_lines_b_roll if min_lines_b_roll is not None else min_lines_per_segment))
    run: list[dict[str, Any]] = []
    for seg in segments:
        if not isinstance(seg, dict):
            run.clear()
            continue
        ml = min_lines_for_long_conversation_segment(
            seg, min_lines_talking_head=ml_th, min_lines_b_roll=ml_br
        )
        if not segment_qualifies_long_conversation_two_speaker_beat(seg, min_nonempty_lines=ml):
            run.clear()
            continue
        if run and _talking_head_same_subject_adjoins(run[-1], seg):
            run.clear()
        run.append(seg)
        union: set[str] = set()
        for s in run:
            union |= _segment_non_narrator_speakers(s)
        if len(union) > 2:
            run = [seg]
            continue
        if len(run) >= ms and len(union) == 2:
            if not require_talking_head_in_window or any(
                normalize_visual_mode(s) == VISUAL_MODE_TALKING_HEAD for s in run
            ):
                return True
    return False


def validate_dialogue_visual_modes(
    segments: list[Any],
    *,
    allowed_speakers: frozenset[str],
    max_consecutive_talking_head: int | None = None,
    max_talking_head_cast_lines: int | None = None,
    require_talking_head: bool = False,
) -> None:
    """
    Validate visual_mode / talking_head_subject / dialogue rules for dialogue-pack Phase 1.
    Raises ValueError on violation.

    ``require_talking_head`` fails the episode when it contains **zero** ``talking_head``
    segments—set by callers driven by a week-arc day plan whose ``recommended_mode`` is
    ``dialogue``/``long_conversation`` (see ``pipeline.week_arc.requires_talking_head``),
    so the planned on-camera beat is not silently dropped to all-``b_roll``.
    """
    if not isinstance(segments, list):
        raise ValueError("segments must be a list")
    th_cap = (
        int(max_consecutive_talking_head)
        if max_consecutive_talking_head is not None
        else MAX_CONSECUTIVE_TALKING_HEAD
    )
    if th_cap < 1:
        th_cap = 1
    first_th_1based: int | None = None
    run_th = 0
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise ValueError(f"Phase 1 segment {i} must be an object")
        mode = (seg.get("visual_mode") or "").strip().lower() or VISUAL_MODE_B_ROLL
        if mode not in _VALID_MODES:
            raise ValueError(
                f"Phase 1 segment {i}: visual_mode must be {sorted(_VALID_MODES)!r}, got {mode!r}"
            )
        idx_1based = i + 1
        if mode == VISUAL_MODE_TALKING_HEAD:
            if first_th_1based is None:
                first_th_1based = idx_1based
            run_th += 1
            if run_th > th_cap:
                raise ValueError(
                    f"Phase 1 segment {i}: at most {th_cap} consecutive "
                    f"{VISUAL_MODE_TALKING_HEAD} segments; insert {VISUAL_MODE_B_ROLL} between character beats."
                )
            sub = (seg.get("talking_head_subject") or "").strip().lower()
            if not sub:
                raise ValueError(
                    f"Phase 1 segment {i}: {VISUAL_MODE_TALKING_HEAD} requires non-empty talking_head_subject"
                )
            if sub not in allowed_speakers:
                raise ValueError(
                    f"Phase 1 segment {i}: unknown talking_head_subject {sub!r}; "
                    "use narration_characters ids or narrator"
                )
            if _is_animal_subject(sub):
                raise ValueError(
                    f"Phase 1 segment {i}: talking_head_subject {sub!r} is not supported for talking-head video"
                )
            try:
                from pipeline.narration_characters.storage import character_portrait_asset_exists
            except Exception:
                character_portrait_asset_exists = None  # type: ignore[assignment]
            dlg = seg.get("dialogue")
            ref_for_portrait = str(seg.get("reference_character_id") or "").strip().lower()
            try:
                from pipeline.narration_characters.storage import (
                    load_pair_portrait_rules,
                    portrait_exists_for_composite,
                )
            except Exception:

                def load_pair_portrait_rules():  # type: ignore[misc]
                    return ()

                portrait_exists_for_composite = None  # type: ignore[assignment]

            if ref_for_portrait:
                for rule in load_pair_portrait_rules():
                    if rule.composite_id == ref_for_portrait:
                        raise ValueError(
                            f"Phase 1 segment {i}: {VISUAL_MODE_TALKING_HEAD} must use a solo portrait for the "
                            f"on-camera speaker; do not set reference_character_id to pair composite "
                            f"{ref_for_portrait!r}—split the exchange into per-speaker segments."
                        )

            _has_ref_composite = bool(
                portrait_exists_for_composite
                and ref_for_portrait
                and portrait_exists_for_composite(ref_for_portrait)
            )
            if character_portrait_asset_exists and not (
                _has_ref_composite or character_portrait_asset_exists(sub)
            ):
                raise ValueError(
                    f"Phase 1 segment {i}: no portrait asset for talking_head_subject {sub!r} "
                    f"or composite reference_character_id (expected character-portraits/{{id}}.png or .jpg)"
                )
            if not isinstance(dlg, list) or len(dlg) < 1:
                raise ValueError(
                    f"Phase 1 segment {i}: {VISUAL_MODE_TALKING_HEAD} requires a non-empty dialogue array "
                    "with lines only from talking_head_subject (segment TTS is gap + dialogue; "
                    "`narration` is not synthesized for this mode but may remain as script backup)."
                )
            speakers: list[str] = []
            for j, line in enumerate(dlg):
                if not isinstance(line, dict):
                    raise ValueError(f"Phase 1 segment {i} dialogue[{j}] must be an object")
                sid = (line.get("speaker_id") or "").strip().lower()
                txt = (line.get("text") or "").strip()
                if not sid or not txt:
                    raise ValueError(
                        f"Phase 1 segment {i} dialogue[{j}] needs non-empty speaker_id and text"
                    )
                if sid not in allowed_speakers:
                    raise ValueError(
                        f"Phase 1 segment {i} dialogue[{j}] unknown speaker_id {sid!r}"
                    )
                speakers.append(sid)
            distinct_cast = {s for s in speakers if s != "narrator"}
            if len(distinct_cast) == 0:
                raise ValueError(
                    f"Phase 1 segment {i}: {VISUAL_MODE_TALKING_HEAD} needs at least one non-narrator "
                    "dialogue line matching talking_head_subject"
                )
            if len(distinct_cast) > 1:
                raise ValueError(
                    f"Phase 1 segment {i}: {VISUAL_MODE_TALKING_HEAD} allows only the on-camera "
                    f"speaker in dialogue (talking_head_subject={sub!r}); got {sorted(distinct_cast)!r}. "
                    "Split alternating lines into separate segments (one primary speaker per clip) or use b_roll."
                )
            sole = next(iter(distinct_cast))
            if sole != sub or any(s not in (sub, "narrator") for s in speakers):
                raise ValueError(
                    f"Phase 1 segment {i}: {VISUAL_MODE_TALKING_HEAD} requires every dialogue line to use "
                    f"speaker_id {sub!r} or narrator only (not {sorted(set(speakers))!r})."
                )
            cast_line_count = sum(1 for s in speakers if s != "narrator")
            if max_talking_head_cast_lines is not None and cast_line_count > int(
                max_talking_head_cast_lines
            ):
                raise ValueError(
                    f"Phase 1 segment {i}: {VISUAL_MODE_TALKING_HEAD} allows exactly "
                    f"{int(max_talking_head_cast_lines)} non-narrator dialogue line per clip in this pack "
                    "(one `dialogue[]` row; multiple sentences in that string are fine); "
                    f"got {cast_line_count}. Split into more alternating segments instead of multiple rows."
                )
        else:
            run_th = 0
            dlg = seg.get("dialogue")
            if dlg is not None and dlg != []:
                if not isinstance(dlg, list):
                    raise ValueError(f"Phase 1 segment {i}: dialogue must be an array when present")
                for j, line in enumerate(dlg):
                    if not isinstance(line, dict):
                        raise ValueError(f"Phase 1 segment {i} dialogue[{j}] must be an object")
                    sid = line.get("speaker_id")
                    txt = line.get("text")
                    if not isinstance(sid, str) or not sid.strip():
                        raise ValueError(
                            f"Phase 1 segment {i} dialogue[{j}] must have string speaker_id"
                        )
                    if not isinstance(txt, str) or not txt.strip():
                        raise ValueError(
                            f"Phase 1 segment {i} dialogue[{j}] must have non-empty string text"
                        )
                    sid_norm = sid.strip().lower()
                    if len(allowed_speakers) > 1 and sid_norm not in allowed_speakers:
                        raise ValueError(
                            f"Phase 1 segment {i} dialogue[{j}] unknown speaker_id {sid_norm!r}"
                        )

    if first_th_1based is not None and first_th_1based < MIN_SEGMENT_INDEX_FIRST_TALKING_HEAD:
        raise ValueError(
            f"First {VISUAL_MODE_TALKING_HEAD} segment must be at segment_index "
            f">= {MIN_SEGMENT_INDEX_FIRST_TALKING_HEAD} (keep at least two {VISUAL_MODE_B_ROLL} "
            f"opening segments for narrator-first pacing); got first at {first_th_1based}"
        )

    if require_talking_head and first_th_1based is None:
        raise ValueError(
            "The week-arc plan for this day calls for a talking-head beat: convert at least one "
            f"interpersonal or first-person segment (segment_index >= {MIN_SEGMENT_INDEX_FIRST_TALKING_HEAD}) "
            f"to `visual_mode`: `{VISUAL_MODE_TALKING_HEAD}` with a `talking_head_subject` from "
            "the planned speakers, dialogue lines only from that speaker, and a `talking_head_prompt`."
        )
