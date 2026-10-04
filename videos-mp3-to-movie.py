#!/usr/bin/env python3
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import ffmpeg

from pipeline.ambient_audio import maybe_mix_ambient_under_voice
from pipeline.narration_utils import get_mid_episode_map_insertion, load_narration
from pipeline.output_naming import DEFAULT_VIDEO_OUTPUT_PREFIX, output_video_filename
from pipeline_logging import log_file_created


def _segment_key_from_video_filename(name: str) -> str | None:
    """e.g. '03.mp4' -> '3'; '00_intro.mp4' / non-numeric -> None."""
    m = re.match(r"^(\d{2})\.mp4$", name, re.I)
    if not m:
        return None
    n = int(m.group(1), 10)
    if n == 0:
        return None
    return str(n)


def _load_fal_segment_cover_config(video_dir: Path) -> dict:
    """Optional sidecar from fal when i2v falls back to portrait-only (see video_vendors/fal.py)."""
    p = video_dir / "fal_segment_covers.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    segs = data.get("segments")
    if not isinstance(segs, dict):
        return {}
    return {"segments": segs}


def _effective_opening_cover(
    seg_key: str | None,
    cover_data: dict,
    args,
    target_dur: float,
) -> dict | None:
    """Return {cover_mode, cover_seconds} or None."""
    if getattr(args, "no_opening_cover", False) or not seg_key:
        return None
    seg = cover_data.get("segments", {}).get(seg_key)
    if not isinstance(seg, dict):
        return None
    mode = str(seg.get("cover_mode", "black")).strip().lower()
    if mode not in ("black", "map"):
        mode = "black"
    if getattr(args, "opening_cover_mode", None) is not None:
        mode = args.opening_cover_mode
    sec = float(seg.get("cover_seconds", 1.0))
    if getattr(args, "opening_cover_seconds", None) is not None:
        sec = args.opening_cover_seconds
    sec = min(sec, max(0.05, target_dur - 0.02))
    if sec <= 0:
        return None
    return {"cover_mode": mode, "cover_seconds": sec}


def _apply_opening_cover(
    stream,
    *,
    cfg: dict,
    date_id: str,
    video_dir: Path,
    target_w: int,
    target_h: int,
    target_fps: int,
):
    """
    Draw opaque black or a route map over the first N seconds of this segment's video
    (after time-stretch). Audio and segment duration are unchanged.
    """
    t_eff = float(cfg["cover_seconds"])
    mode = cfg["cover_mode"]
    if mode == "black":
        black = ffmpeg.input(
            f"color=c=black:s={target_w}x{target_h}:r={target_fps}:d={t_eff + 0.1}",
            f="lavfi",
        ).video
        return ffmpeg.overlay(stream, black, enable=f"lte(t,{t_eff})")

    # map
    map_path: Path | None = None
    try:
        from map_intro import generate_mid_episode_map
    except ImportError:
        generate_mid_episode_map = None
    if generate_mid_episode_map:
        try:
            map_path = generate_mid_episode_map(date_id, "parchment_overlay", t_eff, video_dir)
        except Exception as e:
            print(f"[WARN] Opening-cover map skipped: {e}", file=sys.stderr)
    if map_path is None:
        black = ffmpeg.input(
            f"color=c=black:s={target_w}x{target_h}:r={target_fps}:d={t_eff + 0.1}",
            f="lavfi",
        ).video
        return ffmpeg.overlay(stream, black, enable=f"lte(t,{t_eff})")
    map_in = ffmpeg.input(str(map_path), loop=1, t=t_eff + 0.05, framerate=target_fps).video
    map_scaled = (
        map_in.filter("scale", target_w, target_h, force_original_aspect_ratio="decrease")
        .filter("pad", target_w, target_h, "(ow-iw)/2", "(oh-ih)/2")
        .filter("format", pix_fmts="yuv420p")
    )
    return ffmpeg.overlay(stream, map_scaled, enable=f"lte(t,{t_eff})")


def _write_latest_shortcut(shortcut_name: str, target: Path) -> None:
    """Write a small .url file in the repo root pointing at target."""
    try:
        root = Path(__file__).resolve().parent
        shortcut_path = root / shortcut_name
        url = f"file:///{target.resolve().as_posix()}"
        contents = f"[InternetShortcut]\nURL={url}\n"
        shortcut_path.write_text(contents, encoding="utf-8")
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description="Assemble video clips + audio into final MP4.")
    parser.add_argument("date_id", help="Date identifier, e.g. 18030830")
    parser.add_argument(
        "--shorts",
        action="store_true",
        default=True,
        help="Output 9:16 vertical for YouTube Shorts (720x1280). Default: on.",
    )
    parser.add_argument(
        "--wide-screen",
        action="store_false",
        dest="shorts",
        help="Output 16:9 landscape (1280x720) instead of Shorts.",
    )
    parser.add_argument(
        "--exclude-segments",
        type=str,
        default=None,
        metavar="N,M,...",
        help="Exclude narrative segments by 1-based number (e.g. 6 or 6,3). Video and audio for those segments are omitted from the final movie.",
    )
    parser.add_argument(
        "--no-opening-cover",
        action="store_true",
        help="Ignore movie-images/<date>/fal_segment_covers.json (no black/map mask on segment openings).",
    )
    parser.add_argument(
        "--opening-cover-seconds",
        type=float,
        default=None,
        metavar="SEC",
        help="Override cover duration for all segments listed in fal_segment_covers.json.",
    )
    parser.add_argument(
        "--opening-cover-mode",
        choices=("black", "map"),
        default=None,
        help="Override cover mode for all segments in fal_segment_covers.json.",
    )
    parser.add_argument(
        "--ambient",
        action="store_true",
        default=False,
        help="When narration audio_design requests light/moderate ambience, mix a bed from ambient_library/ (repo root).",
    )
    parser.add_argument(
        "--audio-tail-pad-seconds",
        type=float,
        default=1.5,
        metavar="SEC",
        help="Append SEC seconds of silence after the full voice track before mux (so the last line does not cut off instantly). Default 1.5; use 0 to disable.",
    )
    parser.add_argument(
        "--output-prefix",
        default=DEFAULT_VIDEO_OUTPUT_PREFIX,
        metavar="PREFIX",
        help=f"Output MP4 basename prefix before _<date>_video (default: {DEFAULT_VIDEO_OUTPUT_PREFIX}).",
    )
    parser.add_argument(
        "--clips-subdir",
        default="",
        metavar="DIR",
        help=(
            "Read segment MP4s from movie-images/<date_id>/<DIR>/ instead of the date root "
            "(e.g. anchor_preview for cheap anchor+TTS preview assembly)."
        ),
    )
    args = parser.parse_args()
    date_id = args.date_id

    # Paths based on the provided date identifier
    _clips_sub = (args.clips_subdir or "").strip().replace("\\", "/").strip("/")
    VIDEO_DIR = Path(f"movie-images/{date_id}")
    if _clips_sub:
        VIDEO_DIR = VIDEO_DIR / _clips_sub
    DURATIONS_JSON = Path(f"audio/{date_id}/durations.json")
    FINAL_AUDIO = Path(f"audio/{date_id}/final.mp3")
    TMP_VIDEO = Path(f"temp/tmp_video_{date_id}.mp4")
    prefix = (
        args.output_prefix or DEFAULT_VIDEO_OUTPUT_PREFIX
    ).strip() or DEFAULT_VIDEO_OUTPUT_PREFIX
    OUTPUT_VIDEO = Path("output") / output_video_filename(prefix, date_id)
    TARGET_FPS = 24
    if args.shorts:
        TARGET_W, TARGET_H = 720, 1280  # 9:16 vertical for Shorts
    else:
        TARGET_W, TARGET_H = 1280, 720  # 16:9 landscape; match map intro

    # 1) Load target durations
    durations = json.loads(DURATIONS_JSON.read_text(encoding="utf-8"))
    dur_list = [entry["duration"] for entry in durations]

    # 2) Gather video files
    videos = sorted(VIDEO_DIR.glob("*.mp4"))

    # 2b) If we have one more video than durations and the first video is the intro, infer missing intro duration
    #     (can happen if map_intro wasn't run after narration-to-mp3 or durations.json was overwritten)
    if (
        len(videos) == len(dur_list) + 1
        and videos[0].name == "00_intro.mp4"
        and (not dur_list or durations[0].get("file") != "intro")
    ):
        intro_mp3 = Path(f"audio/{date_id}/intro_date.mp3")
        if intro_mp3.exists():
            info = ffmpeg.probe(str(intro_mp3))
            intro_dur = float(info["format"]["duration"])
        else:
            info = ffmpeg.probe(str(videos[0]))
            intro_dur = float(
                next(s for s in info["streams"] if s["codec_type"] == "video")["duration"]
            )
        intro_dur = round(intro_dur, 3)
        dur_list.insert(0, intro_dur)
        durations.insert(0, {"file": "intro", "duration": intro_dur})
        DURATIONS_JSON.write_text(json.dumps(durations, indent=2), encoding="utf-8")
        print(f"[OK] Prepended missing intro duration ({intro_dur}s) to {DURATIONS_JSON}")

    # 2b1) Optional: exclude specific narrative segments (1-based); filter videos and durations
    exclude_set = set()
    kept_indices = None  # when set, 0-based indices into original video list that were kept
    if args.exclude_segments:
        for part in args.exclude_segments.split(","):
            part = part.strip()
            if part:
                try:
                    exclude_set.add(int(part))
                except ValueError:
                    parser.error(f"--exclude-segments: invalid number {part!r}")
        if exclude_set:
            has_intro = videos and videos[0].name == "00_intro.mp4"

            def one_based_segment_at(i: int) -> int:
                if has_intro and i == 0:
                    return 0  # intro (always keep)
                return i + 1 if not has_intro else i

            kept_indices = [
                i
                for i in range(len(videos))
                if one_based_segment_at(i) == 0 or one_based_segment_at(i) not in exclude_set
            ]
            videos = [videos[i] for i in kept_indices]
            dur_list = [dur_list[i] for i in kept_indices]
            durations = [durations[i] for i in kept_indices]
            print(f"[OK] Excluded segment(s) {sorted(exclude_set)}; {len(videos)} clips remain.")

    _movie_root = Path(f"movie-images/{date_id}")
    cover_data = {} if _clips_sub else _load_fal_segment_cover_config(_movie_root)

    # 2b2) Mid-episode map (v2 narration): parchment_overlay only (map overlaid on segment; no standalone clip)
    narration_data = load_narration(date_id)
    map_insert = get_mid_episode_map_insertion(narration_data)
    map_overlay_path = None
    map_overlay_segment_index = None
    map_overlay_seconds = None
    if map_insert and not _clips_sub:
        try:
            from map_intro import generate_mid_episode_map
        except ImportError:
            generate_mid_episode_map = None
        if generate_mid_episode_map:
            try:
                seg_idx = map_insert["segment_index"]
                style = map_insert["visual_style"]
                dur_sec = map_insert["duration_seconds"]
                has_intro = bool(videos and videos[0].name == "00_intro.mp4")
                insert_at = seg_idx + (1 if has_intro else 0)  # 0-based index for overlay segment
                if kept_indices is not None:
                    # Overlay referred to original list; remap to kept index only if that segment was kept
                    orig_videos = sorted(_movie_root.glob("*.mp4"))
                    orig_has_intro = orig_videos and orig_videos[0].name == "00_intro.mp4"

                    def orig_segment_at(j: int) -> int:
                        if orig_has_intro and j == 0:
                            return 0
                        return j + 1 if not orig_has_intro else j

                    orig_kept = [
                        j
                        for j in range(len(orig_videos))
                        if orig_segment_at(j) == 0 or orig_segment_at(j) not in exclude_set
                    ]
                    insert_at_orig = seg_idx + (1 if orig_has_intro else 0)
                    if insert_at_orig in orig_kept:
                        map_overlay_segment_index = orig_kept.index(insert_at_orig)
                    else:
                        map_overlay_segment_index = None  # overlay segment was excluded
                else:
                    map_overlay_segment_index = insert_at
                if map_overlay_segment_index is not None and style == "parchment_overlay":
                    out_path = generate_mid_episode_map(
                        date_id, "parchment_overlay", dur_sec, VIDEO_DIR
                    )
                    if out_path:
                        map_overlay_path = out_path
                        map_overlay_seconds = dur_sec
                    else:
                        cached = VIDEO_DIR / "mid_map_overlay.png"
                        if cached.is_file():
                            map_overlay_path = cached
                            map_overlay_seconds = dur_sec
                            print(
                                f"[OK] Reusing {cached.name} for mid-episode map "
                                f"({dur_sec:.1f}s overlay on segment index {map_overlay_segment_index})"
                            )
            except Exception as e:
                print(f"[WARN] Mid-episode map generation failed: {e}", file=sys.stderr)
                if map_overlay_segment_index is not None and style == "parchment_overlay":
                    cached = VIDEO_DIR / "mid_map_overlay.png"
                    if cached.is_file():
                        map_overlay_path = cached
                        map_overlay_seconds = dur_sec
                        print(
                            f"[OK] Reusing {cached.name} for mid-episode map "
                            f"({dur_sec:.1f}s overlay on segment index {map_overlay_segment_index})",
                            file=sys.stderr,
                        )

    if len(videos) != len(dur_list):
        raise RuntimeError(f"Found {len(videos)} videos but {len(dur_list)} durations")

    # 2c) Audio source. When segments are excluded, concat intro (if any) + kept segment mp3s; otherwise intro + final or final only.
    intro_mp3_path = Path(f"audio/{date_id}/intro_date.mp3")
    if exclude_set:
        # Build audio from intro + kept segment files so excluded segments are omitted from audio too.
        audio_files = []
        for entry in durations:
            f = entry.get("file")
            if f == "intro":
                if intro_mp3_path.exists():
                    audio_files.append(intro_mp3_path)
            else:
                # e.g. "segments/06.mp3"
                p = Path(f"audio/{date_id}/{f}")
                if p.exists():
                    audio_files.append(p)
        if not audio_files:
            raise RuntimeError(
                "No audio files found for excluded-segments run (intro + segment mp3s)."
            )
        audio_in = ffmpeg.filter(
            [ffmpeg.input(str(p)).audio for p in audio_files],
            "concat",
            n=len(audio_files),
            v=0,
            a=1,
        )
    elif videos[0].name == "00_intro.mp4" and intro_mp3_path.exists():
        audio_in = ffmpeg.filter(
            [ffmpeg.input(str(intro_mp3_path)).audio, ffmpeg.input(str(FINAL_AUDIO)).audio],
            "concat",
            n=2,
            v=0,
            a=1,
        )
    else:
        audio_in = ffmpeg.input(str(FINAL_AUDIO)).audio

    # 2d) Optional ambient under full voice timeline (intro + segments per durations order)
    repo_root = Path(__file__).resolve().parent
    if exclude_set:
        probe_sr_path = audio_files[0]
    elif videos[0].name == "00_intro.mp4" and intro_mp3_path.exists():
        probe_sr_path = intro_mp3_path
    else:
        probe_sr_path = FINAL_AUDIO
    try:
        _pi = ffmpeg.probe(str(probe_sr_path))
        _ps = next(s for s in _pi["streams"] if s["codec_type"] == "audio")
        voice_sr = int(_ps["sample_rate"])
    except Exception:
        voice_sr = 24000
    mixed, amb_tmpdir = maybe_mix_ambient_under_voice(
        audio_in,
        narration_data=narration_data,
        durations=durations,
        use_ambient=args.ambient,
        voice_sample_rate=voice_sr,
        repo_root=repo_root,
    )
    if mixed is not None:
        audio_in = mixed

    tail_pad = float(args.audio_tail_pad_seconds or 0.0)
    if tail_pad > 0.01:
        try:
            _pi2 = ffmpeg.probe(str(probe_sr_path))
            _ps2 = next(s for s in _pi2["streams"] if s["codec_type"] == "audio")
            ch_tail = int(_ps2.get("channels") or 1)
        except Exception:
            ch_tail = 1
        layout = "mono" if ch_tail == 1 else "stereo"
        silence_tail = ffmpeg.input(
            f"anullsrc=sample_rate={voice_sr}:channel_layout={layout}",
            f="lavfi",
            t=tail_pad,
        ).audio
        audio_in = ffmpeg.filter([audio_in, silence_tail], "concat", n=2, v=0, a=1)

    # Gentle fade-out over the last of the spoken track so the closing line
    # settles into the tail-pad silence instead of stopping mid-air.
    _closing_fade_seconds = 0.6
    if exclude_set:
        _voice_total_dur = sum(float(d["duration"]) for d in durations)
    elif videos[0].name == "00_intro.mp4" and intro_mp3_path.exists():
        _voice_total_dur = float(ffmpeg.probe(str(intro_mp3_path))["format"]["duration"]) + float(
            ffmpeg.probe(str(FINAL_AUDIO))["format"]["duration"]
        )
    else:
        _voice_total_dur = float(ffmpeg.probe(str(FINAL_AUDIO))["format"]["duration"])
    _fade_start = _voice_total_dur - _closing_fade_seconds
    if _fade_start > 0.0:
        audio_in = ffmpeg.filter(
            audio_in, "afade", type="out", start_time=_fade_start, duration=_closing_fade_seconds
        )

    # 3) Build slowed video streams
    # At most one route-map graphic per final video (mid-episode parchment OR fal opening-cover map).
    map_graphic_used = False
    streams = []
    for idx, (vid_path, target_dur) in enumerate(zip(videos, dur_list, strict=True)):
        info = ffmpeg.probe(str(vid_path))
        orig_dur = float(next(s for s in info["streams"] if s["codec_type"] == "video")["duration"])
        speed = orig_dur / target_dur
        seg_key = _segment_key_from_video_filename(vid_path.name)
        eff_cover = _effective_opening_cover(seg_key, cover_data, args, target_dur)

        mid_map_here = map_overlay_path is not None and idx == map_overlay_segment_index

        # Same segment: narration mid-episode map beats opening-cover map (only one map on screen).
        if mid_map_here and eff_cover and eff_cover.get("cover_mode") == "map":
            eff_cover = {**eff_cover, "cover_mode": "black"}

        # Only one map graphic in the whole Short / video — later "map" opening covers become black.
        if eff_cover and eff_cover.get("cover_mode") == "map" and map_graphic_used:
            eff_cover = {**eff_cover, "cover_mode": "black"}

        if eff_cover and seg_key:
            print(
                f"[OK] Opening cover ({eff_cover['cover_mode']}, {eff_cover['cover_seconds']:.2f}s) "
                f"on segment {seg_key} ({vid_path.name})"
            )

        if mid_map_here and map_graphic_used:
            # Mid-episode map was already shown on an earlier segment — play this segment without parchment.
            stream = (
                ffmpeg.input(str(vid_path))
                .video.filter("scale", TARGET_W, TARGET_H, force_original_aspect_ratio="increase")
                .filter("crop", TARGET_W, TARGET_H)
                .filter("setpts", f"PTS/{speed}")
            )
            print(
                f"[OK] Mid-episode map skipped for {vid_path.name} — map already used once in this video.",
                file=sys.stderr,
            )
            if eff_cover:
                stream = _apply_opening_cover(
                    stream,
                    cfg=eff_cover,
                    date_id=date_id,
                    video_dir=VIDEO_DIR,
                    target_w=TARGET_W,
                    target_h=TARGET_H,
                    target_fps=TARGET_FPS,
                )
        elif mid_map_here:
            # Parchment overlay: first N seconds of this segment with map on top, then rest
            seg = ffmpeg.input(str(vid_path)).video
            map_in = ffmpeg.input(
                str(map_overlay_path), loop=1, t=map_overlay_seconds, framerate=TARGET_FPS
            ).video
            seg_pre = seg.trim(start=0, end=map_overlay_seconds).setpts("PTS-STARTPTS")
            seg_post = seg.trim(start=map_overlay_seconds).setpts("PTS-STARTPTS")
            seg_pre_scaled = seg_pre.filter(
                "scale", TARGET_W, TARGET_H, force_original_aspect_ratio="increase"
            ).filter("crop", TARGET_W, TARGET_H)
            seg_post_scaled = seg_post.filter(
                "scale", TARGET_W, TARGET_H, force_original_aspect_ratio="increase"
            ).filter("crop", TARGET_W, TARGET_H)
            # Letterbox the map inside the target frame.
            # Make it feel like a hard cut to a "map segment" (no fade-in),
            # then fade OUT near the end so it blends into the next visuals.
            fade_dur = min(map_overlay_seconds, 0.75)
            fade_out_start = max(0, map_overlay_seconds - fade_dur)
            map_scaled = (
                map_in.filter("scale", TARGET_W, TARGET_H, force_original_aspect_ratio="decrease")
                .filter("pad", TARGET_W, TARGET_H, "(ow-iw)/2", "(oh-ih)/2")
                .filter("format", pix_fmts="rgba")
                .filter("fade", type="out", start_time=fade_out_start, duration=fade_dur, alpha=1)
            )
            overlay_pre = ffmpeg.overlay(seg_pre_scaled, map_scaled)
            combined = ffmpeg.filter([overlay_pre, seg_post_scaled], "concat", v=1, a=0).node[0]
            stream = combined.filter("setpts", f"PTS/{speed}")
            map_graphic_used = True
            if eff_cover:
                stream = _apply_opening_cover(
                    stream,
                    cfg=eff_cover,
                    date_id=date_id,
                    video_dir=VIDEO_DIR,
                    target_w=TARGET_W,
                    target_h=TARGET_H,
                    target_fps=TARGET_FPS,
                )
        else:
            stream = (
                ffmpeg.input(str(vid_path))
                .video.filter("scale", TARGET_W, TARGET_H, force_original_aspect_ratio="increase")
                .filter("crop", TARGET_W, TARGET_H)
                .filter("setpts", f"PTS/{speed}")
            )
            if eff_cover:
                stream = _apply_opening_cover(
                    stream,
                    cfg=eff_cover,
                    date_id=date_id,
                    video_dir=VIDEO_DIR,
                    target_w=TARGET_W,
                    target_h=TARGET_H,
                    target_fps=TARGET_FPS,
                )
                if eff_cover.get("cover_mode") == "map":
                    map_graphic_used = True
        streams.append(stream)

    # 4) Concatenate into one silent video
    TMP_VIDEO.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_VIDEO.parent.mkdir(parents=True, exist_ok=True)
    video_concat = ffmpeg.concat(*streams, v=1, a=0).node[0]
    (
        ffmpeg.output(
            video_concat, str(TMP_VIDEO), vcodec="libx264", pix_fmt="yuv420p", r=TARGET_FPS
        )
        .overwrite_output()
        .run(quiet=False)
    )
    log_file_created(TMP_VIDEO, TMP_VIDEO.stat().st_size)

    # 5) Mux in the final narration audio (intro + final when intro clip present, else final only)
    # If video is shorter than audio (e.g. setpts/frame rounding), pad video so -shortest doesn't cut off audio
    video_info = ffmpeg.probe(str(TMP_VIDEO))
    video_dur = float(
        next(s["duration"] for s in video_info["streams"] if s["codec_type"] == "video")
    )
    tail_pad_mux = float(args.audio_tail_pad_seconds or 0.0)
    if exclude_set:
        audio_dur = sum(float(d["duration"]) for d in durations) + tail_pad_mux
    elif videos[0].name == "00_intro.mp4" and intro_mp3_path.exists():
        ai = ffmpeg.probe(str(intro_mp3_path))
        af = ffmpeg.probe(str(FINAL_AUDIO))
        audio_dur = float(ai["format"]["duration"]) + float(af["format"]["duration"]) + tail_pad_mux
    else:
        audio_dur = float(ffmpeg.probe(str(FINAL_AUDIO))["format"]["duration"]) + tail_pad_mux
    pad_sec = audio_dur - video_dur
    video_in = ffmpeg.input(str(TMP_VIDEO))
    # audio_in was set above: concat(intro_date, final) when intro present, else final only
    try:
        if pad_sec > 0.05:
            video_stream = video_in.video.filter("tpad", stop_mode="clone", stop_duration=pad_sec)
            (
                ffmpeg.output(
                    video_stream,
                    audio_in,
                    str(OUTPUT_VIDEO),
                    vcodec="libx264",
                    pix_fmt="yuv420p",
                    acodec="aac",
                    r=TARGET_FPS,
                )
                .global_args("-shortest")
                .overwrite_output()
                .run(quiet=False)
            )
        else:
            (
                ffmpeg.output(
                    video_in.video,
                    audio_in,
                    str(OUTPUT_VIDEO),
                    vcodec="copy",
                    acodec="aac",
                )
                .global_args("-shortest")
                .overwrite_output()
                .run(quiet=False)
            )
    finally:
        if amb_tmpdir:
            shutil.rmtree(amb_tmpdir, ignore_errors=True)
    log_file_created(OUTPUT_VIDEO, OUTPUT_VIDEO.stat().st_size)
    # Convenience: clickable shortcut to latest assembled video in repo root
    _write_latest_shortcut("latest_video.url", OUTPUT_VIDEO)

    print(f"[OK] Done! Final video at {OUTPUT_VIDEO}")
    # Optionally remove temp file
    try:
        TMP_VIDEO.unlink()
    except OSError:
        pass


if __name__ == "__main__":
    main()
