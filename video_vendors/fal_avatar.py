"""FAL talking-head clips: portrait image + segment audio -> silent MP4 for assembly."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from pipeline_logging import log_api_call_with_bodies, log_file_created

from .fal_retry import (
    fal_subscribe_with_retries,
    fal_upload_with_retries,
    http_stream_to_file_with_retries,
)
from .kling import is_kling_avatar_model

# SadTalker ``preprocess`` (fal-ai/sadtalker): how the *model* finds the face inside the image you upload.
# It is not ffmpeg cropping before upload — we still send the same source URL/file (often a 9:16 scene anchor).
#   full   — prefer whole-frame / padded face box; keeps the most of a vertical plate (closest to “no tight face crop”).
#   resize — scale the frame for the detector; middle ground.
#   crop   — tight face crop inside SadTalker; last resort when the API cannot find a face with milder modes.
# Order: try the widest context first; on face-detection failures, fall back to tighter modes.
_SADTALKER_PREPROCESS_CHAIN: tuple[str, ...] = ("full", "resize", "crop")


# HeyGen Avatar 4 on fal (image + audio → talking avatar). Spike / production via fal_talking_head_model.
HEYGEN_AVATAR4_I2V_MODEL = "fal-ai/heygen/avatar4/image-to-video"


def _is_sadtalker_model(model_id: str) -> bool:
    return "sadtalker" in (model_id or "").lower()


def _is_heygen_avatar_model(model_id: str) -> bool:
    ml = (model_id or "").lower()
    return "heygen" in ml and "avatar" in ml


def _model_supports_mask_url(model_id: str) -> bool:
    """True for the only talking-head model whose args builder forwards ``mask_url``.

    A mask is how a shared two-shot bookend still tells the model which of the two
    faces is speaking (see ``conversation_bookend_anchor.py`` / ``segment_masks`` in
    the conversation manifest). HeyGen Avatar4 and SadTalker silently ignore it.
    """
    ml = (model_id or "").lower()
    return "omnihuman" in ml and "/v1.5" in ml


def _heygen_aspect_ratio(aspect_ratio: str | None) -> str:
    ar = (aspect_ratio or "").strip().lower().replace(" ", "")
    if ar in ("9:16", "9x16", "portrait", "vertical"):
        return "9:16"
    if ar in ("1:1", "1x1", "square"):
        return "1:1"
    return "16:9"


def _subscribe_args_for_talking_head(
    model_id: str,
    *,
    image_url: str,
    audio_url: str,
    prompt: str | None = None,
    mask_url: str | None = None,
    sadtalker_still_mode: bool = True,
    aspect_ratio: str | None = None,
) -> dict:
    """FAL model-specific argument shape (Kling Avatar, OmniHuman, HeyGen, SadTalker)."""
    from .kling import kling_avatar_arguments

    mid = (model_id or "").strip()
    ml = mid.lower()
    if "kling" in ml and "avatar" in ml:
        return kling_avatar_arguments(
            image_url=image_url,
            audio_url=audio_url,
            prompt=prompt,
        )
    if _is_heygen_avatar_model(mid):
        # audio_url overrides HeyGen TTS (prompt/voice); keep talking_style stable for documentary.
        args: dict[str, object] = {
            "image_url": image_url,
            "audio_url": audio_url,
            "talking_style": "stable",
            "aspect_ratio": _heygen_aspect_ratio(aspect_ratio),
            "resolution": "720p",
        }
        return args
    if "omnihuman" in ml:
        args = {"image_url": image_url, "audio_url": audio_url}
        mu = (mask_url or "").strip()
        if mu and "/v1.5" in ml:
            args["mask_url"] = mu
        if "/v1.5" in ml and (prompt or "").strip():
            args["prompt"] = str(prompt).strip()
        return args
    args = {"source_image_url": image_url, "driven_audio_url": audio_url}
    if _is_sadtalker_model(mid):
        args["still_mode"] = bool(sadtalker_still_mode)
    return args


def _sadtalker_preprocess_fallback_worthy(exc: BaseException) -> bool:
    """True when trying another preprocess mode might help (face not found / bad crop)."""
    m = str(exc).lower()
    if "content_policy" in m or "content checker" in m:
        return False
    return (
        "face_detection" in m
        or "face not" in m
        or "no face" in m
        or "unable to detect" in m
        or ("422" in m and "face" in m)
    )


def _sadtalker_try_preprocess_chain(
    segment_index: int,
    mid: str,
    image_url: str,
    audio_url: str,
    fal_client: object,
    *,
    sadtalker_still_mode: bool = True,
) -> tuple[dict | None, BaseException | None]:
    """
    SadTalker only: try preprocess modes full → resize → crop.
    Returns (result, None) on success, or (None, last_exc) if all worthy failures exhausted.
    Re-raises immediately on non-worthy errors (e.g. content policy).
    """
    args_base = _subscribe_args_for_talking_head(
        mid,
        image_url=image_url,
        audio_url=audio_url,
        sadtalker_still_mode=sadtalker_still_mode,
        aspect_ratio=None,
    )
    last_exc: BaseException | None = None
    for i, preprocess in enumerate(_SADTALKER_PREPROCESS_CHAIN):
        args = {**args_base, "preprocess": preprocess}
        try:
            result = fal_subscribe_with_retries(
                f"Talking-head seg {segment_index} ({mid}, preprocess={preprocess})",
                lambda a=args: fal_client.subscribe(mid, arguments=a),
            )
            if i > 0:
                print(
                    f"   [INFO] SadTalker succeeded with preprocess={preprocess!r} "
                    f"after earlier mode(s) failed face detection.",
                    file=sys.stderr,
                )
            if not isinstance(result, dict):
                raise RuntimeError(f"Unexpected FAL result type: {type(result)}")
            return result, None
        except BaseException as e:
            last_exc = e
            if not _sadtalker_preprocess_fallback_worthy(e):
                raise
            if i < len(_SADTALKER_PREPROCESS_CHAIN) - 1:
                print(
                    f"   [WARN] SadTalker preprocess={preprocess!r} failed ({e}); "
                    f"retrying with next preprocess mode...",
                    file=sys.stderr,
                )
            else:
                break
    return None, last_exc


def resolve_portrait_path(repo_root: Path, speaker_id: str) -> Path | None:
    sid = (speaker_id or "").strip().lower()
    if not sid:
        return None
    base = repo_root / "character-portraits"
    for ext in (".png", ".jpg", ".jpeg"):
        p = base / f"{sid}{ext}"
        if p.is_file():
            return p
    return None


def portrait_path_for_talking_head(
    repo_root: Path,
    talking_head_subject: str,
    reference_character_id: str | None = None,
) -> tuple[Path, str]:
    """
    Pick the image file for FAL talking-head: prefer ``reference_character_id`` when a
    matching file exists under character-portraits/ (e.g. composite ``lewis_seaman``),
    else ``talking_head_subject`` (e.g. ``lewis``).
    Returns (path, id_used).
    """
    subj = (talking_head_subject or "").strip().lower()
    ref = (reference_character_id or "").strip().lower()
    order: list[str] = []
    if ref:
        order.append(ref)
    if subj and subj not in order:
        order.append(subj)
    if not order:
        raise ValueError("talking_head_subject or reference_character_id required")
    for cid in order:
        p = resolve_portrait_path(repo_root, cid)
        if p is not None:
            return p, cid
    raise FileNotFoundError(
        f"No portrait under character-portraits/ for tried ids {order!r} "
        f"(composite or single-subject PNG/JPG)"
    )


def _video_url_from_fal_result(result: dict) -> str:
    vid = result.get("video")
    if isinstance(vid, dict) and vid.get("url"):
        return str(vid["url"]).strip()
    raise RuntimeError(f"FAL talking-head response missing video.url: {result!r}")


def _download_video(url: str, dest: Path, *, segment_index: int) -> None:
    http_stream_to_file_with_retries(
        url,
        dest,
        label=f"Talking-head seg {segment_index} raw mp4",
        chunk_size=65_536,
    )
    sz = dest.stat().st_size if dest.is_file() else 0
    log_api_call_with_bodies(
        "fal",
        "requests.get",
        request_body={"url": url},
        response_body=f"[mp4 stream saved bytes={sz} path={dest.name}]",
        model="talking_head_download",
        extra={
            "segment": segment_index,
            "url_host": url.split("/")[2] if "/" in url else "unknown",
        },
    )


def _canvas_size_for_aspect(aspect_ratio: str) -> tuple[int, int]:
    """Match videos-mp3-to-movie.py Shorts vs landscape targets."""
    ar = (aspect_ratio or "").strip().lower().replace(" ", "")
    if ar in ("9:16", "9x16", "portrait", "vertical"):
        return 720, 1280
    return 1280, 720


def _parse_cropdetect_spec(spec: str) -> tuple[int, int, int, int] | None:
    parts = (spec or "").strip().split(":")
    if len(parts) != 4:
        return None
    try:
        w, h, x, y = (int(p) for p in parts)
    except ValueError:
        return None
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        return None
    return w, h, x, y


def _probe_letterbox_crop(src: Path, *, frame_width: int, frame_height: int) -> str | None:
    """
    Detect baked-in black bars via ffmpeg cropdetect.
    Returns a ``w:h:x:y`` crop spec when bars trim ≥16px from height or width.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(src),
        "-vf",
        "cropdetect=24:16:0",
        "-frames:v",
        "30",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    crops: list[str] = []
    for line in (proc.stderr or "").splitlines():
        m = re.search(r"crop=(\d+:\d+:\d+:\d+)", line)
        if m:
            crops.append(m.group(1))
    if not crops:
        return None
    parsed = _parse_cropdetect_spec(crops[-1])
    if parsed is None:
        return None
    cw, ch, _cx, _cy = parsed
    if (frame_height - ch) < 16 and (frame_width - cw) < 16:
        return None
    return crops[-1]


def _encode_silent_canvas(
    src: Path,
    dest: Path,
    *,
    width: int,
    height: int,
    pad_lead_seconds: float = 0.0,
    auto_crop_letterbox: bool = False,
) -> None:
    """
    Strip audio; scale with letterboxing/pillarboxing to WxH; H.264 yuv420p.
    Optional tpad clones the first frame at the start (talking-head drive shorter than segment).

    When ``auto_crop_letterbox`` is True (Kling Avatar), cropdetect removes model baked-in
    bars, then scale-to-fill + center-crop restores the target canvas without black bands.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    pad = float(pad_lead_seconds or 0.0)
    scale_pad = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )
    core = scale_pad
    if auto_crop_letterbox:
        crop_spec = _probe_letterbox_crop(src, frame_width=width, frame_height=height)
        if crop_spec:
            scale_fill = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
            )
            core = f"crop={crop_spec},{scale_fill}"
            print(
                f"   [INFO] Talking-head letterbox crop {crop_spec} -> {width}x{height}",
                file=sys.stderr,
            )
    if pad > 0.02:
        vf = f"tpad=start_mode=clone:start_duration={pad:.3f},{core}"
    else:
        vf = core
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg canvas encode failed ({proc.returncode}): {proc.stderr[-2000:]}"
        )


def generate_talking_head_clip(
    *,
    repo_root: Path,
    segment_index: int,
    audio_mp3: Path,
    talking_head_subject: str,
    output_mp4: Path,
    model_id: str,
    reference_character_id: str | None = None,
    source_image_url: str | None = None,
    source_image_path: Path | None = None,
    source_mask_path: Path | None = None,
    source_mask_url: str | None = None,
    talking_head_prompt: str | None = None,
    pad_lead_seconds: float = 0.0,
    aspect_ratio: str = "16:9",
    fallback_model_id: str | None = None,
    sadtalker_still_mode: bool = True,
    require_scene_anchor: bool = True,
) -> Path:
    """
    Call FAL image+audio talking-head model; write **silent** H.264 MP4 to output_mp4.
    Requires FAL_KEY and fal-client.

    Source image resolution: ``source_image_url`` (e.g. scene-anchor i2i CDN URL),
    else ``source_image_path`` (on-disk anchor or still). When ``require_scene_anchor``
    is True (default), raw ``character-portraits/`` cutouts are not used as a fallback.

    When ``pad_lead_seconds`` > 0, prepends that many seconds by cloning the first video
    frame (``tpad``), for legacy drive files shorter than the full segment MP3 (encoding drift).
    Normal talking-head drive audio is gap+dialogue (same as segment MP3) from narration-to-mp3.

    ``aspect_ratio`` (e.g. ``9:16`` for Shorts) sets the output frame size after ffmpeg
    scale+pad, matching ``videos-mp3-to-movie.py`` (720x1280 or 1280x720).

    ``sadtalker_still_mode``: passed to FAL SadTalker as ``still_mode`` (default ``True``).
    Prefer leaving it ``True``: ``False`` can increase whole-head motion that reads as detached from the torso.
    """
    try:
        import fal_client
    except ImportError as e:
        raise ImportError("pip install fal-client") from e

    api_key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
    if not api_key:
        raise ValueError("Set FAL_KEY for FAL talking-head generation")

    if not audio_mp3.is_file():
        raise FileNotFoundError(f"Missing segment audio: {audio_mp3}")

    su = (source_image_url or "").strip()
    if su:
        image_url = su
        portrait_id_used = "scene_anchor_url"
        src_label = "FAL scene-anchor / i2i URL"
    elif source_image_path is not None and source_image_path.is_file():
        image_url = fal_upload_with_retries(
            f"Talking-head seg {segment_index} image file",
            lambda: fal_client.upload_file(str(source_image_path)),
        )
        portrait_id_used = source_image_path.stem
        src_label = str(source_image_path)
    elif require_scene_anchor:
        raise RuntimeError(
            f"Segment {segment_index}: talking-head requires a scene-anchor still "
            f"(source_image_url or source_image_path); raw portrait fallback is disabled."
        )
    else:
        portrait, portrait_id_used = portrait_path_for_talking_head(
            repo_root,
            talking_head_subject,
            reference_character_id=reference_character_id,
        )
        image_url = fal_upload_with_retries(
            f"Talking-head seg {segment_index} portrait",
            lambda: fal_client.upload_file(str(portrait)),
        )
        src_label = str(portrait)

    audio_url = fal_upload_with_retries(
        f"Talking-head seg {segment_index} audio",
        lambda: fal_client.upload_file(str(audio_mp3)),
    )

    mask_url = (source_mask_url or "").strip()
    if not mask_url and source_mask_path is not None and source_mask_path.is_file():
        mask_url = fal_upload_with_retries(
            f"Talking-head seg {segment_index} mask",
            lambda: fal_client.upload_file(str(source_mask_path)),
        )

    mid = (model_id or "fal-ai/sadtalker").strip()
    if mask_url and not _model_supports_mask_url(mid):
        fb = (fallback_model_id or "").strip()
        if fb and _model_supports_mask_url(fb):
            print(
                f"   [INFO] Segment {segment_index}: two-shot mask available but "
                f"primary model {mid!r} doesn't accept mask_url; using mask-aware "
                f"model {fb!r} instead so the correct speaker's face is driven.",
                file=sys.stderr,
            )
            mid = fb
        else:
            print(
                f"   [WARN] Segment {segment_index}: two-shot mask available but "
                f"neither primary {mid!r} nor fallback {fb!r} accept mask_url; "
                f"speaker face is unresolved for this shot.",
                file=sys.stderr,
            )

    arguments = _subscribe_args_for_talking_head(
        mid,
        image_url=image_url,
        audio_url=audio_url,
        prompt=talking_head_prompt,
        mask_url=mask_url or None,
        sadtalker_still_mode=sadtalker_still_mode,
        aspect_ratio=aspect_ratio,
    )
    is_sadtalker = _is_sadtalker_model(mid)

    print(f"   [INFO] Talking-head source image: {portrait_id_used} ({src_label})")
    print(f"   [INFO] Talking-head model: {mid}", file=sys.stderr)
    if is_sadtalker:
        print(
            f"   [INFO] SadTalker still_mode={sadtalker_still_mode!r}",
            file=sys.stderr,
        )

    result: dict | None = None
    if is_sadtalker:
        result, last_exc = _sadtalker_try_preprocess_chain(
            segment_index,
            mid,
            image_url,
            audio_url,
            fal_client,
            sadtalker_still_mode=sadtalker_still_mode,
        )
        if result is None:
            fb = (fallback_model_id or "").strip()
            if fb and fb != mid:
                print(
                    f"   [WARN] SadTalker exhausted preprocess chain ({last_exc}); "
                    f"trying fallback model {fb!r} (vendor policy).",
                    file=sys.stderr,
                )
                mid_fb = fb
                args_fb = _subscribe_args_for_talking_head(
                    mid_fb,
                    image_url=image_url,
                    audio_url=audio_url,
                    prompt=talking_head_prompt,
                    sadtalker_still_mode=sadtalker_still_mode,
                    aspect_ratio=aspect_ratio,
                )
                if _is_sadtalker_model(mid_fb):
                    result, last_exc_fb = _sadtalker_try_preprocess_chain(
                        segment_index,
                        mid_fb,
                        image_url,
                        audio_url,
                        fal_client,
                        sadtalker_still_mode=sadtalker_still_mode,
                    )
                    if result is None:
                        assert last_exc_fb is not None
                        raise last_exc_fb
                else:
                    result = fal_subscribe_with_retries(
                        f"Talking-head seg {segment_index} ({mid_fb})",
                        lambda: fal_client.subscribe(mid_fb, arguments=args_fb),
                    )
            else:
                assert last_exc is not None
                raise last_exc
    else:
        try:
            result = fal_subscribe_with_retries(
                f"Talking-head seg {segment_index} ({mid})",
                lambda: fal_client.subscribe(mid, arguments=arguments),
            )
        except BaseException as primary_exc:
            fb = (fallback_model_id or "").strip()
            if not fb or fb == mid:
                raise
            print(
                f"   [WARN] Primary talking-head model {mid!r} failed ({primary_exc}); "
                f"trying fallback {fb!r}.",
                file=sys.stderr,
            )
            mid_fb = fb
            args_fb = _subscribe_args_for_talking_head(
                mid_fb,
                image_url=image_url,
                audio_url=audio_url,
                prompt=talking_head_prompt,
                sadtalker_still_mode=sadtalker_still_mode,
                aspect_ratio=aspect_ratio,
            )
            if _is_sadtalker_model(mid_fb):
                result, last_fb = _sadtalker_try_preprocess_chain(
                    segment_index,
                    mid_fb,
                    image_url,
                    audio_url,
                    fal_client,
                    sadtalker_still_mode=sadtalker_still_mode,
                )
                if result is None:
                    assert last_fb is not None
                    raise last_fb from None
            else:
                result = fal_subscribe_with_retries(
                    f"Talking-head seg {segment_index} ({mid_fb})",
                    lambda: fal_client.subscribe(mid_fb, arguments=args_fb),
                )
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected FAL result type: {type(result)}")
    log_api_call_with_bodies(
        "fal",
        "fal_client.subscribe",
        request_body={
            "segment": segment_index,
            "model_id_arg": model_id,
            "primary_model": mid,
            "portrait_id": portrait_id_used,
            "talking_head_subject": (talking_head_subject or "").strip().lower(),
            "image_url": image_url,
            "audio_url": audio_url,
            "arguments_last_primary": arguments,
            "talking_head_prompt": (talking_head_prompt or "")[:4000],
        },
        response_body=result,
        model=mid,
        extra={"segment": segment_index, "portrait_id": portrait_id_used},
    )
    raw_url = _video_url_from_fal_result(result)

    tw, th = _canvas_size_for_aspect(aspect_ratio)
    with tempfile.TemporaryDirectory() as tmp:
        raw_path = Path(tmp) / "raw_avatar.mp4"
        _download_video(raw_url, raw_path, segment_index=segment_index)
        _encode_silent_canvas(
            raw_path,
            output_mp4,
            width=tw,
            height=th,
            pad_lead_seconds=float(pad_lead_seconds or 0.0),
            auto_crop_letterbox=is_kling_avatar_model(mid),
        )

    log_file_created(output_mp4, output_mp4.stat().st_size)
    return output_mp4
