#!/usr/bin/env python3
"""
Phase 1 narration generation helpers.

Extracted from generate-narration-two-phase.py so the CLI script can be a thin
orchestrator. This module owns the Phase 1 system/user prompts, tension/urgency
helpers, validation, and OpenAI call wrapper.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from pipeline.llm_provider import call_llm
from pipeline.narration_common import clean_json_reply
from pipeline.narration_visual_mode import (
    MAX_CONSECUTIVE_TALKING_HEAD_LONG,
    has_long_conversation_sustained_exchange,
    parse_long_conversation_mode_config,
    validate_dialogue_visual_modes,
)
from pipeline.phase1_speaker_dialogue_rules import (
    AIM_SPOKEN_NARRATION_WORDS_HIGH,
    AIM_SPOKEN_NARRATION_WORDS_LOW,
    LENGTH_DIVERSITY_MIN_FRACTION_DEFAULT,
    LENGTH_DIVERSITY_MIN_FRACTION_LONG_CONVERSATION,
    MAX_SPOKEN_NARRATION_WORDS,
    MAX_TOTAL_SPOKEN_WORDS,
    MIN_SPOKEN_CAST_LONG_CONVERSATION,
    MIN_SPOKEN_NARRATION_WORDS,
    spoken_length_guidance,
    validate_speaker_dialogue_rules,
    validate_spoken_narration_word_limits,
)
from pipeline.phase1_title_hook import (
    TITLE_LOOKBACK,
    TITLE_REWRITE_MAX_RETRIES,
    TITLE_REWRITE_MAX_TOKENS,
    TITLE_REWRITE_SYSTEM,
    accept_rewritten_title,
    build_title_rewrite_user_prompt,
    parse_rewritten_title,
    title_rewrite_reason,
    title_stem,
)
from pipeline.pipeline_profile import Phase1UserPromptFields, lewis_clark_phase1_user_defaults
from pipeline.prompt_pack_paths import packs_root

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TENSION_ARCS_PATH = _REPO_ROOT / "config" / "narration_tension-arcs.json"

MAX_RETRIES = 4
DEFAULT_MAX_TOKENS = 8192


def _resolve_tension_context(date_id: str) -> tuple[str | None, float]:
    """Return (tension_prompt, tension_weight_0_to_1) for this date.

    - Uses narration_tension-arcs.json expedition_milestones.
    - For each arc, derives a piecewise-linear weight based on distance to end_date:
      * 0 before the window
      * ramp from ~0.25 up to 1.0 between start_date and end_date
      * linear decay from 1.0 back to 0 over 4 days after end_date
    - Returns the prompt from the strongest arc (if any) plus the max weight across arcs.
    """
    if not _TENSION_ARCS_PATH.exists():
        return (None, 0.0)
    try:
        data = json.loads(_TENSION_ARCS_PATH.read_text(encoding="utf-8"))
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
            # Before this milestone window: no contribution.
            weight = 0.0
        elif start <= current <= end:
            # Approach ramp: floor at 0.25 up to 1.0 on end_date.
            total_days = max(1, (end - start).days)
            progressed = (current - start).days
            frac = min(1.0, max(0.0, progressed / total_days))
            weight = 0.25 + 0.75 * frac
            if current == end:
                prompt = arc.get("arrival_release_prompt")
            else:
                prompt = arc.get("approach_prompt")
        elif end < current <= end + timedelta(days=4):
            # Post-event decay over 4 days.
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


def _tension_weight_to_urgency(weight: float) -> str:
    """Map tension weight (0–1) to a discrete urgency level for the prompt."""
    if weight >= 0.7:
        return "high"
    if weight >= 0.3:
        return "moderate"
    return "low"


def _urgency_instruction(urgency: str) -> str:
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


def _macro_event_editorial_priority(entry_text: str) -> str | None:
    """
    When extracted journal text suggests diplomacy or intertribal conflict stakes, append a
    user-prompt reminder so the model applies system prompt §3b (macro-event through-line).
    """
    t = re.sub(r"\s+", " ", (entry_text or "").strip().lower())
    if len(t) < 15:
        return None
    blurb = (
        "EDITORIAL PRIORITY: This entry foregrounds diplomacy, intertribal relations, or conflict prevention. "
        "Follow the MACRO-EVENT / STAKES THROUGH-LINE rules (system prompt §3b): at least half of middle segments "
        "(after hook, before reflection) must advance that main story. Do not let generic river, firelight, or "
        "anonymous camp mood displace it. Named expedition cast (e.g. Seaman, York) may still appear where fitting—"
        "they do not replace the required stake coverage."
    )
    stakes = re.search(
        r"\b(war|wars|warrior|warriors|attack|attacks|raid|raids|hostilities|hostile|"
        r"treaty|truce|council|councils|parley|mediate|mediation|diplomacy|negotiate|negotiation|"
        r"interpreter|interpreters|intervene|intervention|ambush|battle|delegation)\b",
        t,
    )
    prevention = (
        re.search(
            r"\b(stop|stops|stopping|prevent|preventing|dissuade|avert|forestall)\b.{0,160}\b(war|wars|raid|raids|attack|attacks|fighting)\b",
            t,
        )
        or re.search(
            r"\b(war|wars|raid|raids|attack)\b.{0,160}\b(against|prevent|stop|avert)\b",
            t,
        )
        or re.search(r"\bgoing\s+to\s+war\b", t)
    )
    peoples = re.search(
        r"\b(osage|osarges|osarg|kickapoo|kickpo|sac|sauk|shawnee|sioux|"
        r"teton|mandan|hidatsa|blackfeet|shoshone|nez\s*perce|nezperce|"
        r"piegans|tribe|tribes|native\s+american|american\s+indian)\b",
        t,
    )
    if peoples and (stakes or prevention):
        return blurb
    if prevention and re.search(r"\b(110|\d+\s+men|party|warrior|chief|nation)\b", t):
        return blurb
    return None


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def build_phase1_system_prompt(
    repo_root: Path | None = None,
    prompt_pack: str = "lewis_clark",
) -> str:
    """Load Phase 1 system prompt from prompt_packs/<pack>/phase1_system.txt."""
    root = repo_root or _default_repo_root()
    path = packs_root(root) / prompt_pack / "phase1_system.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Missing Phase 1 system prompt for pack {prompt_pack!r}: {path}")
    return path.read_text(encoding="utf-8").strip()


def _dialogue_speaker_ids_allowed() -> frozenset[str]:
    try:
        from pipeline.narration_characters import load_characters

        return frozenset(c.id for c in load_characters() if getattr(c, "id", None)) | frozenset(
            {"narrator"}
        )
    except Exception:
        return frozenset({"narrator"})


_PACK_DIALOGUE_STANDARD = "lewis_clark_dialogue"
_PACK_DIALOGUE_LONG = "lewis_clark_long_conversation"


def build_phase1_user_prompt(
    entry_text: str,
    date_id: str,
    entry_author: str | None = None,
    focus_topic: str | None = None,
    editorial_notes: str | None = None,
    *,
    phase1_user: Phase1UserPromptFields | None = None,
    dialogue_mode: bool = False,
    conversation_mode: dict[str, Any] | None = None,
    prompt_pack: str = "lewis_clark",
    recent_episodes_digest: str | None = None,
    week_arc_prompt: str | None = None,
) -> str:
    style = phase1_user or lewis_clark_phase1_user_defaults()
    pack = (prompt_pack or "").strip() or "lewis_clark"
    opening = style.user_opening_template.format(date_id=date_id)
    parts: list[str] = [opening, ""]

    total_words_note = (
        f"HARD CAP: total spoken words across the whole episode (narration + dialogue combined) "
        f"must be **≤{MAX_TOTAL_SPOKEN_WORDS} words** (~115s at this pipeline's TTS pace, well under "
        "YouTube's 3-minute Shorts limit and this channel's own <2-minute target). This governs over "
        "any segment-count target below—if hitting the segment count would exceed the word cap, use "
        "fewer segments or trim toward the low end of each segment's word range instead."
    )
    if dialogue_mode:
        if pack == _PACK_DIALOGUE_LONG:
            lc = parse_long_conversation_mode_config(conversation_mode)
            parts.append(
                "LENGTH: Target **8–11 segments** total (long-conversation mode). Plan one **bounded conversation "
                "micro-arc** (open → body → close or interrupt) between **two** roster speakers—not always "
                "Lewis and Clark—then bookend with `b_roll` for expedition action."
            )
            parts.append(total_words_note)
            parts.append(
                "HARD VALIDATION: Output is rejected unless it contains a sustained two-speaker exchange: "
                f"at least {lc['min_segments']} consecutive eligible segments whose **union** of non-narrator "
                "speakers is exactly two roster ids. **`talking_head`:** one speaker per clip, "
                f"exactly {lc['max_lines_talking_head']} cast dialogue row per clip (multi-sentence OK), "
                "alternating faces (no back-to-back same `talking_head_subject`). **`b_roll` dialogue:** "
                f"≥{lc['min_lines_b_roll']} strictly alternating cast lines. "
                "The exchange must **end** in-dialogue (close/interrupt)—do not drift into corps commands inside the run. "
                "Keep the ping-pong run to **4–6 clips**—enough to satisfy the sustained-exchange minimum without "
                "blowing the total word cap above."
            )
        elif pack == _PACK_DIALOGUE_STANDARD:
            parts.append(
                "LENGTH: standard dialogue episode budget (about 7–9 segments). Keep **spoken** content "
                f"at {spoken_length_guidance()} per segment "
                "(narrator-only `narration` or cast `dialogue`; TTS reads it aloud). Limit **character-dialogue** "
                "beats to **at most 3 segments** total. Narrator-led segments should carry the rest (roughly 4+ segments)."
            )
            parts.append(total_words_note)
        else:
            parts.append("LENGTH: Follow the Phase 1 system prompt segment budget.")
            parts.append(total_words_note)
    else:
        parts.append(
            f"LENGTH: 7–10 segments (~65–100s). Each narration {spoken_length_guidance()} "
            "(TTS reads every segment verbatim; hard validation + length diversity)."
        )
        parts.append(total_words_note)

    parts.append("")

    if style.use_tension_arcs:
        tension_prompt, tension_weight = _resolve_tension_context(date_id)
        if tension_prompt is not None and tension_weight > 0:
            urgency = _tension_weight_to_urgency(tension_weight)
            instruction = _urgency_instruction(urgency)
            parts.append("EXPEDITION MILESTONE (this date):")
            parts.append(f"- {tension_prompt}")
            parts.append(f"URGENCY: {urgency}. {instruction}")
            parts.append("")

    if (recent_episodes_digest or "").strip():
        parts.append((recent_episodes_digest or "").strip())
        parts.append("")

    if (week_arc_prompt or "").strip():
        parts.append((week_arc_prompt or "").strip())
        parts.append("")

    if focus_topic:
        if style.focus_topic_compares_1803:
            focus_blurb = (
                f"FOCUS TOPIC: {focus_topic}. Structure the narration in four parts:\n"
                "1. FIRST SEGMENT: Hook — generate excitement about the focus topic so the viewer is eager for the deep dive. Then briefly frame the day (e.g. a sparse entry, a routine day, or a blank page) so it's clear why we're leaning on the theme.\n"
                "2. SECOND SEGMENT: Brief third-person overview of the journal entry (what was recorded, or that little was recorded). This makes it explicit that the day is repetitive or blank-page before the theme takes over.\n"
                f"3. MIDDLE 6–10 SEGMENTS: First-person deep dive comparing {focus_topic} in 1803 vs today. Keep the 1800s setting; use period technology as a visual metaphor for modern concepts.\n"
                "4. FINAL SEGMENT: Return to third-person and the expedition's legacy."
            )
        else:
            focus_blurb = (
                f"FOCUS TOPIC: {focus_topic}. Weave this theme through the episode while staying faithful to the source; "
                "do not contradict the source. Use a strong hook, sustained development, and a reflective close."
            )
        parts.append(focus_blurb)
        parts.append("")

    if style.use_expedition_user_notes:
        if dialogue_mode:
            is_long = pack == _PACK_DIALOGUE_LONG
            lc_user = parse_long_conversation_mode_config(conversation_mode) if is_long else None
            th_pacing = (
                "NARRATOR-FIRST PACING: Keep the **first two segments** as `visual_mode`: `b_roll` with **narration-only** "
                "(empty `dialogue` [] or omit `dialogue`). Do not use `talking_head` before segment 3. "
                "**No spoiler VO:** segments 1–2 must **not** narrate outcomes the centerpiece conversation will decide "
                "(plans, who stays, boats left behind)—only situation + prep + tension. Reveal outcomes in dialogue or "
                "in `narration` **after** the micro-arc. For the centerpiece "
                f"exchange you may run up to **{MAX_CONSECUTIVE_TALKING_HEAD_LONG}** consecutive alternating `talking_head` "
                "segments (any two roster speakers ping-pong); `b_roll` before/after the micro-arc and for hook/reflection."
                if is_long
                else "NARRATOR-FIRST PACING: Keep the **first two segments** as `visual_mode`: `b_roll` with **narration-only** "
                "(empty `dialogue` [] or omit `dialogue`) so the episode opens in documentary voice + B-roll. "
                "Do not use `talking_head` before segment 3. Do not chain more than **two** `talking_head` segments in a row—"
                "return to `b_roll` between character moments."
            )
            th_lines_hint = (
                "When `visual_mode` is `talking_head`: one on-camera `speaker_id` per segment; **exactly one** cast "
                "`dialogue` row per clip (multi-sentence OK). Each line speaks **to the other participant**, not "
                "generic orders to off-screen men—each segment should **answer** the previous speaker. "
                "Close or interrupt the micro-arc before switching to expedition action in `narration`."
                if is_long and lc_user
                else (
                    "When `visual_mode` is `talking_head`: set `talking_head_subject` to the single `speaker_id` shown on camera "
                    "(must match a portrait under character-portraits/, not animals). Include **only** that speaker in `dialogue` "
                    "(one or more lines, all the same `speaker_id`)."
                )
            )
            parts.extend(
                [
                    "DIRECTOR'S NOTE (character dialogue mode): Use your full historical knowledge of the Corps of Discovery. Expand brief journal mentions into accurate figures and objects (Seaman, York, boats, named enlisted men) when appropriate for this date.",
                    "VISUAL_MODE (per segment, string): `b_roll` (default) = Wan environment/scene clips. `talking_head` = FAL audio-driven close-up of one speaker.",
                    th_pacing,
                    "ONE SPEAKER PER SEGMENT (TTS, mandatory): Each segment is heard as **either** documentary narrator **or** **one** cast member—not both. "
                    "**Narrator-only:** non-empty `narration`, `dialogue` omitted or `[]`—only the narrator is synthesized. "
                    "**Cast-only:** one roster `speaker_id` in `dialogue[]` (all lines that speaker); `narration` is director/Phase 2 script only and is **not** read aloud when that speaker has a pipeline voice. "
                    "Never put narrator bridge lines in `dialogue[]` in the same segment as expedition cast lines—split segments. At most **one** distinct cast `speaker_id` per segment.",
                    th_lines_hint,
                    (
                        " **TTS:** `talking_head` and cast-only `b_roll` segments are **gap + dialogue only** (no lead-in from `narration`). "
                        "FAL OmniHuman v1.5 reads merged **`talking_head_prompt`** as avatar motion (still + segment MP3). "
                        "**`talking_head_prompt`** (mandatory, 2–4 sentences): **(1) camera**—steady or slow push; "
                        "**(2) emotion**; **(3) speaks/talks** with pre-speech neutral mouth; **(4) one restrained gesture**; "
                        "**(5) post-speech settle**—jaw softens during trailing silence."
                    ),
                    "Each segment MUST include string `narration`: non-empty. **Narrator-only** segments (empty cast "
                    f"`dialogue`): spoken VO — {spoken_length_guidance()} "
                    "(past-tense). **Cast / talking_head**: spoken `dialogue` in the same range; director "
                    "`narration` for Phase 2 (30–120 words OK; not spoken when cast lines carry the beat). Spell out ranks in "
                    "full in spoken `text` for TTS.",
                    "CHRONOLOGY: Respect date_id—do not give lines to people not yet on the expedition (see system prompt). Seaman does not speak as dialogue; he may be described in `narration` or by others.",
                    "MULTI-AUTHOR DAYS: If the journal body uses bracketed headers like `[Author Name]` or JOURNAL AUTHOR lists multiple writers, treat each block as a distinct voice—rotate `dialogue` across matching roster `speaker_id`s when the source gives them substance; do not attribute everything to Clark/Lewis by default.",
                    (
                        "CAST / CAMERA (long-conversation): **Default** `conversation_micro_arc.speakers` to **lewis+clark** "
                        "when the journal supports captain dialogue. Use a **different** roster pair only when (a) "
                        "DIVERSITY HINTS ask for rotation **and** today's journal **explicitly pairs** those two in one "
                        "shared scene, or (b) the journal names a non-captain pair together for the centerpiece beat "
                        "(joint walk, council, repair)—not from separate `[Author]` blocks alone. "
                        "Corps-wide orders and camp action belong in `b_roll`/`narration` **outside** the ping-pong run."
                        if is_long
                        else "CAST / CAMERA: Lewis and Clark stay the primary voices; when the journal clearly gives a beat to another named roster member, use matching `speaker_id` / `talking_head_subject`. Avoid a fixed Lewis→Clark→Ordway three-beat treadmill with generic filler unless the text supports it."
                    ),
                    "NARRATION VS CAST: If `narration` in this or the prior segment already explained a place (river, ford, landmark), cast `dialogue` must not parrot the same travelogue—add a different in-scene layer (task, order, worry, consequence) while staying factual.",
                    "VIDEO ALIGNMENT: Keep `narration` friendly to environment B-roll when the journal is about seeing landforms or distance; use `talking_head` for interpersonal or first-person beats that benefit from a face on camera.",
                    "",
                ]
            )
            convo_cfg = conversation_mode or {}
            if bool(convo_cfg.get("enabled")):
                if pack == _PACK_DIALOGUE_LONG:
                    lc = parse_long_conversation_mode_config(convo_cfg)
                    parts.extend(
                        [
                            "CONVERSATION BEAT (enabled): One **bounded micro-arc** inside the episode—**open → body → close or interrupt**.",
                            (
                                f"- **Two** roster speakers (journal-driven), **≥{lc['min_segments']}** consecutive alternating "
                                "`talking_head` segments when portraits allow; one cast `dialogue` row per clip."
                            ),
                            "- **Open:** first turn states why they are talking. **Body:** each turn responds to the last. "
                            "**Close/interrupt:** final turn(s) settle or break off—then `b_roll` for party orders, travel, etc.",
                            "- Do **not** drift from dyad talk into both speakers barking orders at the men without a close and `b_roll` bridge.",
                            "- Use **`conversation_tracking.continuity_carry`** for the open thread; mark closure on the last turn(s).",
                            "- Emit **`conversation_micro_arc`** with **`shared_setting`** (fixed backdrop), **`speakers`**, and **`segment_indices`** covering the full ping-pong run; keep **`setting_state`** aligned on every turn.",
                            "",
                        ]
                    )
                else:
                    min_segments = int(convo_cfg.get("min_segments", 2) or 2)
                    min_lines = int(convo_cfg.get("min_lines_per_segment", 4) or 4)
                    min_segments = max(1, min_segments)
                    min_lines = max(2, min_lines)
                    parts.extend(
                        [
                            "CONVERSATION BEAT (enabled for this run): Include one sustained two-character exchange as a centerpiece when the journal supports it.",
                            (
                                f"- Use exactly two expedition speakers (non-`narrator`) across at least {min_segments} consecutive "
                                f"middle segments, with at least {min_lines} dialogue lines per segment."
                            ),
                            "- Keep this exchange in `b_roll` segments so speaker turns can alternate naturally; talking_head remains one-speaker-only per segment.",
                            "- Prefer this exchange over generic reflective camp chatter. Keep facts tied to this date's events and constraints.",
                            "",
                        ]
                    )
        else:
            parts.extend(
                [
                    "DIRECTOR'S NOTE: Use your full historical knowledge of the Corps of Discovery. Expand brief journal mentions of 'the dog', 'the boat', 'the men', or 'the camp' into vivid, historically accurate descriptions (e.g. Seaman, the Keelboat, specific personnel) as appropriate for this date.",
                    "AUTHORITY NOTE: The source journal text is often first-person. Treat first-person references ('I', 'we', 'my') as coming from the named journal author, and rewrite narration in third person using that author's name when the journal clearly attributes an action or decision to that author.",
                    "MULTI-AUTHOR SOURCES: When the journal body includes distinct bracketed `[Name]` sections for the same calendar date, preserve that split—attribute beats to the recorder the source implies for each passage, not only under the JOURNAL AUTHOR primary name.",
                    "SPARING AUTHOR NAMES: The expedition is a team effort. Do NOT mention the journal author by name in every segment. If the journal describes shared work, camp routines, or group observations, use 'the party', 'the expedition', or 'the men' instead of repeating the author. Only use the author's name for actions that are clearly theirs (e.g. a decision they explicitly made, a measurement they personally took).",
                    "VIDEO ALIGNMENT: When a segment is mainly about **seeing** landscape, earthworks, mounds, distant terrain, sky, or river ice—word it so B-roll can show **that subject**. Avoid 'Lewis/Clark spots/notices/sees [landmark]' unless the human figure must be on screen; prefer neutral expedition phrasing or 'the journal records...' so the picture is the landmark, not a portrait.",
                    "",
                ]
            )

    if style.use_journal_author_line:
        auth_line = (
            f"JOURNAL AUTHOR: {entry_author.strip()}"
            if entry_author and entry_author.strip()
            else "JOURNAL AUTHOR: Unknown (infer cautiously from expedition context)"
        )
        parts.extend([auth_line, ""])

    if pack == _PACK_DIALOGUE_LONG and (entry_text or "").strip():
        from pipeline.conversation_dyad import (
            build_journal_dyad_phase1_block,
            detect_journal_explicit_dyads,
        )

        journal_dyads = detect_journal_explicit_dyads(entry_text)
        block = build_journal_dyad_phase1_block(journal_dyads)
        if block:
            parts.extend([block, ""])

    parts.extend([style.source_body_heading, entry_text.strip()])

    if (editorial_notes or "").strip():
        parts.extend(["", style.tei_notes_heading, editorial_notes.strip()])
    if style.use_macro_event_editorial:
        priority = _macro_event_editorial_priority(entry_text)
        if priority:
            parts.extend(["", priority])
    return "\n\n".join(parts)


def _recent_episode_titles(repo_root: Path, date_id: str) -> list[str]:
    from pipeline.recent_episode_diversity import load_prior_episode_metas

    narr = Path(repo_root) / "narrations"
    if not narr.is_dir():
        return []
    metas = load_prior_episode_metas(narr, date_id, limit=TITLE_LOOKBACK)
    titles: list[str] = []
    for m in metas:
        t = str(m.get("title") or "").strip()
        if t:
            titles.append(t)
    return titles


def refine_phase1_title(
    parsed: dict[str, Any],
    *,
    date_id: str,
    repo_root: Path,
    prompt_pack: str,
    model: str,
    provider: str = "openai",
) -> dict[str, Any]:
    """If the finished episode title is generic or reused, ask a short rewrite prompt."""
    from pipeline.recent_episode_diversity import is_lewis_clark_prompt_pack

    if not is_lewis_clark_prompt_pack(prompt_pack):
        return parsed
    recent_titles = _recent_episode_titles(repo_root, date_id)
    recent_stems = [title_stem(t) for t in recent_titles]
    reason = title_rewrite_reason(str(parsed.get("title") or ""), recent_title_stems=recent_stems)
    if not reason:
        return parsed
    user = build_title_rewrite_user_prompt(parsed, reason=reason, recent_titles=recent_titles)
    last_raw = ""
    for _attempt in range(TITLE_REWRITE_MAX_RETRIES + 1):
        last_raw = _call_api(
            TITLE_REWRITE_SYSTEM,
            user,
            model,
            max_tokens=TITLE_REWRITE_MAX_TOKENS,
            provider=provider,
        )
        candidate = accept_rewritten_title(
            parse_rewritten_title(last_raw),
            recent_title_stems=recent_stems,
        )
        if candidate:
            old = str(parsed.get("title") or "")
            parsed["title"] = candidate
            print(
                f"[Phase 1] Title rewrite ({reason}): {old!r} -> {candidate!r}",
                file=sys.stderr,
            )
            return parsed
        user = (
            "That title was still invalid (generic, reused, or not 2–8 words). "
            'Return ONLY JSON: {"title": "..."}.\n\n'
            f"{last_raw}"
        )
    print(
        f"[WARN] Title rewrite failed ({reason}); keeping {parsed.get('title')!r}",
        file=sys.stderr,
    )
    return parsed


def _validate_phase1(
    parsed: dict,
    *,
    dialogue_mode: bool = False,
    prompt_pack: str = "lewis_clark",
    conversation_mode: dict[str, Any] | None = None,
    require_talking_head: bool = False,
) -> None:
    if not isinstance(parsed, dict):
        raise ValueError("Phase 1 JSON must be an object")
    if "title" not in parsed or not isinstance(parsed.get("title"), str):
        raise ValueError("Phase 1 must have string 'title'")
    segments = parsed.get("segments")
    if not isinstance(segments, list) or len(segments) == 0:
        raise ValueError("Phase 1 must have non-empty 'segments' array")
    pack_id = (prompt_pack or "").strip() or "lewis_clark"
    if dialogue_mode and pack_id == _PACK_DIALOGUE_STANDARD:
        char_dialogue_segments = 0
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            rows = seg.get("dialogue")
            if not isinstance(rows, list):
                continue
            has_character_line = any(
                isinstance(r, dict)
                and str(r.get("speaker_id") or "").strip().lower() not in ("", "narrator")
                and str(r.get("text") or "").strip()
                for r in rows
            )
            if has_character_line:
                char_dialogue_segments += 1
        if char_dialogue_segments > 3:
            raise ValueError(
                f"Phase 1 lewis_clark_dialogue allows character dialogue in at most 3 segments "
                f"({char_dialogue_segments} received); use long-conversation mode for denser dialogue runs."
            )
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise ValueError(f"Phase 1 segment {i} must be an object")
        if "narration" not in seg or not isinstance(seg.get("narration"), str):
            raise ValueError(f"Phase 1 segment {i} must have string 'narration'")
        if "segment_type" not in seg:
            raise ValueError(f"Phase 1 segment {i} must have 'segment_type'")
    diversity_fraction = LENGTH_DIVERSITY_MIN_FRACTION_DEFAULT
    cast_min: int | None = None
    if dialogue_mode and pack_id == _PACK_DIALOGUE_LONG:
        diversity_fraction = LENGTH_DIVERSITY_MIN_FRACTION_LONG_CONVERSATION
        cast_min = MIN_SPOKEN_CAST_LONG_CONVERSATION
    validate_spoken_narration_word_limits(
        segments,
        length_diversity_min_fraction=diversity_fraction,
        min_spoken_cast_words=cast_min,
    )
    if dialogue_mode:
        allowed = _dialogue_speaker_ids_allowed()
        max_th = MAX_CONSECUTIVE_TALKING_HEAD_LONG if pack_id == _PACK_DIALOGUE_LONG else None
        max_th_lines = None
        if pack_id == _PACK_DIALOGUE_LONG:
            lc_val = parse_long_conversation_mode_config(conversation_mode)
            max_th_lines = lc_val["max_lines_talking_head"]
        validate_dialogue_visual_modes(
            segments,
            allowed_speakers=allowed,
            max_consecutive_talking_head=max_th,
            max_talking_head_cast_lines=max_th_lines,
            require_talking_head=require_talking_head,
        )
        validate_speaker_dialogue_rules(segments)
    if dialogue_mode and pack_id == _PACK_DIALOGUE_LONG:
        convo_cfg = conversation_mode or {}
        convo_enabled = bool(convo_cfg.get("enabled", True))
        if convo_enabled:
            lc = parse_long_conversation_mode_config(convo_cfg)
            if not has_long_conversation_sustained_exchange(
                segments,
                min_segments=lc["min_segments"],
                min_lines_per_segment=lc["min_lines_talking_head"],
                min_lines_talking_head=lc["min_lines_talking_head"],
                min_lines_b_roll=lc["min_lines_b_roll"],
                require_talking_head_in_window=require_talking_head,
            ):
                extra = (
                    " The week-arc plan for this day requires at least one `talking_head` segment "
                    "**inside** the sustained exchange itself (not just anywhere in the episode): "
                    "an all-`b_roll` exchange plus an unrelated talking_head clip elsewhere does not satisfy it."
                    if require_talking_head
                    else ""
                )
                raise ValueError(
                    "Phase 1 long-conversation mode requires a sustained two-speaker exchange in consecutive "
                    "eligible segments: `b_roll` beats must **alternate** non-narrator `speaker_id` lines (A/B/A/B); "
                    "`talking_head` clips must **alternate** `talking_head_subject` with short turns "
                    f"(exactly one cast dialogue row per talking_head clip). "
                    f"min_segments={lc['min_segments']}, min_lines_b_roll={lc['min_lines_b_roll']}."
                    + extra
                )
            from pipeline.conversation_visual_spine import validate_conversation_micro_arc

            validate_conversation_micro_arc(parsed, len(segments))
    valid_registers = ("light", "warm", "reflective", "tense", "somber")
    tr = parsed.get("tone_register")
    if tr not in valid_registers:
        raise ValueError(f"Phase 1 must have tone_register one of {valid_registers!r}, got {tr!r}")


def _strip_deprecated_phase1_fields(parsed: dict[str, Any]) -> None:
    """Remove legacy keys the pipeline no longer uses (keeps voice sidecar / Phase 2 input clean)."""
    parsed.pop("episode_director_notes", None)
    for seg in parsed.get("segments") or []:
        if isinstance(seg, dict):
            seg.pop("director_notes", None)


def _call_api(
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    provider: str = "openai",
) -> str:
    return call_llm(provider, system_prompt, user_prompt, model, max_tokens, purpose="phase1")


def run_phase1(
    entry_text: str,
    date_id: str,
    entry_author: str | None = None,
    model: str = "gpt-4o",
    focus_topic: str | None = None,
    max_retries: int = MAX_RETRIES,
    editorial_notes: str | None = None,
    *,
    repo_root: Path | None = None,
    prompt_pack: str = "lewis_clark",
    phase1_user: Phase1UserPromptFields | None = None,
    dialogue_mode: bool = False,
    conversation_mode: dict[str, Any] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    recent_episodes_digest: str | None = None,
    week_arc_prompt: str | None = None,
    require_talking_head: bool = False,
    provider: str = "openai",
) -> dict[str, Any]:
    root = repo_root or _default_repo_root()
    system = build_phase1_system_prompt(repo_root=root, prompt_pack=prompt_pack)
    user = build_phase1_user_prompt(
        entry_text,
        date_id,
        entry_author,
        focus_topic,
        editorial_notes=editorial_notes,
        phase1_user=phase1_user,
        dialogue_mode=dialogue_mode,
        conversation_mode=conversation_mode,
        prompt_pack=prompt_pack,
        recent_episodes_digest=recent_episodes_digest,
        week_arc_prompt=week_arc_prompt,
    )
    base_user = user
    last_raw = None
    last_error: JSONDecodeError | ValueError | None = None
    parsed: dict[str, Any] | None = None
    for attempt in range(max_retries + 1):
        raw = _call_api(system, user, model, max_tokens=max_tokens, provider=provider)
        cleaned = clean_json_reply(raw)
        last_raw = raw
        try:
            parsed = json.loads(cleaned)
            _validate_phase1(
                parsed,
                dialogue_mode=dialogue_mode,
                prompt_pack=prompt_pack,
                conversation_mode=conversation_mode,
                require_talking_head=require_talking_head,
            )
            _strip_deprecated_phase1_fields(parsed)
            break
        except (JSONDecodeError, ValueError) as e:
            parsed = None
            last_error = e
            if attempt >= max_retries:
                break
            diversity_hint = "half"
            spoken_hint = f"{MIN_SPOKEN_NARRATION_WORDS}-{MAX_SPOKEN_NARRATION_WORDS} words (count every word)"
            if (prompt_pack or "").strip() == _PACK_DIALOGUE_LONG:
                diversity_hint = "about one third"
                spoken_hint = (
                    f"narrator-only VO {MIN_SPOKEN_NARRATION_WORDS}-{MAX_SPOKEN_NARRATION_WORDS} words; "
                    f"cast dialogue ≥{MIN_SPOKEN_CAST_LONG_CONVERSATION} and "
                    f"≤{MAX_SPOKEN_NARRATION_WORDS} words"
                )
            user = (
                f"{base_user}\n\n"
                "----------------------------------------------------------------\n"
                "RETRY: The previous response was invalid or did not match required constraints.\n"
                f"Validation error: {e}\n"
                "Fix ONLY the reported issues, using the JOURNAL ENTRY and instructions above as source. "
                "Each spoken segment must be "
                f"{spoken_hint}; "
                f"aim {AIM_SPOKEN_NARRATION_WORDS_LOW}-{AIM_SPOKEN_NARRATION_WORDS_HIGH} and vary lengths "
                f"so at least {diversity_hint} of segments are "
                f"≥{AIM_SPOKEN_NARRATION_WORDS_LOW} words.\n"
                "Return ONLY valid JSON with the exact required structure.\n\n"
                f"Previous (invalid) reply:\n{cleaned}"
            )
    if parsed is None:
        err_kind = (
            "valid JSON" if isinstance(last_error, JSONDecodeError) else "required constraints"
        )
        print(f"[ERROR] Phase 1 failed to produce {err_kind}", file=sys.stderr)
        print(f"Date ID: {date_id}", file=sys.stderr)
        if last_error is not None:
            print(f"Last error: {last_error}", file=sys.stderr)
        if last_raw:
            print("----- Raw reply begin -----", file=sys.stderr)
            print(last_raw[:2000], file=sys.stderr)
            print("----- Raw reply end -------", file=sys.stderr)
        raise RuntimeError(
            f"Phase 1 failed after {max_retries + 1} attempts for date {date_id}"
            + (f": {last_error}" if last_error is not None else "")
        )
    return parsed
