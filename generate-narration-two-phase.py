#!/usr/bin/env python3
"""
Two-phase narration generation: Phase 1 produces narration + metadata (no visuals);
with `--dialogue` or `--long-conversation`, a phase1-dialogue pass polishes `dialogue[].text` using
`prompt_packs/<pack>/phase1_dialogue_system.txt`, then Phase 2 produces a visual plan
(scene_plan, video_metadata, map_insertions, audio_design).
Results are merged into the pipeline-shaped JSON (title, scene_spine, narration_script)
for use by narration-to-mp3 and narration-to-video.
"""

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import argparse
import json
import os
import re
import sys
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from openai import OpenAI

from pipeline.llm_provider import DEFAULT_MODEL_BY_PROVIDER
from pipeline.narration_characters import assign_reference_character_hints, update_character_usage
from pipeline.narration_common import (
    apply_prompt_replacements,
    get_default_visual_style,
    load_narration_config,
    recent_visual_style_names,
    select_visual_style,
    validate_narration_schema,
)
from pipeline.narration_phase1 import refine_phase1_title
from pipeline.narration_phase1_dialogue import run_phase1_dialogue
from pipeline.narration_phase2 import (
    build_visual_sidecar_document,
    build_voice_sidecar_document,
)
from pipeline.narration_utils import enforce_narrator_only_for_style_policy
from pipeline.pipeline_profile import default_profile_path, load_pipeline_profile
from pipeline.prompt_pack_metadata import build_prompt_pack_lineage
from pipeline.source_episode import load_episode_source_bundle
from pipeline_logging import log_file_created

_repo_root = Path(__file__).resolve().parent

# theme_engine focus topic (optional)
try:
    _repo_root = Path(__file__).resolve().parent
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))
    from theme_engine.theme_selector import (
        recommend as theme_recommend,
    )
    from theme_engine.theme_selector import (
        record_narration as theme_record_narration,
    )
    from theme_engine.theme_selector import (
        remove_date_from_state as theme_remove_date_from_state,
    )
except ImportError:
    theme_recommend = None
    theme_record_narration = None
    theme_remove_date_from_state = None

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MAX_RETRIES = 4
DEFAULT_MAX_TOKENS = 8192


def _write_latest_shortcut(shortcut_name: str, target: Path) -> None:
    """Write a small .url file in the repo root pointing at target.

    This gives a clickable shortcut (latest narration / video) without copying large files
    or requiring Windows symlink permissions.
    """
    try:
        root = Path(__file__).resolve().parent
        shortcut_path = root / shortcut_name
        url = f"file:///{target.resolve().as_posix()}"
        contents = f"[InternetShortcut]\nURL={url}\n"
        shortcut_path.write_text(contents, encoding="utf-8")
    except OSError:
        # Non-fatal: narrations still written even if shortcut update fails.
        pass


def run_phase1(
    entry_text: str,
    date_id: str,
    entry_author: str | None = None,
    model: str = "gpt-4o",
    focus_topic: str | None = None,
    max_retries: int = MAX_RETRIES,
    editorial_notes: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Delegates to pipeline.narration_phase1.run_phase1 (kwargs: repo_root, prompt_pack, phase1_user, dialogue_mode)."""
    from pipeline.narration_phase1 import run_phase1 as _run

    return _run(
        entry_text=entry_text,
        date_id=date_id,
        entry_author=entry_author,
        model=model,
        focus_topic=focus_topic,
        max_retries=max_retries,
        editorial_notes=editorial_notes,
        **kwargs,
    )


def run_phase2(
    phase1: dict[str, Any],
    date_id: str,
    model: str = "gpt-4o",
    style: dict[str, str] | None = None,
    focus_topic: str | None = None,
    max_retries: int = MAX_RETRIES,
    user_suffix: str | None = None,
    character_anchor_hints: list[str | None] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Delegates to pipeline.narration_phase2.run_phase2 (kwargs: focus_expedition_era, historical_only_visuals, seasonal_ambient_from_date_id)."""
    from pipeline.narration_phase2 import run_phase2 as _run

    return _run(
        phase1=phase1,
        date_id=date_id,
        model=model,
        style=style,
        focus_topic=focus_topic,
        max_retries=max_retries,
        user_suffix=user_suffix,
        character_anchor_hints=character_anchor_hints,
        **kwargs,
    )


def merge_phase1_phase2(
    phase1: dict[str, Any],
    phase2: dict[str, Any],
    style: dict[str, str] | None = None,
    character_config: dict[str, Any] | None = None,
    date_id: str = "",
    character_anchor_hints: list[str | None] | None = None,
    dialogue_mode: bool = False,
    long_conversation_mode: bool = False,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Backwards-compat wrapper that delegates to pipeline.narration_phase2.merge_phase1_phase2."""
    from pipeline.narration_phase2 import merge_phase1_phase2 as _merge

    return _merge(
        phase1=phase1,
        phase2=phase2,
        style=style,
        character_config=character_config,
        date_id=date_id,
        character_anchor_hints=character_anchor_hints,
        dialogue_mode=dialogue_mode,
        long_conversation_mode=long_conversation_mode,
        repo_root=repo_root,
    )


# --- Title uniqueness (B3: derive from narration_script when duplicate) ---

TITLE_STOPWORDS = frozenset(
    "the a an to and or but as at by for in of on is it was were be been being "
    "have has had do does did will would could should may might must shall can "
    "this that these those i you he she we they what which who whom so no".split()
)
TITLE_RECENT_WINDOW = 30  # number of recent narrations to check for duplicate titles
TITLE_MIN_WORDS = 3
TITLE_MAX_WORDS = 5
TITLE_MIN_LAST_WORD_LEN = 4
TITLE_MAX_TOTAL_WORDS = 7  # allow skip words in the middle; cap total phrase length


def _normalize_title_for_comparison(title: str) -> str:
    """Lowercase, strip, collapse punctuation for duplicate check."""
    if not title or not isinstance(title, str):
        return ""
    t = title.strip().lower()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _extract_title_candidate_from_text(text: str) -> str | None:
    """
    Extract a title phrase from narration: 3–5 content words; skip words allowed in
    the middle. Phrase must end on a content word with 4+ letters and only at a
    punctuation boundary (so we don't cut "risen the keelboat" mid-clause). Total
    length 4–7 words. Returns None if no valid phrase.
    """
    if not text or not isinstance(text, str):
        return None
    # Build (word, followed_by_punct) so we only end at natural boundaries
    words_with_punct: list[tuple[str, bool]] = []
    for m in re.finditer(r"[A-Za-z]+", text):
        word = m.group()
        end = m.end()
        while end < len(text) and text[end].isspace():
            end += 1
        followed_by_punct = end < len(text) and text[end] in ".,;:!?"
        words_with_punct.append((word, followed_by_punct))
    if not words_with_punct:
        return None
    phrase_parts: list[str] = []
    content_count = 0
    candidate: str | None = None
    for w, followed_by_punct in words_with_punct:
        phrase_parts.append(w)
        is_content = w.lower() not in TITLE_STOPWORDS
        if is_content:
            content_count += 1
        if content_count > TITLE_MAX_WORDS or len(phrase_parts) > TITLE_MAX_TOTAL_WORDS:
            break
        if (
            TITLE_MIN_WORDS <= content_count <= TITLE_MAX_WORDS
            and is_content
            and len(w) >= TITLE_MIN_LAST_WORD_LEN
            and len(phrase_parts) >= 4
            and followed_by_punct
        ):
            candidate = " ".join(phrase_parts).title()
    return candidate


def _get_existing_titles(
    narrations_dir: Path,
    date_id: str,
    recent_n: int = TITLE_RECENT_WINDOW,
) -> set[str]:
    """Collect normalized titles from recent narration files, excluding date_id."""
    narrations_dir = Path(narrations_dir)
    if not narrations_dir.exists():
        return set()
    pattern = re.compile(r"narration(\d{8})\.json$", re.IGNORECASE)
    files: list[tuple[str, Path]] = []
    for p in narrations_dir.glob("narration*.json"):
        m = pattern.match(p.name)
        if m and m.group(1) != date_id:
            files.append((m.group(1), p))
    files.sort(key=lambda x: x[0], reverse=True)
    files = files[:recent_n]
    existing = set()
    for _, path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            t = (data.get("title") or "").strip()
            if t:
                existing.add(_normalize_title_for_comparison(t))
        except (OSError, JSONDecodeError, TypeError):
            continue
    return existing


def ensure_title_unique(
    merged: dict[str, Any],
    date_id: str,
    narrations_dir: Path,
    recent_n: int = TITLE_RECENT_WINDOW,
) -> None:
    """
    If merged["title"] is a duplicate of a recent narration title, replace it with
    a title derived from the first 3–5 content words of a narration segment (B3).
    Mutates merged in place.
    """
    current = (merged.get("title") or "").strip()
    if not current:
        return
    narrations_dir = Path(narrations_dir)
    existing = _get_existing_titles(narrations_dir, date_id, recent_n=recent_n)
    if not existing:
        return
    if _normalize_title_for_comparison(current) not in existing:
        return
    script = merged.get("narration_script") or []
    for seg in script:
        narration = (seg.get("narration") or "").strip()
        candidate = _extract_title_candidate_from_text(narration)
        if not candidate:
            continue
        cand_norm = _normalize_title_for_comparison(candidate)
        if cand_norm and cand_norm not in existing:
            if candidate and candidate[-1] not in ".,;:!?":
                candidate = candidate + "."
            merged["title"] = candidate
            print(f"[OK] Title unique: replaced with script phrase {candidate!r}", file=sys.stderr)
            return
    # No unique candidate from script; leave title as-is (B1/B2 could be added later)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Two-phase narration: Phase 1 (narration + metadata), Phase 2 (visual plan), then merge to pipeline JSON.",
    )
    parser.add_argument("date_id", help="Date identifier, e.g. 18030911")
    parser.add_argument(
        "--llm-provider",
        choices=["openai", "claude"],
        default="claude",
        help="Backend for Phase 1 / phase1-dialogue / title rewrite / Phase 2 (default: claude). "
        "Requires ANTHROPIC_API_KEY in .env; pass 'openai' to fall back to OPENAI_API_KEY. "
        "Does not affect --use-week-arc planning, which stays on OpenAI for now.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model id to use. Defaults per --llm-provider: "
        f"{DEFAULT_MODEL_BY_PROVIDER['openai']!r} for openai, "
        f"{DEFAULT_MODEL_BY_PROVIDER['claude']!r} for claude.",
    )
    parser.add_argument(
        "--config", default="config/narration_config.json", help="Path to narration config"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run Phase 1 only; print Phase 1 JSON and exit (no Phase 2, no write).",
    )
    parser.add_argument(
        "--no-focus-topic",
        "--no-theme-selector",
        dest="no_focus_topic",
        action="store_true",
        help="Disable theme_engine automatic focus topic (no recommend/record). Legacy alias: --no-theme-selector.",
    )
    parser.add_argument(
        "--visual-style-name",
        metavar="NAME",
        help="Override visual style: use this style name (same as v1 narration_script.visual_style.name).",
    )
    parser.add_argument(
        "--visual-style-desc",
        metavar="DESC",
        default="",
        help="Description for --visual-style-name (default: same as name).",
    )
    parser.add_argument(
        "--xml-dir",
        default="journal-entries",
        help="Directory of journal XML files (tei_journal sources)",
    )
    parser.add_argument(
        "--profile",
        metavar="PATH",
        default=None,
        help="Pipeline profile JSON (default: config/profiles/lewis_clark.json).",
    )
    parser.add_argument(
        "--source-text-file",
        metavar="PATH",
        default=None,
        help="UTF-8 text file for profiles with source.type plain_file.",
    )
    parser.add_argument(
        "--fal-scene-anchor-openings",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--dialogue",
        action="store_true",
        help="Character dialogue mode: prompt_packs/lewis_clark_dialogue (narrator-led with character dialogue in "
        "at most 3 segments; use --long-conversation for denser cast dialogue). Requires per-segment dialogue[] rules for TTS. "
        "Ignored when a focus topic is in use (single-narrator episode).",
    )
    parser.add_argument(
        "--long-conversation",
        action="store_true",
        help="Long dialogue episode mode: prompt_packs/lewis_clark_long_conversation (implies --dialogue; more segments "
        "+ optional segments[].conversation_tracking). Ignored when a focus topic is in use.",
    )
    parser.add_argument(
        "--use-week-arc",
        action="store_true",
        help="Load or create a 7-journal-day week arc plan (state/week_arcs/) and apply today's slice to Phase 1. "
        "When no manual --dialogue/--long-conversation flags are passed, recommended_mode from the plan selects the pack.",
    )
    parser.add_argument(
        "--refresh-week-arc",
        action="store_true",
        help="With --use-week-arc: regenerate the week plan even when a cached file exists.",
    )
    parser.add_argument(
        "--no-week-arc",
        action="store_true",
        help="Do not load or create a week arc (overrides --use-week-arc).",
    )
    args = parser.parse_args()
    if args.model is None:
        args.model = DEFAULT_MODEL_BY_PROVIDER[args.llm_provider]
    if args.llm_provider != "claude":
        print(
            f"[LLM] Using provider={args.llm_provider!r} model={args.model!r} "
            "for Phase 1 / phase1-dialogue / title rewrite / Phase 2.",
            file=sys.stderr,
        )

    date_id = args.date_id
    if len(date_id) != 8:
        print("[ERROR] date_id must be 8 digits (e.g. 18030911)", file=sys.stderr)
        sys.exit(1)

    _repo_root = Path(__file__).resolve().parent
    prof_path = (
        Path(args.profile).expanduser() if args.profile else default_profile_path(_repo_root)
    )
    if not prof_path.is_file():
        print(f"[ERROR] Profile not found: {prof_path}", file=sys.stderr)
        sys.exit(1)
    profile = load_pipeline_profile(prof_path)
    print(f"[Profile] {profile.id} ({profile.label})", file=sys.stderr)

    if profile.source_type.lower().strip() == "plain_file" and not args.source_text_file:
        print(
            "[ERROR] This profile uses plain_file sources; pass --source-text-file PATH.",
            file=sys.stderr,
        )
        sys.exit(1)

    use_week_arc = bool(args.use_week_arc) and not bool(args.no_week_arc)

    if args.no_focus_topic:
        print(
            "[Focus topic] Disabled (--no-focus-topic): skipping theme_engine recommend/record.",
            file=sys.stderr,
        )
    elif not profile.use_theme_engine:
        print(
            "[Focus topic] Automatic focus topic off for this profile (theme_engine disabled).",
            file=sys.stderr,
        )
    elif use_week_arc:
        print(
            "[Focus topic] Disabled (--use-week-arc): week arc plans own the episode's mode/role; "
            "a focus topic would override that with a single-narrator deep dive.",
            file=sys.stderr,
        )
    effective_no_theme = bool(args.no_focus_topic or not profile.use_theme_engine or use_week_arc)
    ctx = load_episode_source_bundle(
        _repo_root,
        profile,
        date_id,
        xml_rel=args.xml_dir,
        source_text_file=Path(args.source_text_file).expanduser()
        if args.source_text_file
        else None,
        no_theme_selector=effective_no_theme,
    )
    if not ctx.get("ok"):
        print(f"[ERROR] {ctx.get('message', ctx.get('error', 'prepare failed'))}", file=sys.stderr)
        sys.exit(1)
    entry_text = ctx["entry_text"]
    entry_author = ctx.get("entry_author")
    tei_linked_notes = ctx.get("editorial_notes") or ""
    focus_topic = ctx.get("focus_topic")
    theme_recommend_result = ctx.get("theme_recommend_result")
    is_blank_page = bool(ctx.get("is_blank_page"))
    if (
        profile.use_theme_engine
        and not args.no_focus_topic
        and focus_topic
        and isinstance(theme_recommend_result, dict)
    ):
        reason = theme_recommend_result.get("reason", "") or ""
        cs = theme_recommend_result.get("closest_prior_similarity")
        cd = theme_recommend_result.get("closest_prior_date_id")
        extra = ""
        if cs is not None and cd is not None:
            extra = f", closest_prior={cs} ({cd})"
        elif cs is not None:
            extra = f", closest_prior={cs}"
        print(
            f"[Focus topic] Using: {focus_topic} ({reason}{extra})",
            file=sys.stderr,
        )
    if is_blank_page:
        if focus_topic:
            print(
                "[Blank page] Using theme-based or generic placeholder for narration.",
                file=sys.stderr,
            )
        else:
            print("[Blank page] Using generic placeholder for narration.", file=sys.stderr)

    if args.long_conversation and args.dialogue:
        print(
            "[Phase 1] Using long-conversation pack (--dialogue redundant with --long-conversation)",
            file=sys.stderr,
        )

    theme_journal_dir = _repo_root / (
        profile.source_xml_dir
        if profile.source_type.lower().strip() == "tei_journal"
        else args.xml_dir
    )

    from pipeline.week_arc import (
        build_week_arc_prompt_block,
        cli_specifies_mode,
        ensure_week_arc,
        modes_from_day_plan,
        requires_talking_head,
        week_arc_ref_for_merge,
    )

    week_arc_doc: dict[str, Any] | None = None
    week_arc_prompt: str | None = None
    if use_week_arc:
        try:
            week_arc_doc = ensure_week_arc(
                date_id,
                repo_root=_repo_root,
                journal_dir=theme_journal_dir,
                narrations_dir=_repo_root / "narrations",
                # Week-arc planning stays on OpenAI regardless of --llm-provider: it's a
                # separate call site (pipeline.week_arc) not yet wired to pipeline.llm_provider.
                model=args.model
                if args.llm_provider == "openai"
                else DEFAULT_MODEL_BY_PROVIDER["openai"],
                refresh=bool(args.refresh_week_arc),
            )
        except Exception as exc:
            print(f"[WARN] Week arc: could not load or create plan: {exc}", file=sys.stderr)
            week_arc_doc = None
        if week_arc_doc:
            week_arc_prompt = build_week_arc_prompt_block(week_arc_doc, date_id) or None
            if week_arc_prompt:
                print(
                    f"[Week arc] Using plan week_{week_arc_doc.get('week_start_date_id')} "
                    f"({week_arc_doc.get('week_start_date_id')}–{week_arc_doc.get('week_end_date_id')}).",
                    file=sys.stderr,
                )
            else:
                print(
                    "[WARN] Week arc: plan loaded but no day slice for this date.", file=sys.stderr
                )
        else:
            print(
                "[WARN] Week arc: no plan (date before anchor or journal week missing).",
                file=sys.stderr,
            )

    manual_mode = cli_specifies_mode(
        dialogue=bool(args.dialogue),
        no_dialogue=False,
        long_conversation=bool(args.long_conversation),
        no_long_conversation=False,
    )

    LC_PACK = "lewis_clark_long_conversation"

    require_talking_head_effective = False
    if manual_mode:
        long_conversation_effective = bool(args.long_conversation) and not bool(focus_topic)
        dialogue_effective = (bool(args.dialogue) or long_conversation_effective) and not bool(
            focus_topic
        )
    elif week_arc_doc:
        # focus_topic is always unset here: --use-week-arc forces effective_no_theme above,
        # so week-arc days never lose their planned mode to a focus-topic override.
        day_plan = (week_arc_doc.get("days") or {}).get(date_id)
        day_plan = day_plan if isinstance(day_plan, dict) else None
        dialogue_effective, long_conversation_effective = modes_from_day_plan(
            day_plan,
            focus_topic=focus_topic,
        )
        if dialogue_effective or long_conversation_effective:
            mode_label = "long_conversation" if long_conversation_effective else "dialogue"
            print(f"[Week arc] Recommended mode for today: {mode_label}", file=sys.stderr)
            require_talking_head_effective = requires_talking_head(
                day_plan, focus_topic=focus_topic
            )
            if require_talking_head_effective:
                print(
                    "[Week arc] Requiring at least one talking_head segment for this day's plan.",
                    file=sys.stderr,
                )
    else:
        long_conversation_effective = bool(args.long_conversation) and not bool(focus_topic)
        dialogue_effective = (bool(args.dialogue) or long_conversation_effective) and not bool(
            focus_topic
        )
    phase1_prompt_pack = profile.prompt_pack
    phase2_prompt_pack = profile.prompt_pack
    if long_conversation_effective:
        allowed_phase1_for_override = {"lewis_clark", "lewis_clark_dialogue", LC_PACK}
        if phase1_prompt_pack not in allowed_phase1_for_override:
            print(
                f"[WARN] --long-conversation overrides prompt_pack {phase1_prompt_pack!r} → {LC_PACK}",
                file=sys.stderr,
            )
        phase1_prompt_pack = LC_PACK
        if phase2_prompt_pack not in allowed_phase1_for_override:
            print(
                f"[WARN] --long-conversation overrides Phase 2 prompt_pack {phase2_prompt_pack!r} → {LC_PACK}",
                file=sys.stderr,
            )
        phase2_prompt_pack = LC_PACK
        print(f"[Phase 1] Long conversation mode ({LC_PACK} pack)", file=sys.stderr)
    elif dialogue_effective:
        if phase1_prompt_pack not in ("lewis_clark", "lewis_clark_dialogue"):
            print(
                f"[WARN] --dialogue overrides prompt_pack {phase1_prompt_pack!r} → lewis_clark_dialogue",
                file=sys.stderr,
            )
        phase1_prompt_pack = "lewis_clark_dialogue"
        print("[Phase 1] Character dialogue mode (lewis_clark_dialogue pack)", file=sys.stderr)
    elif args.dialogue and focus_topic:
        print(
            "[Phase 1] Dialogue disabled for this run: focus topic uses single-narrator style "
            "(ignoring --dialogue).",
            file=sys.stderr,
        )
    elif args.long_conversation and focus_topic:
        print(
            "[Phase 1] Long conversation disabled for this run: focus topic uses single-narrator style "
            "(ignoring --long-conversation).",
            file=sys.stderr,
        )

    theme_narrations_dir = _repo_root / "narrations"
    theme_state_path = _repo_root / "theme_engine" / "state.json"
    theme_embeddings_path = _repo_root / "theme_engine" / "embeddings.json"
    config_path = Path(args.config) if args.config else None
    config = load_narration_config(config_path)
    if args.visual_style_name:
        style = {
            "name": args.visual_style_name,
            "description": (args.visual_style_desc or args.visual_style_name).strip(),
        }
    else:
        diversity_cfg = config.get("style_diversity") or {}
        recent_window = int(diversity_cfg.get("recent_window", 6) or 6)
        recent_styles = recent_visual_style_names(
            theme_narrations_dir,
            current_date_id=date_id,
            limit=max(0, recent_window),
        )
        style = select_visual_style(
            config, recent_style_names=recent_styles
        ) or get_default_visual_style(config)

    phase1_max_tokens = 16384 if long_conversation_effective else DEFAULT_MAX_TOKENS
    phase1_max_retries = 5 if long_conversation_effective else MAX_RETRIES
    # Phase 2's visual plan has one block per Phase 1 segment, so it needs the same
    # larger budget in long-conversation mode (12-15 segments) — otherwise the response
    # truncates mid-JSON, same failure mode the phase1-dialogue budget above already covers.
    # Standard dialogue-mode episodes (7-9 segments) can also truncate at the plain
    # DEFAULT_MAX_TOKENS budget when Phase 2's per-segment visual detail runs long, so give
    # them headroom above the default too (short of the long-conversation budget).
    phase2_max_tokens = 16384 if long_conversation_effective else 12288
    if long_conversation_effective and phase1_max_tokens != DEFAULT_MAX_TOKENS:
        print(
            f"[Phase 1] Long-conversation token budget: {phase1_max_tokens}",
            file=sys.stderr,
        )
        print(
            f"[Phase 1] Long-conversation retry budget: {phase1_max_retries + 1} attempts",
            file=sys.stderr,
        )
    print("[Phase 1] Generating narration + metadata...", file=sys.stderr)
    from pipeline.recent_episode_diversity import build_episode_diversity_bundle

    week_arc_role: str | None = None
    if week_arc_doc:
        _role_day_plan = (week_arc_doc.get("days") or {}).get(date_id)
        if isinstance(_role_day_plan, dict):
            week_arc_role = _role_day_plan.get("role")

    ediv_bundle = build_episode_diversity_bundle(
        _repo_root,
        theme_narrations_dir,
        date_id,
        phase1_prompt_pack,
        config,
        week_arc_role=week_arc_role,
    )
    recent_episodes_digest = str(ediv_bundle.get("digest") or "")
    if recent_episodes_digest:
        print(
            f"[Phase 1] Lewis & Clark episode-diversity digest appended ({phase1_prompt_pack}).",
            file=sys.stderr,
        )
    phase1 = run_phase1(
        entry_text,
        date_id,
        entry_author=entry_author,
        model=args.model,
        focus_topic=focus_topic,
        max_retries=phase1_max_retries,
        editorial_notes=tei_linked_notes or None,
        repo_root=_repo_root,
        prompt_pack=phase1_prompt_pack,
        phase1_user=profile.phase1_user,
        dialogue_mode=dialogue_effective,
        conversation_mode=(config.get("dialogue_conversation_mode") or {}),
        max_tokens=phase1_max_tokens,
        recent_episodes_digest=recent_episodes_digest or None,
        require_talking_head=require_talking_head_effective,
        week_arc_prompt=week_arc_prompt,
        provider=args.llm_provider,
    )
    print(f"[Phase 1] OK — {len(phase1.get('segments') or [])} segments", file=sys.stderr)

    if dialogue_effective:
        print("[phase1-dialogue] Polishing spoken dialogue lines...", file=sys.stderr)
        phase1 = run_phase1_dialogue(
            phase1,
            date_id,
            model=args.model,
            repo_root=_repo_root,
            prompt_pack=phase1_prompt_pack,
            conversation_mode=(config.get("dialogue_conversation_mode") or {}),
            provider=args.llm_provider,
            # phase1-dialogue echoes the full Phase 1 JSON back with only dialogue[].text
            # rewritten, so it needs the same token budget as Phase 1 itself — otherwise a
            # properly-sized (12-15 segment) episode truncates mid-response. Previously left
            # at the 8192 default, which only "worked" because Phase 1 output was usually
            # short of the segment target.
            max_tokens=phase1_max_tokens,
        )
        print("[phase1-dialogue] OK", file=sys.stderr)

    phase1 = refine_phase1_title(
        phase1,
        date_id=date_id,
        repo_root=_repo_root,
        prompt_pack=phase1_prompt_pack,
        model=args.model,
        provider=args.llm_provider,
    )

    character_anchor_hints = assign_reference_character_hints(
        phase1,
        date_id,
        config.get("character_injection") if profile.use_character_hints else None,
    )
    n_hinted = sum(1 for h in character_anchor_hints if h)
    if n_hinted:
        print(
            f"[Phase 1.5] Character anchor hints: {n_hinted} segment(s) with portrait id",
            file=sys.stderr,
        )

    if args.dry_run:
        print(json.dumps(phase1, indent=2, ensure_ascii=False))
        return

    theme_narrations_dir.mkdir(parents=True, exist_ok=True)
    voice_sidecar_path = theme_narrations_dir / f"narration{date_id}_voice.json"
    voice_doc = build_voice_sidecar_document(phase1, date_id)
    voice_json = json.dumps(voice_doc, indent=2, ensure_ascii=False)
    voice_sidecar_path.write_text(voice_json, encoding="utf-8")
    log_file_created(voice_sidecar_path, len(voice_json.encode("utf-8")))
    print(f"[OK] Voice sidecar: {voice_sidecar_path}", file=sys.stderr)

    print("[Phase 2] Generating visual plan...", file=sys.stderr)
    if recent_episodes_digest:
        print(
            "[Phase 2] Recent-episode diversity digest appended to user prompt.",
            file=sys.stderr,
        )
    phase2 = run_phase2(
        phase1,
        date_id,
        model=args.model,
        style=style,
        focus_topic=focus_topic,
        character_anchor_hints=character_anchor_hints,
        focus_expedition_era=profile.phase2.focus_expedition_era,
        historical_only_visuals=profile.phase2.historical_only_visuals,
        seasonal_ambient_from_date_id=profile.phase2.seasonal_ambient_from_date_id,
        repo_root=_repo_root,
        narrations_dir=theme_narrations_dir,
        prompt_pack=phase2_prompt_pack,
        recent_episodes_digest=recent_episodes_digest or None,
        provider=args.llm_provider,
        max_tokens=phase2_max_tokens,
    )
    print("[Phase 2] OK", file=sys.stderr)

    visual_sidecar_path = theme_narrations_dir / f"narration{date_id}_visual.json"
    visual_doc = build_visual_sidecar_document(phase2, date_id)
    visual_json = json.dumps(visual_doc, indent=2, ensure_ascii=False)
    visual_sidecar_path.write_text(visual_json, encoding="utf-8")
    log_file_created(visual_sidecar_path, len(visual_json.encode("utf-8")))
    print(f"[OK] Visual sidecar: {visual_sidecar_path}", file=sys.stderr)

    merged, used_counts = merge_phase1_phase2(
        phase1,
        phase2,
        style,
        character_config=config.get("character_injection"),
        date_id=date_id,
        character_anchor_hints=character_anchor_hints,
        dialogue_mode=dialogue_effective,
        long_conversation_mode=long_conversation_effective,
        repo_root=_repo_root,
    )
    cue_log = apply_prompt_replacements(
        merged,
        config.get("prompt_replacements") or [],
        config.get("video_prompt_replacements") or [],
    )
    if cue_log:
        print(f"[video_prompt_cues] applied: {cue_log}", file=sys.stderr)
    # Use same absolute narrations dir for writing and for theme_selector.record_narration
    out_dir = theme_narrations_dir
    ensure_title_unique(merged, date_id, out_dir)
    validate_narration_schema(merged)
    merged["prompt_pack_lineage"] = build_prompt_pack_lineage(
        _repo_root,
        phase1_pack=phase1_prompt_pack,
        phase2_pack=phase2_prompt_pack,
        phase1_dialogue_pack=phase1_prompt_pack if dialogue_effective else None,
    )
    merged["pipeline_profile_id"] = profile.id
    if ctx.get("parent_document_id") is not None:
        merged["source_parent_document_id"] = ctx["parent_document_id"]
    if ctx.get("chunk_index") is not None:
        merged["source_chunk_index"] = ctx["chunk_index"]
    if week_arc_doc:
        ref = week_arc_ref_for_merge(week_arc_doc, date_id)
        if ref:
            merged["week_arc_ref"] = ref
    if args.no_focus_topic or not profile.use_theme_engine:
        merged.pop("focus_topic", None)
        merged.pop("theme_selector_decision", None)
    else:
        if focus_topic:
            merged["focus_topic"] = focus_topic
        if focus_topic and theme_recommend_result:
            merged["theme_selector_decision"] = {
                "reason": theme_recommend_result.get("reason"),
                "closest_prior_similarity": theme_recommend_result.get("closest_prior_similarity"),
                "closest_prior_date_id": theme_recommend_result.get("closest_prior_date_id"),
                "similarity_threshold": theme_recommend_result.get("similarity_threshold"),
            }
    merged["fal_scene_anchor_openings"] = True
    policy_cfg = config.get("style_mode_policy") or {}
    narrator_only_styles = policy_cfg.get("narrator_only_styles") or []
    if enforce_narrator_only_for_style_policy(
        merged,
        narrator_only_style_names=narrator_only_styles,
    ):
        print(
            "[INFO] Style policy applied: narrator-only style (dialogue and scene anchors disabled).",
            file=sys.stderr,
        )

    # Unified canonical output: narration<date_id>.json (stitched from voice + visual sidecars)
    out_dir.mkdir(parents=True, exist_ok=True)
    # narration<date_id>.json
    out_path = out_dir / f"narration{date_id}.json"
    # Safety: if a narration already exists, move it aside with a timestamped suffix
    if out_path.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = out_dir / f"narration{date_id}-{ts}.json"
        out_path.rename(backup_path)
        print(
            f"[BACKUP] Previous narration (snapshot before this run; may still show old focus_topic) → {backup_path}",
            file=sys.stderr,
        )
    content = json.dumps(merged, indent=2, ensure_ascii=False)
    out_path.write_text(content, encoding="utf-8")
    log_file_created(out_path, len(content.encode("utf-8")))
    print(f"[OK] Wrote {out_path}", file=sys.stderr)
    try:
        from pipeline.episode_state import refresh_episode_state_sidecar

        refresh_episode_state_sidecar(
            _repo_root,
            date_id,
            narration=merged,
            source="generate-narration-two-phase",
        )
    except Exception as e:
        print(f"[WARN] episode state sidecar: {e}", file=sys.stderr)
    # Update persistent character usage counts for tuning
    update_character_usage(date_id, used_counts)
    # Convenience: clickable shortcut to latest narration in repo root
    _write_latest_shortcut("latest_narration.url", out_path)

    if (
        profile.use_theme_engine
        and not args.no_focus_topic
        and theme_record_narration is not None
        and profile.source_type.lower().strip() == "tei_journal"
    ):
        try:
            theme_record_narration(
                date_id,
                narrations_dir=out_dir,
                state_path=theme_state_path,
                journal_dir=theme_journal_dir,
                embeddings_path=theme_embeddings_path,
            )
            print(f"[Focus topic] Recorded theme_engine state for {date_id}", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Focus topic / theme_engine record failed: {e}", file=sys.stderr)
    elif profile.use_theme_engine and not args.no_focus_topic and theme_record_narration is None:
        print(
            "[WARN] theme_engine not available (import failed); theme_engine/state.json will not be updated.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
