#!/usr/bin/env python3
"""
Experiment: put a scene-anchor still behind an existing talking-head MP4.

Phase 1 (default): ffmpeg colorkey on flat void (white/black) + overlay on anchor PNG.
Phase 2 (--method ben): FAL Ben v2 video background removal (webm alpha) + overlay.

Run from repo root:
  python scripts/composite_talking_head_background.py 18040529 3
  python scripts/composite_talking_head_background.py 18040529 3 --method ben
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _probe_video(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,duration",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    v = (data.get("streams") or [{}])[0]
    dur = float(v.get("duration") or 0.0)
    if dur <= 0:
        dur = float((data.get("format") or {}).get("duration") or 0.0)
    fps_raw = v.get("r_frame_rate") or "30/1"
    if "/" in str(fps_raw):
        num, den = str(fps_raw).split("/", 1)
        fps = float(num) / float(den) if float(den) else 30.0
    else:
        fps = float(fps_raw)
    return {
        "width": int(v["width"]),
        "height": int(v["height"]),
        "duration": dur,
        "fps": fps,
    }


def _guess_void_color(frame_png: Path) -> str:
    """Sample border pixels on frame 0; return ffmpeg colorkey color name or hex."""
    from PIL import Image

    im = Image.open(frame_png).convert("RGB")
    w, h = im.size
    # Skip corners (often black letterbox); sample top/bottom/left/right mid-edges.
    border_pts: list[tuple[int, int, int]] = []
    x0, x1 = w // 8, (7 * w) // 8
    y0, y1 = h // 8, (7 * h) // 8
    for x in range(x0, x1, max(1, (x1 - x0) // 16)):
        border_pts.append(im.getpixel((x, 2)))
        border_pts.append(im.getpixel((x, h - 3)))
    for y in range(y0, y1, max(1, (y1 - y0) // 16)):
        border_pts.append(im.getpixel((2, y)))
        border_pts.append(im.getpixel((w - 3, y)))
    white_votes = sum(1 for p in border_pts if p[0] > 200 and p[1] > 200 and p[2] > 200)
    black_votes = sum(1 for p in border_pts if p[0] < 40 and p[1] < 40 and p[2] < 40)
    if white_votes > black_votes and white_votes >= len(border_pts) // 4:
        return "white"
    if black_votes > white_votes and black_votes >= len(border_pts) // 4:
        return "black"
    avg = tuple(sum(c[i] for c in border_pts) // len(border_pts) for i in range(3))
    if avg[0] > 200 and avg[1] > 200 and avg[2] > 200:
        return "white"
    if avg[0] < 40 and avg[1] < 40 and avg[2] < 40:
        return "black"
    return f"0x{avg[0]:02X}{avg[1]:02X}{avg[2]:02X}"


def _extract_frame(video: Path, dest_png: Path, t: float = 0.0) -> None:
    dest_png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(dest_png),
        ],
        check=True,
        capture_output=True,
    )


def _prepare_background_plate(
    background_image: Path,
    plate_mp4: Path,
    *,
    width: int,
    height: int,
    duration: float,
    fps: float,
) -> None:
    """Resize still once (PIL), encode a short H.264 plate — avoids huge -loop graphs."""
    from PIL import Image

    plate_mp4.parent.mkdir(parents=True, exist_ok=True)
    work = plate_mp4.parent
    still_jpg = work / f"{plate_mp4.stem}_bg.jpg"
    im = Image.open(background_image).convert("RGB")
    src_w, src_h = im.size
    scale = max(width / src_w, height / src_h)
    nw, nh = max(1, int(src_w * scale)), max(1, int(src_h * scale))
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    left = max(0, (nw - width) // 2)
    top = max(0, (nh - height) // 2)
    im = im.crop((left, top, left + width, top + height))
    im.save(still_jpg, format="JPEG", quality=92, optimize=True)

    t_out = max(0.1, float(duration))
    fps_use = max(1.0, min(float(fps or 25.0), 30.0))
    cmd = [
        "ffmpeg",
        "-y",
        "-threads",
        "2",
        "-loop",
        "1",
        "-framerate",
        f"{fps_use:.3f}",
        "-i",
        str(still_jpg),
        "-t",
        f"{t_out:.3f}",
        "-vf",
        f"scale={width}:{height},setsar=1",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        str(plate_mp4),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg background plate failed ({proc.returncode}):\n{proc.stderr[-3000:]}"
        )


def composite_colorkey(
    *,
    foreground_mp4: Path,
    background_image: Path,
    output_mp4: Path,
    width: int,
    height: int,
    duration: float,
    fps: float,
    key_color: str,
    similarity: float,
    blend: float,
) -> None:
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    if output_mp4.is_file() and output_mp4.stat().st_size == 0:
        output_mp4.unlink()

    work = output_mp4.parent
    plate_mp4 = work / f"{output_mp4.stem}_plate.mp4"
    _prepare_background_plate(
        background_image,
        plate_mp4,
        width=width,
        height=height,
        duration=duration,
        fps=fps,
    )

    # Pass 2: two video streams only (lower peak RAM than looped PNG + filter_complex).
    fc = (
        f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p,"
        f"colorkey={key_color}:similarity={similarity}:blend={blend}[fg];"
        f"[0:v][fg]overlay=0:0:shortest=1[out]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-threads",
        "2",
        "-filter_complex_threads",
        "1",
        "-i",
        str(plate_mp4),
        "-i",
        str(foreground_mp4),
        "-filter_complex",
        fc,
        "-map",
        "[out]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        str(output_mp4),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg colorkey composite failed ({proc.returncode}):\n{proc.stderr[-4000:]}"
        )
    sz = output_mp4.stat().st_size if output_mp4.is_file() else 0
    if sz > 50_000_000:
        raise RuntimeError(
            f"Composite output suspiciously large ({sz} bytes); "
            "try --method ben or lower --similarity."
        )


def _overlay_matte_on_plate(
    *,
    plate_mp4: Path,
    matte_video: Path,
    output_mp4: Path,
    width: int,
    height: int,
    key_color: str,
    similarity: float,
    blend: float,
) -> None:
    """Overlay a matte clip (Ben webm or keyed mp4) on a pre-encoded background plate."""
    fc = (
        f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p,"
        f"colorkey={key_color}:similarity={similarity}:blend={blend}[fg];"
        f"[0:v][fg]overlay=0:0:shortest=1[out]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-threads",
        "2",
        "-filter_complex_threads",
        "1",
        "-i",
        str(plate_mp4),
        "-i",
        str(matte_video),
        "-filter_complex",
        fc,
        "-map",
        "[out]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        str(output_mp4),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg matte overlay failed ({proc.returncode}):\n{proc.stderr[-4000:]}"
        )


def composite_ben_v2(
    *,
    foreground_mp4: Path,
    background_image: Path,
    output_mp4: Path,
    width: int,
    height: int,
    duration: float,
    fps: float,
    reuse_ben_fg: bool,
    key_color: str,
    similarity: float,
    blend: float,
) -> tuple[Path, str]:
    """Ben v2 matting + colorkey overlay (Ben webm is usually yuv420p without alpha)."""
    import os

    import fal_client

    from video_vendors.fal_retry import fal_subscribe_with_retries, http_stream_to_file_with_retries

    work = output_mp4.parent
    work.mkdir(parents=True, exist_ok=True)
    fg_webm = work / f"{output_mp4.stem}_ben_fg.webm"

    if not (reuse_ben_fg and fg_webm.is_file()):
        api_key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
        if not api_key:
            raise ValueError("Set FAL_KEY for --method ben")

        video_url = fal_client.upload_file(str(foreground_mp4))
        result = fal_subscribe_with_retries(
            "Ben v2 video background removal",
            lambda: fal_client.subscribe(
                "fal-ai/ben/v2/video",
                arguments={"video_url": video_url, "output_format": "webm"},
            ),
        )
        fg_url = (result.get("video") or {}).get("url")
        if not fg_url:
            raise RuntimeError(f"Ben v2 returned no video URL: {result!r}")
        http_stream_to_file_with_retries(fg_url, fg_webm, label="Ben v2 foreground webm")

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    plate_mp4 = work / f"{output_mp4.stem}_plate.mp4"
    _prepare_background_plate(
        background_image,
        plate_mp4,
        width=width,
        height=height,
        duration=duration,
        fps=fps,
    )

    matte_key = (key_color or "auto").strip()
    if matte_key.lower() == "auto":
        with tempfile.TemporaryDirectory() as tmp:
            frame0 = Path(tmp) / "ben_frame0.png"
            _extract_frame(fg_webm, frame0)
            matte_key = _guess_void_color(frame0)

    _overlay_matte_on_plate(
        plate_mp4=plate_mp4,
        matte_video=fg_webm,
        output_mp4=output_mp4,
        width=width,
        height=height,
        key_color=matte_key,
        similarity=similarity,
        blend=blend,
    )
    return fg_webm, matte_key


def _resolve_paths(date_id: str, segment: int, character: str | None) -> tuple[Path, Path, Path]:
    seg_dir = _REPO / "movie-images" / date_id
    fg = seg_dir / f"{segment:02d}.mp4"
    if not fg.is_file():
        raise FileNotFoundError(f"Missing foreground clip: {fg}")
    anchors = seg_dir / "anchors"
    if character:
        anchor = None
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            p = anchors / f"{segment:02d}_{character}{ext}"
            if p.is_file():
                anchor = p
                break
        if anchor is None:
            raise FileNotFoundError(f"No anchor {segment:02d}_{character}.* under {anchors}")
    else:
        cands = sorted(anchors.glob(f"{segment:02d}_*"))
        cands = [p for p in cands if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
        if not cands:
            raise FileNotFoundError(f"No anchor still for segment {segment} under {anchors}")
        anchor = cands[0]
    out_dir = seg_dir / "experiments"
    return fg, anchor, out_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Composite scene-anchor still behind existing talking-head MP4."
    )
    parser.add_argument("date_id", help="Eight-digit date id, e.g. 18040529")
    parser.add_argument("segment", type=int, help="1-based segment index, e.g. 3")
    parser.add_argument(
        "--character",
        default=None,
        help="Anchor filename character id (default: first anchors/NN_* match)",
    )
    parser.add_argument(
        "--method",
        choices=("colorkey", "ben"),
        default="colorkey",
        help="colorkey=local ffmpeg void key; ben=FAL Ben v2 matting + overlay",
    )
    parser.add_argument(
        "--key-color",
        default="auto",
        help="colorkey color: auto, white, black, or 0xRRGGBB (colorkey method only)",
    )
    parser.add_argument(
        "--similarity",
        type=float,
        default=0.14,
        help="colorkey similarity 0–1 (default 0.14)",
    )
    parser.add_argument(
        "--blend",
        type=float,
        default=0.06,
        help="colorkey blend 0–1 (default 0.06)",
    )
    parser.add_argument(
        "--reuse-ben-fg",
        action="store_true",
        help="Ben method only: skip FAL if experiments/*_ben_fg.webm already exists",
    )
    args = parser.parse_args()

    fg, anchor, out_dir = _resolve_paths(args.date_id, args.segment, args.character)
    meta = _probe_video(fg)
    w, h = meta["width"], meta["height"]

    method_tag = args.method
    char_stem = anchor.stem.split("_", 1)[-1] if "_" in anchor.stem else "char"
    out_mp4 = out_dir / f"{args.segment:02d}_{method_tag}_{char_stem}.mp4"
    preview_png = out_dir / f"{args.segment:02d}_{method_tag}_{char_stem}_preview.png"
    meta_path = out_dir / f"{args.segment:02d}_{method_tag}_{char_stem}_meta.json"

    def _log(msg: str) -> None:
        print(msg, flush=True)

    _log(f"Foreground: {fg}")
    _log(f"Background: {anchor}")
    _log(f"Size: {w}x{h}, duration {meta['duration']:.2f}s, method={args.method}")

    key_color = (args.key_color or "auto").strip()
    if args.method == "colorkey":
        if key_color.lower() == "auto":
            with tempfile.TemporaryDirectory() as tmp:
                frame0 = Path(tmp) / "frame0.png"
                _extract_frame(fg, frame0)
                key_color = _guess_void_color(frame0)
            _log(f"Auto key color: {key_color}")
        composite_colorkey(
            foreground_mp4=fg,
            background_image=anchor,
            output_mp4=out_mp4,
            width=w,
            height=h,
            duration=meta["duration"],
            fps=meta["fps"],
            key_color=key_color,
            similarity=args.similarity,
            blend=args.blend,
        )
    else:
        ben_webm, ben_matte_key = composite_ben_v2(
            foreground_mp4=fg,
            background_image=anchor,
            output_mp4=out_mp4,
            width=w,
            height=h,
            duration=meta["duration"],
            fps=meta["fps"],
            reuse_ben_fg=bool(args.reuse_ben_fg),
            key_color=key_color,
            similarity=args.similarity,
            blend=args.blend,
        )
        _log(f"Ben foreground webm: {ben_webm}")
        _log(f"Ben matte overlay key: {ben_matte_key}")
        key_color = ben_matte_key

    _extract_frame(out_mp4, preview_png, t=min(2.0, meta["duration"] * 0.25))
    meta_path.write_text(
        json.dumps(
            {
                "date_id": args.date_id,
                "segment": args.segment,
                "method": args.method,
                "foreground": str(fg.relative_to(_REPO)),
                "background": str(anchor.relative_to(_REPO)),
                "output": str(out_mp4.relative_to(_REPO)),
                "key_color": key_color if args.method == "colorkey" else None,
                "ben_matte_key": key_color if args.method == "ben" else None,
                "similarity": args.similarity,
                "blend": args.blend,
                "probe": meta,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _log(f"Wrote: {out_mp4}")
    _log(f"Preview: {preview_png}")
    _log(f"Meta: {meta_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
