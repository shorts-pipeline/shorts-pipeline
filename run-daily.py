#!/usr/bin/env python3
"""
Run the full Lewis & Clark video pipeline for a journal entry date,
and optionally upload to YouTube.

Designed for scheduled runs (e.g., daily at 6am) to publish a video
on the anniversary of each diary entry.

Usage:
  python run-daily.py                    # Use today's month-day → 1803-MM-DD
  python run-daily.py --date 1804-08-30  # Explicit journal date (YYYY-MM-DD)
  python run-daily.py --date 18040830    # Same as above (YYYYMMDD)
  python run-daily.py --upload           # Include YouTube upload
  python run-daily.py --vendor google    # Use Google Veo (default: fal)
  python run-daily.py --ambient          # Mix ambient bed in assembly (see audio_design.ambience_level)
  python run-daily.py --no-focus-topic  # Narration without automatic focus topic (theme_engine)
"""

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import video_manifest
from pipeline.automation_gates import (
    narration_audio_segment_mismatch,
    postflight_output_video,
    preflight_run_daily,
)
from pipeline.day_reset import archive_day_artifacts, revert_last_run_date_if_matches
from pipeline.mode_inference import resolve_run_daily_modes
from pipeline.output_naming import DEFAULT_VIDEO_OUTPUT_PREFIX, output_video_filename
from pipeline.week_arc import ensure_week_arc, get_day_plan
from pipeline_logging import log_video_complete
from theme_engine.theme_selector import entry_word_count as theme_entry_word_count

# Default expedition year when using today's date
DEFAULT_YEAR = 1803

EXPEDITION_YEARS = (1803, 1804, 1805, 1806)
RUN_DAILY_STATE_PATH = Path("state") / "run_daily_state.json"


def date_to_date_id(date_str: str) -> str:
    """Convert YYYY-MM-DD to YYYYMMDD."""
    return date_str.replace("-", "")


def parse_journal_date_arg(raw: str) -> str:
    """Normalize --date to YYYY-MM-DD. Accepts YYYY-MM-DD or YYYYMMDD (compact)."""
    s = raw.strip()
    if not s:
        raise ValueError("Date value is empty.")
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except ValueError:
        pass
    if len(s) == 8 and s.isdigit():
        dt = datetime.strptime(s, "%Y%m%d")
        return dt.strftime("%Y-%m-%d")
    raise ValueError(f"Invalid date format: {raw!r}. Use YYYY-MM-DD, YYYYMMDD, or 'next'.")


def find_available_entry(month: int, day: int) -> str | None:
    """Return YYYY-MM-DD for first available journal entry, or None."""
    journal_dir = Path("journal-entries")
    for year in EXPEDITION_YEARS:
        date_str = f"{year}-{month:02d}-{day:02d}"
        if (journal_dir / f"{date_str}.xml").exists():
            return date_str
    return None


def load_last_run_date() -> str | None:
    """Load the last successfully completed journal date (YYYY-MM-DD) from state file."""
    if not RUN_DAILY_STATE_PATH.exists():
        return None
    try:
        data = json.loads(RUN_DAILY_STATE_PATH.read_text(encoding="utf-8"))
        last = data.get("last_date")
        return last if isinstance(last, str) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_last_run_date(date_str: str) -> None:
    """Persist the last successfully completed journal date (YYYY-MM-DD)."""
    RUN_DAILY_STATE_PATH.write_text(
        json.dumps({"last_date": date_str}, indent=2),
        encoding="utf-8",
    )


# When using --date next, skip entries with this many words or fewer (short/blank).
MIN_ENTRY_WORDS_NEXT = 20


def entry_word_count(date_str: str) -> int:
    """Return alphabetic word count for this journal date. Delegates to theme_selector for single definition."""
    return theme_entry_word_count(date_str, Path("journal-entries"))


def find_next_journal_after(last_date: str) -> str | None:
    """Return the next YYYY-MM-DD journal entry after last_date, based on files in journal-entries/."""
    journal_dir = Path("journal-entries")
    if not journal_dir.exists():
        return None
    candidates: list[str] = []
    for p in journal_dir.glob("*.xml"):
        name = p.stem  # expect YYYY-MM-DD
        try:
            # Validate format
            datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            continue
        if name > last_date:
            candidates.append(name)
    return min(candidates) if candidates else None


def scrape_entry(date_str: str) -> bool:
    """Fetch journal XML from CDRH. Returns True if found."""
    import requests

    url = f"https://cdrhmedia.unl.edu/data/lewisandclark/source/tei/lc.jrn.{date_str}.xml"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            Path("journal-entries").mkdir(exist_ok=True)
            (Path("journal-entries") / f"{date_str}.xml").write_bytes(r.content)
            print(f"[OK] Fetched journal: {date_str}")
            return True
    except Exception as e:
        print(f"[ERROR] Scrape failed: {e}")
    return False


def run_cmd(cmd: list[str], description: str) -> bool:
    """Run a command; return True on success."""
    print(f"\n>> {description}")
    result = subprocess.run(cmd, cwd=Path(__file__).parent)
    if result.returncode != 0:
        print(f"[ERROR] Failed: {' '.join(cmd)}", file=sys.stderr)
        return False
    return True


def parse_regenerate_video_segments_arg(raw: str | None) -> list[int] | None:
    """
    Parse --regenerate-video-segments value into sorted unique 1-based indices.
    None / blank means not requested.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    out: list[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError as e:
            raise ValueError(
                f"Invalid --regenerate-video-segments (expected integers like 3,7): {part!r}"
            ) from e
    if not out:
        return None
    return sorted(set(out))


def backup_clips_and_output_for_regen(
    date_id: str,
    segment_indices: list[int],
    output_video: Path,
) -> Path:
    """
    Move listed segment clips out of movie-images/<date_id>/ into a timestamped backup folder
    (so narration-to-video will recreate them). Copy the final output video into the same folder
    if it exists (assembly will overwrite the original path).

    Also copies FAL scene-anchor stills (``anchors/NN_*.png`` etc.) for those segment indices into
    ``regen_backup_<ts>/anchors/`` so segment regen (which re-runs i2i and overwrites them) does not
    silently discard a hand-picked anchor from the UI.
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    video_dir = Path("movie-images") / date_id
    video_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = video_dir / f"regen_backup_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    anchors_dir = video_dir / "anchors"
    anchor_exts = {".png", ".jpg", ".jpeg", ".webp"}
    for idx in segment_indices:
        name = f"{idx:02d}.mp4"
        src = video_dir / name
        if src.is_file():
            dest = backup_dir / name
            shutil.move(str(src), str(dest))
            print(f"[OK] Moved previous clip to backup: {dest}")
        else:
            print(f"[INFO] No existing clip to move (will create new): {src}")
        if anchors_dir.is_dir():
            dest_anchors = backup_dir / "anchors"
            for cand in sorted(anchors_dir.glob(f"{idx:02d}_*")):
                if cand.is_file() and cand.suffix.lower() in anchor_exts:
                    dest_anchors.mkdir(parents=True, exist_ok=True)
                    dest = dest_anchors / cand.name
                    shutil.copy2(cand, dest)
                    print(f"[OK] Copied previous scene anchor to backup: {dest}")
    if output_video.is_file():
        dest_out = backup_dir / output_video.name
        shutil.copy2(output_video, dest_out)
        print(f"[OK] Copied previous final output to backup: {dest_out}")
    else:
        print(f"[INFO] No existing final output to copy: {output_video}")
    return backup_dir


def main():
    parser = argparse.ArgumentParser(
        description="Run full pipeline for a journal entry date, optionally upload to YouTube"
    )
    parser.add_argument(
        "--date",
        help="Journal date: YYYY-MM-DD or YYYYMMDD. Default: today's month-day with year 1803",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload final video to YouTube",
    )
    parser.add_argument(
        "--vendor",
        choices=["sora", "google", "fal"],
        default="fal",
        help="Video vendor for AI clips (default: fal)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip steps if output exists (default: True)",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_false",
        dest="skip_existing",
        help="Re-run all steps even if output exists",
    )
    parser.add_argument(
        "--privacy",
        choices=["public", "unlisted", "private"],
        default="public",
        help="YouTube privacy (default: public)",
    )
    parser.add_argument(
        "--force-upload",
        action="store_true",
        help="With --upload: upload even if this episode's manifest already has a youtube_video_id "
        "(creates a duplicate YouTube video).",
    )
    parser.add_argument(
        "--allow-upload-gap",
        action="store_true",
        help="With --upload: upload even if earlier assembled episodes in output/ have not been "
        "uploaded yet (publishes out of journal order).",
    )
    parser.add_argument(
        "--shorts",
        action="store_true",
        default=True,
        help="Produce 9:16 vertical video for YouTube Shorts (default).",
    )
    parser.add_argument(
        "--wide-screen",
        action="store_false",
        dest="shorts",
        help="Produce 16:9 wide-screen output instead of Shorts.",
    )
    parser.add_argument(
        "--fal-scene-anchor-openings",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show full plan without executing any steps.",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip preflight and postflight automation checks (JSON, dialogue, FAL portraits, output probe).",
    )
    parser.add_argument(
        "--strict-preflight",
        action="store_true",
        help="Treat stale audio vs narration segment counts as a hard error when skip-existing.",
    )
    parser.add_argument(
        "--tts",
        choices=["pyttsx3", "openai"],
        default="openai",
        help="TTS engine for narration: openai (default, higher quality) or pyttsx3 (local, free)",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["openai", "claude"],
        default="claude",
        help="Pass to generate-narration-two-phase.py: backend for narration text generation "
        "(default: claude, requires ANTHROPIC_API_KEY in .env). Pass 'openai' to fall back "
        "to OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help="Model id for narration (e.g. gpt-4o for --llm-provider openai, claude-sonnet-5 for "
        "claude). Default: per-provider default in generate-narration-two-phase.py.",
    )
    parser.add_argument(
        "--narration-only",
        action="store_true",
        help="Only scrape (if needed) and generate narration JSON; stop before audio, video, assembly, and upload. Does not update last-run pipeline state.",
    )
    parser.add_argument(
        "--no-focus-topic",
        "--no-theme-selector",
        dest="no_focus_topic",
        action="store_true",
        help="Pass to generate-narration-two-phase.py: disable theme_engine (no automatic focus topic; no recommend/record). Legacy alias: --no-theme-selector.",
    )
    parser.add_argument(
        "--dialogue",
        action="store_true",
        help="Pass to generate-narration-two-phase.py: dialogue mode—lewis_clark_dialogue (narrator-led, "
        "character dialogue in at most 3 segments). Use --long-conversation for denser cast dialogue.",
    )
    parser.add_argument(
        "--no-dialogue",
        action="store_true",
        help="Regenerate narration without dialogue mode, even if narrations/narration<DATE>.json was "
        "produced with dialogue. Omit both --dialogue and --no-dialogue to infer from existing file when present.",
    )
    parser.add_argument(
        "--long-conversation",
        action="store_true",
        help="Pass to generate-narration-two-phase.py: long dialogue episode "
        "(lewis_clark_long_conversation prompt pack; implies dialogue).",
    )
    parser.add_argument(
        "--no-long-conversation",
        action="store_true",
        help="Regenerate narration without long-conversation mode, even if narrations/narration<DATE>.json "
        "has long_conversation_mode true. When this flag is omitted and --long-conversation is not passed, "
        "run-daily infers long mode from the existing narration file (same pattern as dialogue inference).",
    )
    parser.add_argument(
        "--use-week-arc",
        action="store_true",
        help="Load or create a 7-journal-day week arc (state/week_arcs/) and apply today's recommended mode "
        "when dialogue/long-conversation flags are not set manually. Passed to generate-narration-two-phase.",
    )
    parser.add_argument(
        "--refresh-week-arc",
        action="store_true",
        help="With --use-week-arc: regenerate the cached week plan before running.",
    )
    parser.add_argument(
        "--no-week-arc",
        action="store_true",
        help="Do not use week arc planning (overrides --use-week-arc).",
    )
    parser.add_argument(
        "--regenerate-video-segments",
        default=None,
        metavar="N,N,...",
        help="google/fal only: comma-separated 1-based segment indices. Backs up those clips and the final "
        "output, regenerates only those segments, then reassembles. Does not update state/run_daily_state.json last_date.",
    )
    parser.add_argument(
        "--reset-day",
        action="store_true",
        help="Before running pipeline steps, move narrations, audio/<date_id>/, movie-images/<date_id>/, "
        "and output/* for this date into archive/reset_<date_id>_<timestamp>/ (fresh start). "
        "If state/run_daily_state.json last_date matches this date, revert it to the prior journal entry.",
    )
    parser.add_argument(
        "--reset-day-only",
        action="store_true",
        help="Only archive day artifacts (--reset-day) and exit; do not run narration, TTS, video, or upload.",
    )
    parser.add_argument(
        "--ambient",
        action="store_true",
        default=False,
        help="Pass to videos-mp3-to-movie: mix ambient bed when narration audio_design requests light/moderate.",
    )
    parser.add_argument(
        "--audio-tail-pad-seconds",
        type=float,
        default=1.5,
        metavar="SEC",
        help="Pass to videos-mp3-to-movie: append SEC seconds of silence after the voice track before mux (default 1.5). Use 0 to disable.",
    )
    parser.add_argument(
        "--force-assembly",
        action="store_true",
        default=False,
        help="Run videos-mp3-to-movie even when --skip-existing would skip assembly (e.g. after swapping "
        "segment MP4s under movie-images/). Does not re-run narration, TTS, or clip generation. "
        "Ignored for --vendor sora (no separate assembly step).",
    )
    parser.add_argument(
        "--output-prefix",
        default=DEFAULT_VIDEO_OUTPUT_PREFIX,
        metavar="PREFIX",
        help=f"Final MP4 basename prefix (default: {DEFAULT_VIDEO_OUTPUT_PREFIX}). Passed to videos-mp3-to-movie.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="PATH",
        help="Forwarded to generate-narration-two-phase.py --profile (default profile in that script if omitted).",
    )
    parser.add_argument(
        "--source-text-file",
        default=None,
        metavar="PATH",
        help="Forwarded to generate-narration-two-phase.py when using a plain_file profile.",
    )
    args = parser.parse_args()

    if args.reset_day_only and not args.reset_day:
        args.reset_day = True
    if args.reset_day and args.regenerate_video_segments:
        print(
            "[ERROR] --reset-day cannot be used with --regenerate-video-segments.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.dialogue and args.no_dialogue:
        print("[ERROR] Use --dialogue or --no-dialogue, not both.", file=sys.stderr)
        sys.exit(1)
    if args.long_conversation and args.no_long_conversation:
        print(
            "[ERROR] Use --long-conversation or --no-long-conversation, not both.", file=sys.stderr
        )
        sys.exit(1)
    if args.no_dialogue and args.long_conversation:
        print("[ERROR] --long-conversation conflicts with --no-dialogue.", file=sys.stderr)
        sys.exit(1)
    if args.use_week_arc and args.no_week_arc:
        print("[ERROR] Use --use-week-arc or --no-week-arc, not both.", file=sys.stderr)
        sys.exit(1)

    try:
        regen_segments = parse_regenerate_video_segments_arg(args.regenerate_video_segments)
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    # Resolve journal date
    if args.date:
        if args.date == "next":
            last = load_last_run_date()
            if not last:
                print(
                    "[ERROR] No last run date recorded; run once with --date YYYY-MM-DD or YYYYMMDD first."
                )
                sys.exit(1)
            next_date = find_next_journal_after(last)
            while next_date and entry_word_count(next_date) < MIN_ENTRY_WORDS_NEXT:
                w = entry_word_count(next_date)
                print(
                    f"[SKIP] {next_date} has {w} word(s) (min {MIN_ENTRY_WORDS_NEXT}); trying next."
                )
                next_date = find_next_journal_after(next_date)
            if not next_date:
                print(
                    f"[ERROR] No journal entry found after {last} (or all candidates are short/blank)."
                )
                sys.exit(1)
            date_str = next_date
            print(f"[OK] Using next available journal entry after {last}: {date_str}")
        else:
            try:
                date_str = parse_journal_date_arg(args.date)
            except ValueError as e:
                print(f"[ERROR] {e}", file=sys.stderr)
                sys.exit(1)
    else:
        now = datetime.now()
        date_str = find_available_entry(now.month, now.day)
        if not date_str:
            # Try to scrape today's entry
            date_str = f"{DEFAULT_YEAR}-{now.month:02d}-{now.day:02d}"
            if not scrape_entry(date_str):
                print(f"[ERROR] No journal entry for {now.month}/{now.day} (tried {date_str})")
                sys.exit(1)
        else:
            print(f"[OK] Processing entry: {date_str}")

    date_id = date_to_date_id(date_str)
    repo_root = Path.cwd()
    xml_path = Path("journal-entries") / f"{date_str}.xml"
    narration_path = Path("narrations") / f"narration{date_id}.json"
    audio_final = Path(f"audio/{date_id}/final.mp3")

    if args.reset_day:
        reset_result = archive_day_artifacts(repo_root, date_id, dry_run=args.dry_run)
        for rel in reset_result.moved:
            verb = "Would move" if args.dry_run else "Moved"
            print(f"[OK] {verb}: {rel}")
        for rel in reset_result.skipped:
            print(f"[INFO] reset-day: {rel}")
        for err in reset_result.errors:
            print(f"[ERROR] reset-day: {err}", file=sys.stderr)
        if reset_result.errors:
            sys.exit(1)
        if reset_result.backup_dir and not args.dry_run:
            print(f"[OK] Day artifacts archived to: {reset_result.backup_dir}")
            prior = revert_last_run_date_if_matches(repo_root, date_str)
            if prior is not None:
                print(f"[OK] state/run_daily_state.json last_date set to: {prior}")
            elif not (repo_root / "state" / "run_daily_state.json").exists():
                print("[OK] state/run_daily_state.json cleared (no prior journal entry).")
        if args.reset_day_only:
            if args.dry_run:
                print("[DRY-RUN] reset-day-only: no files moved; exiting.")
            else:
                print(f"[OK] reset-day-only complete for {date_str} (id: {date_id})")
            return

    use_week_arc = bool(args.use_week_arc) and not bool(args.no_week_arc)
    week_arc_doc = None
    if use_week_arc:
        try:
            week_arc_doc = ensure_week_arc(
                date_id,
                repo_root=repo_root,
                journal_dir=Path("journal-entries"),
                narrations_dir=Path("narrations"),
                model=(args.model or "").strip() or None,
                refresh=bool(args.refresh_week_arc),
            )
            if week_arc_doc:
                print(
                    f"[Week arc] Plan week_{week_arc_doc.get('week_start_date_id')} "
                    f"({week_arc_doc.get('week_start_date_id')}–{week_arc_doc.get('week_end_date_id')}).",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(f"[WARN] Week arc: {exc}", file=sys.stderr)

    generating_narration = not args.skip_existing or not narration_path.exists()

    _mode_decision = resolve_run_daily_modes(
        dialogue_arg=bool(args.dialogue),
        no_dialogue_arg=bool(args.no_dialogue),
        long_conversation_arg=bool(args.long_conversation),
        no_long_conversation_arg=bool(args.no_long_conversation),
        narration_path=narration_path,
        week_arc_active=bool(use_week_arc and week_arc_doc and generating_narration),
        week_arc_day_plan=get_day_plan(week_arc_doc, date_id) if week_arc_doc else None,
    )
    dialogue_effective = _mode_decision.dialogue_effective
    long_conversation_effective = _mode_decision.long_conversation_effective
    if _mode_decision.week_arc_recommended_label:
        print(
            f"[Week arc] Recommended mode for this run: "
            f"{_mode_decision.week_arc_recommended_label}",
            file=sys.stderr,
        )
    # Sora outputs one combined video; google/fal need assembly
    out_prefix = (
        args.output_prefix or DEFAULT_VIDEO_OUTPUT_PREFIX
    ).strip() or DEFAULT_VIDEO_OUTPUT_PREFIX
    output_video = (
        Path("output") / f"video_{date_id}.mp4"
        if args.vendor == "sora"
        else Path("output") / output_video_filename(out_prefix, date_id)
    )

    if regen_segments:
        if args.narration_only:
            print(
                "[ERROR] --regenerate-video-segments cannot be used with --narration-only.",
                file=sys.stderr,
            )
            sys.exit(1)
        if args.vendor == "sora":
            print(
                "[ERROR] --regenerate-video-segments is not supported for Sora (single combined video).",
                file=sys.stderr,
            )
            sys.exit(1)
        if not narration_path.exists():
            print(
                f"[ERROR] Narration required for segment regen: {narration_path}",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            nar = json.loads(narration_path.read_text(encoding="utf-8"))
            n_script = len(nar.get("narration_script") or [])
        except (json.JSONDecodeError, OSError) as e:
            print(f"[ERROR] Could not read narration for segment regen: {e}", file=sys.stderr)
            sys.exit(1)
        if n_script < 1:
            print(
                "[ERROR] Narration has no segments; cannot regenerate video clips.", file=sys.stderr
            )
            sys.exit(1)
        bad = [i for i in regen_segments if i < 1 or i > n_script]
        if bad:
            print(
                f"[ERROR] Segment index out of range (narration has {n_script} segments): {bad}",
                file=sys.stderr,
            )
            sys.exit(1)

    if not args.no_preflight:
        pf = preflight_run_daily(
            repo_root=repo_root,
            date_id=date_id,
            narration_path=narration_path,
            audio_final=audio_final,
            vendor=args.vendor,
            narration_only=args.narration_only,
            skip_existing=args.skip_existing,
            dialogue_effective=dialogue_effective,
            long_conversation_effective=long_conversation_effective,
            strict=args.strict_preflight,
        )
        for w in pf.warnings:
            print(f"[WARN] preflight: {w}", file=sys.stderr)
        for e in pf.errors:
            print(f"[ERROR] preflight: {e}", file=sys.stderr)
        if pf.errors:
            sys.exit(1)

    # ---- DRY RUN: show plan and exit ----
    if args.dry_run:
        print("[DRY-RUN] Full plan (no steps will be executed):")
        print(f"  Date: {date_str} (id: {date_id})")
        if args.reset_day:
            print(
                "  Reset-day: archive narrations, audio, movie-images, output for this date (see [OK] Would move above)"
            )
        print(f"  Vendor: {args.vendor}")
        print(f"  Output: {output_video}")
        if args.narration_only:
            steps = []
            if not xml_path.exists():
                steps.append("1. Scrape journal XML")
            else:
                steps.append("1. Scrape (skip; exists)")
            if not args.skip_existing or not narration_path.exists():
                step2 = "2. Generate narration (two-phase"
                if args.model:
                    step2 += f", model: {args.model}"
                if args.no_focus_topic:
                    step2 += ", no automatic focus topic"
                if dialogue_effective:
                    step2 += ", character dialogue"
                if long_conversation_effective:
                    step2 += ", long-conversation pack"
                steps.append(step2 + ")")
            else:
                steps.append("2. Narration (skip; exists)")
            steps.append(
                "STOP — narration-only (no audio, video, assembly, upload; last-run state unchanged)"
            )
            for s in steps:
                print(f"  {s}")
            return
        steps = []
        if not xml_path.exists():
            steps.append("1. Scrape journal XML")
        else:
            steps.append("1. Scrape (skip; exists)")
        if not args.skip_existing or not narration_path.exists():
            step2 = "2. Generate narration (two-phase"
            if args.model:
                step2 += f", model: {args.model}"
            if args.no_focus_topic:
                step2 += ", no automatic focus topic"
            if dialogue_effective:
                step2 += ", character dialogue"
            if long_conversation_effective:
                step2 += ", long-conversation pack"
            steps.append(step2 + ")")
        else:
            steps.append("2. Narration (skip; exists)")
        audio_stale = (
            args.skip_existing
            and audio_final.exists()
            and narration_audio_segment_mismatch(date_id, narration_path)
        )
        if not args.skip_existing or not audio_final.exists() or audio_stale:
            step3 = f"3. Generate audio (narration-to-mp3, {args.tts})"
            if audio_stale:
                step3 += " [re-sync: narration vs audio segment count]"
            steps.append(step3)
        else:
            steps.append("3. Audio (skip; exists)")
        video_dir = Path("movie-images") / date_id
        durations_path = Path(f"audio/{date_id}/durations.json")
        try:
            n_segments = (
                len(json.loads(durations_path.read_text())) if durations_path.exists() else 0
            )
        except Exception:
            n_segments = 0
        clips = sorted(video_dir.glob("*.mp4")) if video_dir.exists() else []
        need_video = args.vendor == "sora" and (not args.skip_existing or not output_video.exists())
        if not need_video and args.vendor != "sora":
            need_video = not args.skip_existing or len(clips) != n_segments
        if regen_segments:
            seg_s = ",".join(str(i) for i in regen_segments)
            steps.append(
                f"4. Regenerate video segments [{seg_s}] ({args.vendor}): "
                f"move listed clips + copy final output to movie-images/{date_id}/regen_backup_<timestamp>/, "
                f"then narration-to-video --segments {seg_s} (up to 10 parallel jobs; fallback 1 on error)"
            )
            reg_asm = "5. Assemble final video (required after segment regen)"
            if args.ambient:
                reg_asm += " (with --ambient if narration requests ambience)"
            steps.append(reg_asm)
        elif need_video:
            vstep = f"4. Generate video ({args.vendor})"
            if args.vendor in ("google", "fal"):
                vstep += " (up to 10 parallel clip jobs; falls back to 1 on error)"
            steps.append(vstep)
        else:
            steps.append("4. Video (skip; exists)")
        if not regen_segments:
            force_asm = args.force_assembly and args.vendor != "sora" and not args.narration_only
            if args.vendor != "sora" and (
                force_asm or not args.skip_existing or not output_video.exists()
            ):
                asm = "5. Assemble final video"
                if args.ambient:
                    asm += " (with --ambient if narration requests ambience)"
                if force_asm:
                    asm += " (--force-assembly)"
                steps.append(asm)
            else:
                steps.append("5. Assembly (skip; exists)")
        if args.upload:
            steps.append("6. Upload to YouTube")
        for s in steps:
            print(f"  {s}")
        if regen_segments:
            print(
                "  Note: segment regen does not update state/run_daily_state.json last_date "
                "(use plain run for that).",
            )
        return

    # 1) Scrape if needed
    if not xml_path.exists():
        if not scrape_entry(date_str):
            print(f"[ERROR] Journal not available: {date_str}")
            sys.exit(1)

    # 2) Generate narration (two-phase only; writes narration<date_id>.json)
    if not args.skip_existing or not narration_path.exists():
        if (
            dialogue_effective
            and not args.dialogue
            and not args.no_dialogue
            and not args.long_conversation
        ):
            print(
                "[INFO] Narration JSON requests dialogue mode; enabling --dialogue for generation.",
                file=sys.stderr,
            )
        gen_cmd = [sys.executable, "generate-narration-two-phase.py", date_id]
        if args.profile:
            gen_cmd.extend(["--profile", args.profile])
        if args.source_text_file:
            gen_cmd.extend(["--source-text-file", args.source_text_file])
        if args.llm_provider != "claude":
            gen_cmd.extend(["--llm-provider", args.llm_provider])
        if args.model:
            gen_cmd.extend(["--model", args.model])
        if args.no_focus_topic:
            gen_cmd.append("--no-focus-topic")
        if use_week_arc:
            gen_cmd.append("--use-week-arc")
            if args.refresh_week_arc:
                gen_cmd.append("--refresh-week-arc")
        if long_conversation_effective:
            gen_cmd.append("--long-conversation")
        elif dialogue_effective:
            gen_cmd.append("--dialogue")
        if not run_cmd(gen_cmd, "Generating narration"):
            sys.exit(1)

    if args.narration_only:
        print(f"\n[OK] Narration-only complete: {narration_path}")
        print("(Skipped: audio, video, assembly, upload; last-run pipeline state unchanged.)")
        return

    # 3) Generate audio (re-run if narration segment count != existing durations, even when final.mp3 exists)
    if (
        not args.skip_existing
        or not audio_final.exists()
        or narration_audio_segment_mismatch(date_id, narration_path)
    ):
        if (
            args.skip_existing
            and audio_final.exists()
            and narration_audio_segment_mismatch(date_id, narration_path)
        ):
            print(
                "[WARN] Narration segment count differs from existing audio/durations; "
                "re-running narration-to-mp3 to sync.",
                file=sys.stderr,
            )
        if not run_cmd(
            [sys.executable, "narration-to-mp3.py", date_id, "--tts", args.tts],
            "Generating narration audio",
        ):
            sys.exit(1)

    # run-daily does not invoke map_intro.py (assemble uses 01..NN.mp3 + final.mp3; optional 00_intro still supported).

    # 4) Generate video clips (or Sora combined video)
    if args.vendor == "sora":
        need_video = not args.skip_existing or not output_video.exists()
    else:
        video_dir = Path("movie-images") / date_id
        clips = sorted(video_dir.glob("*.mp4")) if video_dir.exists() else []
        durations_path = Path(f"audio/{date_id}/durations.json")
        n_segments = len(json.loads(durations_path.read_text())) if durations_path.exists() else 0
        need_video = not args.skip_existing or len(clips) != n_segments

    produced_video_this_run = False
    if regen_segments:
        print(
            f"\n>> Segment regen {regen_segments}: moving listed clips to backup (if present), "
            "copying final output to backup (if present)",
        )
        backup_dir = backup_clips_and_output_for_regen(date_id, regen_segments, output_video)
        print(f"[OK] Backup folder: {backup_dir}")
        cmd = [
            sys.executable,
            "narration-to-video.py",
            date_id,
            "--vendor",
            args.vendor,
            "--segments",
            ",".join(str(i) for i in regen_segments),
        ]
        if args.shorts:
            cmd.append("--shorts")
        if args.vendor == "fal":
            cmd.append("--reuse-scene-anchor-stills")
        if not run_cmd(cmd, f"Regenerating video segments {regen_segments} ({args.vendor})"):
            sys.exit(1)
        produced_video_this_run = True
    elif need_video:
        cmd = [sys.executable, "narration-to-video.py", date_id, "--vendor", args.vendor]
        if args.shorts:
            cmd.append("--shorts")
        if args.vendor == "fal":
            cmd.append("--reuse-scene-anchor-stills")
        if not run_cmd(cmd, f"Generating video ({args.vendor})"):
            sys.exit(1)
        produced_video_this_run = True  # we just ran the video step

    # 5) Assemble final video (google/fal only; Sora produces combined output)
    did_produce_output = False
    if args.force_assembly and args.vendor == "sora":
        print(
            "[WARN] --force-assembly applies to fal/google assembly only; ignored for Sora.",
            file=sys.stderr,
        )
    if args.vendor != "sora":
        force_assemble_after_regen = bool(regen_segments)
        if (
            force_assemble_after_regen
            or args.force_assembly
            or (not args.skip_existing or not output_video.exists())
        ):
            movie_cmd = [
                sys.executable,
                "videos-mp3-to-movie.py",
                date_id,
                "--output-prefix",
                out_prefix,
            ]
            if args.shorts:
                movie_cmd.append("--shorts")
            if args.ambient:
                movie_cmd.append("--ambient")
            movie_cmd.extend(["--audio-tail-pad-seconds", str(args.audio_tail_pad_seconds)])
            if not run_cmd(movie_cmd, "Assembling final video"):
                sys.exit(1)
            did_produce_output = output_video.exists()
    else:
        did_produce_output = produced_video_this_run

    # Record which vendors were used (log + manifest) when we produced this output
    if output_video.exists() and did_produce_output:
        log_video_complete(
            date_id,
            output_video,
            narration_vendor="openai",
            tts_vendor=args.tts,
            video_vendor=args.vendor,
        )
        manifest_path = video_manifest.write_pipeline_manifest(
            output_video,
            date_id=date_id,
            aspect_ratio="9:16" if args.shorts else "16:9",
            narration_vendor="openai",
            tts_vendor=args.tts,
            video_vendor=args.vendor,
        )
        if not args.no_preflight:
            nar_post: dict | None = None
            nar_post_path = Path("narrations") / f"narration{date_id}.json"
            if nar_post_path.exists():
                try:
                    nar_post = json.loads(nar_post_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    nar_post = None
            po = postflight_output_video(output_video, narration_data=nar_post)
            for w in po.warnings:
                print(f"[WARN] postflight: {w}", file=sys.stderr)
            for e in po.errors:
                print(f"[ERROR] postflight: {e}", file=sys.stderr)
            if po.errors:
                sys.exit(1)

    # 6) Upload to YouTube
    if args.upload:
        if not output_video.exists():
            print(f"[ERROR] Output video not found: {output_video}")
            sys.exit(1)
        try:
            from pipeline.youtube_metadata import youtube_title_and_description
            from pipeline.youtube_upload_guard import (
                DuplicateUploadError,
                UploadSequenceError,
            )
            from youtube_upload import upload_video

            # Prefer narration episode title for YouTube; fall back to date-based title.
            title, description = youtube_title_and_description(date_id, repo_root=repo_root)

            print("\nUploading to YouTube...")
            manifest_path = video_manifest.manifest_path_for_video(output_video)
            try:
                upload_video(
                    output_video,
                    title=title,
                    description=description,
                    privacy=args.privacy,
                    tags=["Lewis and Clark", "expedition", "history", "journal"],
                    manifest_path=manifest_path,
                    force=args.force_upload,
                    allow_gap=args.allow_upload_gap,
                )
                print("[OK] Upload complete")
            except DuplicateUploadError as e:
                # Already on YouTube — the pipeline goal is met; don't re-upload or fail.
                print(f"[SKIP] {e}")
            except UploadSequenceError as e:
                print(f"[ERROR] {e}", file=sys.stderr)
                sys.exit(1)
        except ImportError as e:
            print(
                f"[ERROR] YouTube upload failed (install google-api-python-client google-auth-oauthlib): {e}"
            )
            sys.exit(1)
        except Exception as e:
            print(f"[ERROR] Upload failed: {e}")
            sys.exit(1)

    print(f"\n[OK] Pipeline complete: {output_video}")
    # Record last successfully completed date (only after full pipeline completes without exiting early).
    # Segment regeneration is a maintenance action; do not move the "next" cursor in state.
    if regen_segments:
        print(
            "[INFO] Skipping state/run_daily_state.json last_date update (segment regeneration run).",
        )
    else:
        save_last_run_date(date_str)


if __name__ == "__main__":
    main()
