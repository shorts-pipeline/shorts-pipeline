#!/usr/bin/env python3
"""Generate MP3 narration from narration JSON. Supports pyttsx3 (local), OpenAI TTS (narrator), and FAL MiniMax for speakers with custom_voice_id (e.g. lewis, clark in voice_prompts.json)."""

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import ffmpeg
import pyttsx3

from pipeline.automation_gates import emit_gate_result, preflight_narration_to_mp3
from pipeline.fal_minimax_tts import (
    fal_tts_credentials_available,
    load_fal_custom_voice_ids,
    synthesize_fal_minimax_speech_mp3,
)
from pipeline.narration_common import load_narration_config
from pipeline.narration_utils import narration_json_expects_long_conversation_mode
from pipeline.segment_plan import build_episode_segment_plans
from pipeline.tts_post_narration_silence import (
    append_post_narration_silence_to_mp3,
    cap_post_narration_silence_in_script,
    segment_post_narration_silence_seconds,
)
from pipeline.tts_speaker_voice import (
    OPENAI_TTS_VOICES,
    resolve_openai_tts_voice_for_speaker,
    segment_audio_is_cast_dialogue_only,
    segment_should_drop_cast_dialogue,
    segment_unique_cast_dialogue_speakers,
    talking_head_subject_has_dedicated_voice,
)
from pipeline.tts_stage_directions import split_dialogue_for_tts
from pipeline.tts_text_normalize import normalize_text_for_tts
from pipeline_logging import log_api_call_with_bodies, log_file_created


def select_voice(engine, prefer_female: bool = False):
    voices = engine.getProperty("voices")
    if prefer_female:
        for v in voices:
            if "en-gb" in v.id.lower() and "female" in v.name.lower():
                return v.id
        for v in voices:
            if "female" in v.name.lower():
                return v.id
    else:
        # Prefer British male
        for v in voices:
            if "en-gb" in v.id.lower() and "male" in v.name.lower():
                return v.id
        for v in voices:
            if "en-gb" in v.id.lower():
                return v.id
    return None


def synthesize_pyttsx3(text, out_path, engine):
    engine.save_to_file(text, str(out_path))
    engine.runAndWait()


def _mp3_pcm_peak(path: Path) -> int:
    """Return max absolute PCM sample (0 = digital silence)."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-",
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        return 0
    data = proc.stdout
    n = len(data) // 2
    if n == 0:
        return 0
    samples = struct.unpack("<" + "h" * n, data[: n * 2])
    return max(abs(s) for s in samples)


def _assert_mp3_has_speech(path: Path, *, label: str) -> None:
    if _mp3_pcm_peak(path) <= 0:
        raise RuntimeError(f"{label} TTS wrote a silent MP3 ({path.name})")


def synthesize_openai(text: str, out_path: Path, voice: str = "onyx") -> None:
    """Use OpenAI TTS API (requires OPENAI_API_KEY). voice: onyx (male), nova (female)."""
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    v = (voice or "onyx").strip().lower()
    if v not in OPENAI_TTS_VOICES:
        v = "onyx"
    response = client.audio.speech.create(
        model="tts-1-hd",
        voice=v,
        input=text,
    )
    # response.stream_to_file() is broken in recent OpenAI SDK (writes duration-sized silence).
    out_path.write_bytes(response.content)
    _assert_mp3_has_speech(out_path, label="OpenAI")
    sz = out_path.stat().st_size if out_path.is_file() else 0
    log_api_call_with_bodies(
        "openai",
        "audio.speech.create",
        request_body={"model": "tts-1-hd", "voice": v, "input": text},
        response_body=f"[audio written to file path={out_path} bytes={sz}]",
        model="tts-1-hd",
        extra={"voice": v},
    )


def probe_duration(path):
    info = ffmpeg.probe(str(path))
    return float(info["format"]["duration"])


def _probe_audio_format(path: Path) -> tuple[int, int]:
    """Return (sample_rate, channels) for the first audio stream, for silence matching."""
    info = ffmpeg.probe(str(path))
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            return int(s.get("sample_rate") or 24000), int(s.get("channels") or 1)
    return 24000, 1


def _silence_mp3(out_path: Path, duration_sec: float, sample_rate: int, channels: int) -> None:
    layout = "mono" if channels == 1 else "stereo"
    src = ffmpeg.input(f"anullsrc=r={sample_rate}:cl={layout}", f="lavfi", t=float(duration_sec))
    (
        ffmpeg.output(src, str(out_path), acodec="libmp3lame", audio_bitrate="192k")
        .overwrite_output()
        .run(quiet=True)
    )


def _synthesize_dialogue_chunk(
    text: str,
    out_path: Path,
    *,
    speaker_id: str,
    use_openai: bool,
    tts_voice: str,
    voice_by_speaker: dict,
    fal_voice_ids: dict,
    engine,
    label: str,
) -> None:
    """One spoken dialogue chunk (caller supplies normalized text)."""
    sid = (speaker_id or "narrator").strip().lower() or "narrator"
    if use_openai:
        fal_cid = fal_voice_ids.get(sid)
        if fal_cid and fal_tts_credentials_available():
            print(f"Synthesizing {label} (fal minimax, speaker={sid})...")
            try:
                synthesize_fal_minimax_speech_mp3(text, out_path, fal_cid)
            except Exception as e:
                print(
                    f"[ERROR] FAL TTS failed for speaker={sid}; not falling back to OpenAI: {e}",
                    file=sys.stderr,
                )
                raise SystemExit(1) from e
        else:
            print(f"Synthesizing {label} (openai, speaker={sid})...")
            v = (
                tts_voice
                if sid == "narrator"
                else resolve_openai_tts_voice_for_speaker(voice_by_speaker, sid, tts_voice)
            )
            synthesize_openai(text, out_path, voice=v)
    else:
        print(f"Synthesizing {label} (pyttsx3, speaker={sid})...")
        synthesize_pyttsx3(text, out_path, engine)


def _append_silence_part(
    part_paths: list[Path],
    segments_dir: Path,
    *,
    idx: int,
    li: int,
    ci: int,
    pause_sec: float,
    sample_probe: Path | None,
) -> None:
    if pause_sec <= 0:
        return
    sr, ch = (
        _probe_audio_format(sample_probe) if sample_probe and sample_probe.is_file() else (24000, 1)
    )
    pause_part = segments_dir / f"_part_{idx:02}_{li:02}_{ci:02}_pause.mp3"
    _silence_mp3(pause_part, pause_sec, sr, ch)
    log_file_created(pause_part, pause_part.stat().st_size)
    part_paths.append(pause_part)


def _concat_mp3_files(part_paths: list[Path], out_mp3: Path) -> None:
    streams = [ffmpeg.input(str(p)) for p in part_paths]
    joined = ffmpeg.concat(*streams, v=0, a=1).node[0]
    (ffmpeg.output(joined, str(out_mp3), acodec="libmp3lame").overwrite_output().run(quiet=True))


def _tts_timing_defaults(config_path: Path | None) -> tuple[float, float]:
    """Return (narration_dialogue_gap_seconds, talking_head_tail_silence_seconds) from config."""
    cfg = load_narration_config(config_path if config_path and config_path.is_file() else None)
    gap = float(cfg.get("narration_dialogue_gap_seconds", 1.0))
    tail = float(cfg.get("talking_head_tail_silence_seconds", 0.5))
    return max(0.0, gap), max(0.0, tail)


def main():
    pre_config_path = Path("config/narration_config.json")
    default_gap, default_tail = _tts_timing_defaults(pre_config_path)

    parser = argparse.ArgumentParser(
        description="Generate MP3 narration from narration JSON. Supports pyttsx3 (local) or OpenAI TTS."
    )
    parser.add_argument("date", help="Date identifier, e.g. 18030830")
    parser.add_argument("--audio-dir", default="audio", help="Directory for audio")
    parser.add_argument(
        "--config",
        default="config/narration_config.json",
        help="Narration config (voice_by_speaker for dialogue mode)",
    )
    parser.add_argument(
        "--tts",
        choices=["pyttsx3", "openai"],
        default="openai",
        help="TTS engine: openai (default, higher quality) or pyttsx3 (local, free)",
    )
    parser.add_argument(
        "--narration-dialogue-gap",
        type=float,
        default=default_gap,
        metavar="SEC",
        help=(
            "Seconds of silence before dialogue clips (talking_head / cast-only dialogue segments, "
            "or after lead-in narration). Default from config narration_dialogue_gap_seconds. Use 0 to disable."
        ),
    )
    parser.add_argument(
        "--segments",
        type=str,
        default=None,
        metavar="N,...",
        help="1-based segment indices to regenerate only (e.g. 3). Other segments must already exist under audio/<date>/segments/. Still rewrites durations.json and final.mp3.",
    )
    parser.add_argument(
        "--talking-head-tail-silence",
        type=float,
        default=default_tail,
        metavar="SEC",
        help=(
            "Trailing silence after dialogue for talking_head segments only. "
            "Default from config talking_head_tail_silence_seconds. Use 0 to disable."
        ),
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip preflight checks (TTS layout, partial --segments prerequisites).",
    )
    parser.add_argument(
        "--strict-preflight",
        action="store_true",
        help="Treat preflight warnings (e.g. stale durations.json) as errors.",
    )
    args = parser.parse_args()

    cfg_path = Path(args.config).expanduser()
    if cfg_path.resolve() != pre_config_path.resolve():
        cfg_gap, cfg_tail = _tts_timing_defaults(cfg_path)
        if args.narration_dialogue_gap == default_gap:
            args.narration_dialogue_gap = cfg_gap
        if args.talking_head_tail_silence == default_tail:
            args.talking_head_tail_silence = cfg_tail

    date_id = args.date
    json_dir = Path("narrations")
    json_file = Path(json_dir / f"narration{date_id}.json")
    if not json_file.exists():
        print(f"[ERROR] JSON file not found: {json_file}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(json_file.read_text(encoding="utf-8"))
    long_conversation_mode = narration_json_expects_long_conversation_mode(data)
    script = data.get("narration_script")
    if isinstance(script, list):
        dropped = cap_post_narration_silence_in_script(script)
        if dropped:
            print(
                f"[WARN] Capped post_narration_silence_seconds: removed from {dropped} segment(s) "
                f"(at most 2 per episode, ~12s total).",
                file=sys.stderr,
            )
    use_female_voice = bool(data.get("focus_topic"))

    narr_config = load_narration_config(cfg_path if cfg_path.is_file() else None)
    stage_pause_sec = float(narr_config.get("tts_stage_direction_pause_seconds") or 0.75)
    voice_by_speaker = narr_config.get("voice_by_speaker") or {}
    if not isinstance(voice_by_speaker, dict):
        voice_by_speaker = {}

    repo_root = Path(__file__).resolve().parent
    voice_prompts_path = repo_root / "character-portraits" / "voice_prompts.json"
    use_openai = args.tts == "openai"
    fal_voice_ids = load_fal_custom_voice_ids(voice_prompts_path) if use_openai else {}
    fal_cred_ok = fal_tts_credentials_available() if use_openai else False
    if use_openai and (fal_voice_ids.get("lewis") or fal_voice_ids.get("clark")):
        if fal_tts_credentials_available():
            print(
                "Using FAL MiniMax speech-02-hd for lewis/clark dialogue (custom_voice_id from voice_prompts.json).",
                file=sys.stderr,
            )
        else:
            print(
                "[WARN] FAL_KEY not set: lewis/clark dialogue will use OpenAI voice_by_speaker instead of MiniMax.",
                file=sys.stderr,
            )

    # Prepare audio folder: audio/<date_id>/ with segments/ subdir for per-segment MP3s
    audio_dir = Path("audio") / date_id
    segments_dir = audio_dir / "segments"
    audio_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(exist_ok=True)

    # Setup TTS engine (female narrator when focus_topic is set)
    tts_voice = "nova" if (use_openai and use_female_voice) else "onyx"
    engine = pyttsx3.init() if not use_openai else None
    if engine:
        voice = select_voice(engine, prefer_female=use_female_voice)
        if voice:
            engine.setProperty("voice", voice)
        engine.setProperty("rate", 150)

    if use_female_voice:
        print(f"Using female narrator (focus_topic: {data.get('focus_topic')})", file=sys.stderr)

    segment_indices: set[int] | None = None
    if args.segments:
        segment_indices = set()
        for part in args.segments.replace(" ", "").split(","):
            if not part:
                continue
            try:
                segment_indices.add(int(part, 10))
            except ValueError:
                print(f"[ERROR] --segments: invalid integer {part!r}", file=sys.stderr)
                sys.exit(2)
        if not segment_indices:
            segment_indices = None
        else:
            print(f"Partial regenerate: segments {sorted(segment_indices)} only.", file=sys.stderr)

    if not args.no_preflight:
        pf = preflight_narration_to_mp3(
            repo_root=Path.cwd(),
            date_id=date_id,
            segment_indices=segment_indices,
            strict=args.strict_preflight,
        )
        if emit_gate_result(pf, strict=args.strict_preflight):
            sys.exit(1)

    episode = build_episode_segment_plans(data, date_id=date_id, repo_root=Path.cwd())

    # 1) Generate individual MP3s
    durations = []
    mp3_paths = []
    for plan in episode:
        idx = plan.index
        entry = plan.row
        dialogue = entry.get("dialogue")
        dialogue_lines: list[tuple[int, dict]] = []
        if isinstance(dialogue, list):
            for li, line in enumerate(dialogue):
                if not isinstance(line, dict):
                    continue
                t = (line.get("text") or "").strip()
                if not t:
                    continue
                dialogue_lines.append((li, line))
        use_dialogue = len(dialogue_lines) > 0

        filename = plan.audio_mp3.name
        out_mp3 = plan.audio_mp3

        if segment_indices is not None and idx not in segment_indices:
            if not out_mp3.is_file():
                print(
                    f"[ERROR] Segment {idx}: missing {out_mp3}; cannot use --segments without existing file.",
                    file=sys.stderr,
                )
                sys.exit(1)
            dur = probe_duration(out_mp3)
            durations.append({"file": f"segments/{filename}", "duration": round(dur, 3)})
            mp3_paths.append(out_mp3)
            continue

        is_th_mode = plan.is_talking_head
        th_subj = plan.talking_head_subject
        th_drive = plan.talking_head_drive_mp3
        voice_kw = dict(
            fal_voice_ids=fal_voice_ids,
            voice_by_speaker=voice_by_speaker,
            narrator_openai_voice=tts_voice,
            use_openai_tts=use_openai,
            fal_credentials_ok=fal_cred_ok,
        )
        if use_dialogue and segment_should_drop_cast_dialogue(
            entry,
            **voice_kw,
            long_conversation_mode=long_conversation_mode,
        ):
            cast_ids = segment_unique_cast_dialogue_speakers(entry)
            detail = (
                f"talking_head subject {th_subj!r} has no dedicated TTS voice"
                if is_th_mode and len(cast_ids) == 1
                else (
                    f"cast speakers {cast_ids!r} lack dedicated voice or exceed one speaker per segment"
                    if cast_ids
                    else "invalid cast dialogue layout"
                )
            )
            print(
                f"[WARN] Segment {idx}: {detail}; using narrator-only audio for this segment "
                "and dropping character dialogue TTS. Re-run narration-to-video so Wan covers this beat.",
                file=sys.stderr,
            )
            dialogue_lines = []
            use_dialogue = False

        will_produce_th_drive = (
            is_th_mode
            and use_dialogue
            and talking_head_subject_has_dedicated_voice(
                th_subj,
                fal_voice_ids=fal_voice_ids,
                voice_by_speaker=voice_by_speaker,
                narrator_openai_voice=tts_voice,
                use_openai_tts=use_openai,
                fal_credentials_ok=fal_cred_ok,
            )
        )
        if not will_produce_th_drive and th_drive.is_file():
            try:
                th_drive.unlink()
            except OSError:
                pass
        is_th = will_produce_th_drive
        cast_dialogue_only = use_dialogue and segment_audio_is_cast_dialogue_only(entry, **voice_kw)

        if use_dialogue:
            part_paths: list[Path] = []
            dialogue_part_paths: list[Path] = []
            narr_part: Path | None = None
            gap_part: Path | None = None
            narr_text = normalize_text_for_tts((entry.get("narration") or "").strip())
            if narr_text and not is_th and not cast_dialogue_only:
                narr_part = segments_dir / f"_part_{idx:02}_narr.mp3"
                print(f"Synthesizing {filename} lead-in narration ({args.tts})...")
                if use_openai:
                    # Documentary narrator: always onyx/nova (not voice_by_speaker["narrator"], often alloy).
                    synthesize_openai(narr_text, narr_part, voice=tts_voice)
                else:
                    synthesize_pyttsx3(narr_text, narr_part, engine)
                log_file_created(narr_part, narr_part.stat().st_size)
                part_paths.append(narr_part)
            elif narr_text and (is_th or cast_dialogue_only):
                mode_note = "talking_head" if is_th else "cast dialogue only (dedicated voice)"
                print(
                    f"Skipping lead-in narration TTS for {filename} ({mode_note}); "
                    "segment is gap + dialogue only.",
                    file=sys.stderr,
                )

            gap = float(args.narration_dialogue_gap or 0.0)
            if (
                gap > 0.0
                and dialogue_lines
                and (narr_part is not None or is_th or cast_dialogue_only)
            ):
                sr, ch = _probe_audio_format(narr_part) if narr_part is not None else (24000, 1)
                gap_part = segments_dir / f"_part_{idx:02}_gap.mp3"
                _silence_mp3(gap_part, gap, sr, ch)
                log_file_created(gap_part, gap_part.stat().st_size)
                part_paths.append(gap_part)

            for li, line in dialogue_lines:
                sid = (line.get("speaker_id") or "narrator").strip().lower() or "narrator"
                raw_text = (line.get("text") or "").strip()
                chunks = split_dialogue_for_tts(raw_text, stage_pause_sec=stage_pause_sec)
                if not chunks:
                    continue
                for ci, (spoken, pause_after) in enumerate(chunks):
                    text = normalize_text_for_tts(spoken)
                    if text:
                        part = segments_dir / f"_part_{idx:02}_{li:02}_{ci:02}.mp3"
                        _synthesize_dialogue_chunk(
                            text,
                            part,
                            speaker_id=sid,
                            use_openai=use_openai,
                            tts_voice=tts_voice,
                            voice_by_speaker=voice_by_speaker,
                            fal_voice_ids=fal_voice_ids,
                            engine=engine,
                            label=f"{filename} part {li}.{ci}",
                        )
                        log_file_created(part, part.stat().st_size)
                        part_paths.append(part)
                        dialogue_part_paths.append(part)
                    if pause_after > 0:
                        probe = dialogue_part_paths[-1] if dialogue_part_paths else None
                        _append_silence_part(
                            part_paths,
                            segments_dir,
                            idx=idx,
                            li=li,
                            ci=ci,
                            pause_sec=pause_after,
                            sample_probe=probe,
                        )
                        if raw_text and not text:
                            print(
                                f"Stage-direction pause ({pause_after:.2f}s) after {filename} "
                                f"dialogue line {li} (no spoken text in chunk).",
                                file=sys.stderr,
                            )

            tail_sec = float(args.talking_head_tail_silence or 0.0)
            if is_th and dialogue_part_paths and tail_sec > 0.0:
                sr_tl, ch_tl = _probe_audio_format(dialogue_part_paths[-1])
                tail_part = segments_dir / f"_part_{idx:02}_tail.mp3"
                _silence_mp3(tail_part, tail_sec, sr_tl, ch_tl)
                log_file_created(tail_part, tail_part.stat().st_size)
                part_paths.append(tail_part)

            if not part_paths:
                use_dialogue = False
            else:
                print(f"Concatenating {len(part_paths)} clip(s) -> {filename}...")
                _concat_mp3_files(part_paths, out_mp3)
                if is_th and dialogue_part_paths:
                    # Same bytes as segment MP3: gap silence + dialogue only (no lead-in narration TTS).
                    th_drive = segments_dir / f"{idx:02d}_talking_head_drive.mp3"
                    shutil.copyfile(out_mp3, th_drive)
                    log_file_created(th_drive, th_drive.stat().st_size)
                    print(
                        f"Wrote talking-head drive audio (copy of segment, gap+dialogue) -> {th_drive.name}",
                        file=sys.stderr,
                    )
                for p in part_paths:
                    try:
                        p.unlink()
                    except OSError:
                        pass
                log_file_created(out_mp3, out_mp3.stat().st_size)

        if not use_dialogue:
            text = normalize_text_for_tts(entry.get("narration", "").strip())
            if not text:
                continue
            print(f"Synthesizing {filename} ({args.tts})...")
            if use_openai:
                synthesize_openai(text, out_mp3, voice=tts_voice)
            else:
                synthesize_pyttsx3(text, out_mp3, engine)
            log_file_created(out_mp3, out_mp3.stat().st_size)

        post_narr_sec = segment_post_narration_silence_seconds(entry)
        if post_narr_sec > 0.0 and out_mp3.is_file():
            print(
                f"Appending {post_narr_sec:.2f}s post-narration silence to {filename}...",
                file=sys.stderr,
            )
            append_post_narration_silence_to_mp3(
                out_mp3,
                post_narr_sec,
                work_dir=segments_dir,
                segment_index=idx,
            )
            log_file_created(out_mp3, out_mp3.stat().st_size)

        if not out_mp3.is_file():
            continue

        dur = probe_duration(out_mp3)
        durations.append({"file": f"segments/{filename}", "duration": round(dur, 3)})
        mp3_paths.append(out_mp3)

    # 2) Write durations JSON
    durations_file = audio_dir / "durations.json"
    durations_file.write_text(json.dumps(durations, indent=2))
    print(f"Wrote durations to {durations_file}")

    # 3) Concatenate into final MP3
    print("Concatenating into final audio...")
    streams = [ffmpeg.input(str(p)) for p in mp3_paths]
    concat_audio = ffmpeg.concat(*streams, v=0, a=1).node[0]
    final_mp3 = audio_dir / "final.mp3"
    (
        ffmpeg.output(concat_audio, str(final_mp3), acodec="libmp3lame")
        .overwrite_output()
        .run(quiet=False)
    )
    log_file_created(final_mp3, final_mp3.stat().st_size)
    print(f"Final audio written to {final_mp3}")

    try:
        from pipeline.episode_state import refresh_episode_state_sidecar

        refresh_episode_state_sidecar(Path.cwd(), date_id, source="narration-to-mp3")
    except Exception as e:
        print(f"[WARN] episode state sidecar: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
