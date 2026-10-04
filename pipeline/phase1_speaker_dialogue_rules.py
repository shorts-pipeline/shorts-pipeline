"""Per-speaker Phase 1 dialogue content rules (post-LLM validation)."""

from __future__ import annotations

import math
import re
from typing import Any

from pipeline.narration_visual_mode import (
    VISUAL_MODE_B_ROLL,
    VISUAL_MODE_TALKING_HEAD,
    normalize_visual_mode,
)

# Drouillard: mixed Shawnee/French Canadian scout & sign-talker — not a booster for Euro-American settlement.
_DROUILLARD_FORBIDDEN = re.compile(
    r"\b("
    r"settlement|settlements|settler|settlers|homestead|coloniz|colonist|colonists|"
    r"pioneer town|townsite|plant a town|establish a town|"
    r"promise for settlement|good (?:country|land) to settle|country to settle|"
    r"hold such promise"
    r")\b",
    re.IGNORECASE,
)


def _iter_cast_dialogue_lines(
    segments: list[Any],
) -> list[tuple[int, int, str, str]]:
    """(segment_index_1based, dialogue_row_index, speaker_id, text)."""
    out: list[tuple[int, int, str, str]] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        rows = seg.get("dialogue")
        if not isinstance(rows, list):
            continue
        for j, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            sid = str(row.get("speaker_id") or "").strip().lower()
            text = str(row.get("text") or "").strip()
            if not sid or sid == "narrator" or not text:
                continue
            seg_n = seg.get("segment_index")
            if isinstance(seg_n, int) and seg_n > 0:
                one_based = seg_n
            else:
                one_based = i + 1
            out.append((one_based, j, sid, text))
    return out


def segment_expects_one_cast_speaker_per_segment(
    seg: dict[str, Any],
    *,
    long_conversation_mode: bool,
) -> bool:
    """
    True when TTS synthesis assumes at most one cast speaker per segment.

    Long-conversation ``b_roll`` beats may alternate Lewis/Clark lines in one segment;
    ``talking_head`` clips always use a single subject.
    """
    mode = normalize_visual_mode(seg)
    if mode == VISUAL_MODE_TALKING_HEAD:
        return True
    if long_conversation_mode and mode == VISUAL_MODE_B_ROLL:
        return False
    return True


def _tts_one_speaker_segment_errors(one_based: int, seg: dict[str, Any]) -> list[str]:
    """Return TTS layout error messages for one segment (caller skips when multi-speaker allowed)."""
    errors: list[str] = []
    cast: list[str] = []
    rows = seg.get("dialogue")
    if isinstance(rows, list):
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("speaker_id") or "").strip().lower()
            txt = str(row.get("text") or "").strip()
            if not sid or sid == "narrator" or not txt:
                continue
            if sid not in seen:
                seen.add(sid)
                cast.append(sid)
    has_narrator_dlg = False
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("speaker_id") or "").strip().lower()
            txt = str(row.get("text") or "").strip()
            if sid == "narrator" and txt:
                has_narrator_dlg = True
                break
    if len(cast) > 1:
        errors.append(
            f"segment {one_based}: at most one cast speaker_id per segment for TTS "
            f"(got {cast!r}); split into separate segments."
        )
    if cast and has_narrator_dlg:
        errors.append(
            f"segment {one_based}: do not mix cast dialogue with narrator dialogue rows "
            "in the same segment—use narrator-only `narration` (empty dialogue) OR cast "
            "`dialogue` only (`narration` is director script, not read aloud)."
        )
    return errors


def validate_one_speaker_per_segment_tts(
    segments: list[Any],
    *,
    long_conversation_mode: bool = False,
    segment_indices: set[int] | None = None,
) -> None:
    """
    Each applicable segment may be heard as narrator-only OR one cast speaker—not both.

    Called at TTS time (``narration-to-mp3`` / preflight), not during Phase 1 merge validation.
    Skips segments where multi-speaker dialogue is allowed (long-conversation ``b_roll``).
    """
    errors: list[str] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        seg_n = seg.get("segment_index")
        one_based = int(seg_n) if isinstance(seg_n, int) and seg_n > 0 else i + 1
        if segment_indices is not None and one_based not in segment_indices:
            continue
        if not segment_expects_one_cast_speaker_per_segment(
            seg, long_conversation_mode=long_conversation_mode
        ):
            continue
        errors.extend(_tts_one_speaker_segment_errors(one_based, seg))
    if errors:
        raise ValueError("One-speaker-per-segment TTS policy failed:\n- " + "\n- ".join(errors))


def validate_speaker_dialogue_content(segments: list[Any]) -> None:
    """
    Raise ValueError when cast dialogue violates per-character content rules.
    Called from Phase 1 validation after structure checks.
    """
    errors: list[str] = []
    for seg_i, row_i, sid, text in _iter_cast_dialogue_lines(segments):
        if sid == "drouillard":
            m = _DROUILLARD_FORBIDDEN.search(text)
            if m:
                errors.append(
                    f"segment {seg_i} dialogue[{row_i}] (drouillard): "
                    f"avoid settlement/colonization boosterism ({m.group(0)!r}); "
                    "prefer scouting, trail hardship, sign/talk, game, or terrain."
                )
    if errors:
        raise ValueError("Phase 1 speaker dialogue content failed:\n- " + "\n- ".join(errors))


# Spoken TTS budget per segment (narration and/or cast dialogue).
# Floor avoids sub-~7s clips; aim band + diversity keep episodes from clustering at the minimum
# (uniform ~5–6s B-roll feels choppy after Wan/Kling stretch-to-audio).
MIN_SPOKEN_NARRATION_WORDS = 18
# Long-conversation cast/talking-head turns may be punchier than narrator VO.
MIN_SPOKEN_CAST_LONG_CONVERSATION = 12
AIM_SPOKEN_NARRATION_WORDS_LOW = 22
AIM_SPOKEN_NARRATION_WORDS_HIGH = 38
MAX_SPOKEN_NARRATION_WORDS = 50
# Whole-episode ceiling on spoken words (narration + dialogue combined), regardless of pack or
# segment count. Calibrated against this pipeline's TTS pacing (~2.4 words/sec observed): 275
# words is ~115s, comfortably under both YouTube's 3-minute Shorts hard limit and the channel's
# own <2-minute target. Enforced in validate_spoken_narration_word_limits below.
MAX_TOTAL_SPOKEN_WORDS = 275
# When an episode has this many spoken segments, enforce the mid-band diversity floor.
_SPOKEN_LENGTH_DIVERSITY_MIN_SEGMENTS = 4
# Default: ≥ half of spoken segments must reach the aim low (documentary / standard dialogue).
LENGTH_DIVERSITY_MIN_FRACTION_DEFAULT = 0.5
# Long conversation: talking-head turns are intentionally punchier; still require some mid-band
# length (≈⅓) so clips do not all park at the floor.
LENGTH_DIVERSITY_MIN_FRACTION_LONG_CONVERSATION = 1.0 / 3.0


def _spoken_narration_word_count(text: str) -> int:
    return len((text or "").split())


def spoken_length_diversity_need(
    spoken_segment_count: int,
    *,
    min_fraction: float = LENGTH_DIVERSITY_MIN_FRACTION_DEFAULT,
) -> int:
    """Minimum number of mid-or-longer spoken segments for diversity (ceil of fraction)."""
    if spoken_segment_count <= 0:
        return 0
    frac = max(0.0, min(1.0, float(min_fraction)))
    return max(1, int(math.ceil(spoken_segment_count * frac)))


def _segment_spoken_text(seg: dict[str, Any]) -> str:
    """Text synthesized for TTS on this segment (narration or cast dialogue)."""
    rows = seg.get("dialogue")
    if isinstance(rows, list):
        parts: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            txt = str(row.get("text") or "").strip()
            if txt:
                parts.append(txt)
        if parts:
            return " ".join(parts)
    return str(seg.get("narration") or "").strip()


def spoken_length_guidance(*, seconds_hint: bool = True) -> str:
    """Compact LENGTH wording for system/user prompts (hard range + aim + vary)."""
    base = (
        f"**{MIN_SPOKEN_NARRATION_WORDS}–{MAX_SPOKEN_NARRATION_WORDS} words** (hard); "
        f"aim **{AIM_SPOKEN_NARRATION_WORDS_LOW}–{AIM_SPOKEN_NARRATION_WORDS_HIGH}** and "
        f"**vary** lengths across segments—do not park every segment near "
        f"{MIN_SPOKEN_NARRATION_WORDS}"
    )
    if seconds_hint:
        return f"{base} (~7–19s TTS; mid band ~10–15s)"
    return base


def segment_has_cast_dialogue(seg: dict[str, Any]) -> bool:
    """True when the segment includes non-narrator cast lines with text."""
    rows = seg.get("dialogue")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("speaker_id") or "").strip().lower()
        txt = str(row.get("text") or "").strip()
        if sid and sid != "narrator" and txt:
            return True
    return False


def validate_spoken_narration_word_limits(
    segments: list[Any],
    *,
    require_length_diversity: bool = True,
    length_diversity_min_fraction: float = LENGTH_DIVERSITY_MIN_FRACTION_DEFAULT,
    min_spoken_cast_words: int | None = None,
) -> None:
    """
    Each segment's spoken TTS content (``narration`` or cast ``dialogue``) must stay in range.
    Director-only ``narration`` on cast/talking_head rows is not checked here.

    Narrator-only spoken VO uses ``MIN_SPOKEN_NARRATION_WORDS``. Cast dialogue uses the same
    floor unless ``min_spoken_cast_words`` is set (long-conversation punchier turns).

    When ``require_length_diversity`` and there are enough spoken segments, a fraction of them
    (default half; long-conversation uses ~⅓) must reach the aim low so episodes do not
    cluster on minimum-length (choppy) clips.

    Also enforces ``MAX_TOTAL_SPOKEN_WORDS`` across all spoken segments combined, independent
    of pack or segment count, so total runtime stays well under YouTube's 3-minute Shorts limit.
    """
    cast_floor = (
        int(min_spoken_cast_words)
        if min_spoken_cast_words is not None
        else MIN_SPOKEN_NARRATION_WORDS
    )
    errors: list[str] = []
    spoken_counts: list[int] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        seg_n = seg.get("segment_index")
        one_based = int(seg_n) if isinstance(seg_n, int) and seg_n > 0 else i + 1
        spoken = _segment_spoken_text(seg)
        if not spoken:
            continue
        wc = _spoken_narration_word_count(spoken)
        spoken_counts.append(wc)
        is_cast = segment_has_cast_dialogue(seg)
        floor = cast_floor if is_cast else MIN_SPOKEN_NARRATION_WORDS
        if wc < floor:
            label = "cast `dialogue`" if is_cast else "spoken content"
            errors.append(
                f"segment {one_based}: {label} must be "
                f"≥{floor} words ({wc} received); expand or merge segments."
            )
        elif wc > MAX_SPOKEN_NARRATION_WORDS:
            label = "cast `dialogue`" if is_cast else "`narration`"
            errors.append(
                f"segment {one_based}: spoken {label} must be "
                f"≤{MAX_SPOKEN_NARRATION_WORDS} words ({wc} received); shorten or split the segment."
            )
    total_words = sum(spoken_counts)
    if total_words > MAX_TOTAL_SPOKEN_WORDS:
        # ~2.4 words/sec observed TTS pace (this pipeline's voices).
        approx_seconds = round(total_words / 2.4)
        errors.append(
            f"total spoken word count across the episode must be ≤{MAX_TOTAL_SPOKEN_WORDS} "
            f"({total_words} received, ~{approx_seconds}s at this pipeline's TTS pace); "
            "shorten segments or reduce segment count to fit the target runtime."
        )
    if (
        require_length_diversity
        and len(spoken_counts) >= _SPOKEN_LENGTH_DIVERSITY_MIN_SEGMENTS
        and not errors
    ):
        mid_or_longer = sum(1 for wc in spoken_counts if wc >= AIM_SPOKEN_NARRATION_WORDS_LOW)
        need = spoken_length_diversity_need(
            len(spoken_counts),
            min_fraction=length_diversity_min_fraction,
        )
        if mid_or_longer < need:
            errors.append(
                f"spoken length diversity: at least {need} of {len(spoken_counts)} segments must be "
                f"≥{AIM_SPOKEN_NARRATION_WORDS_LOW} words (aim "
                f"{AIM_SPOKEN_NARRATION_WORDS_LOW}–{AIM_SPOKEN_NARRATION_WORDS_HIGH}); "
                f"only {mid_or_longer} qualify — lengthen some beats so clips are not uniformly short."
            )
    if errors:
        raise ValueError("Spoken segment word limits failed:\n- " + "\n- ".join(errors))


def validate_speaker_dialogue_rules(segments: list[Any]) -> None:
    """Per-speaker Phase 1 dialogue content checks (not TTS layout; see ``validate_one_speaker_per_segment_tts``)."""
    validate_speaker_dialogue_content(segments)
