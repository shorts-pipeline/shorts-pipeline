"""fal.ai video vendor — Wan 2.5 or Kling v3 text/image-to-video APIs."""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pipeline.narration_common import load_narration_config
from pipeline_logging import log_api_call_with_bodies, log_file_created

from .fal_retry import fal_subscribe_with_retries, http_stream_to_file_with_retries
from .kling import (
    KLING_BROLL_ENGINE,
    KLING_I2V_MODEL_DEFAULT,
    KLING_T2V_MODEL_DEFAULT,
    kling_broll_duration_limits,
    kling_duration_seconds,
    kling_i2v_arguments,
    kling_t2v_arguments,
    resolve_broll_route_engines,
)


def _fal_wan_stat_segments(xs: list[int]) -> str:
    return ", ".join(str(x) for x in xs) if xs else "—"


def _record_fal_segment_cover(output_dir: Path, segment_index: int, reason: str) -> None:
    """
    When i2v falls back to portrait-only (scene anchor rejected), record a hint for
    videos-mp3-to-movie.py to mask the first second(s) with black or map (see fal_segment_covers.json).
    """
    path = output_dir / "fal_segment_covers.json"
    sec = float(os.environ.get("FAL_FALLBACK_COVER_SECONDS", "1.2"))
    mode = os.environ.get("FAL_FALLBACK_COVER_MODE", "map").strip().lower()
    if mode not in ("black", "map"):
        mode = "map"
    data: dict = {"version": 1, "segments": {}}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not isinstance(data.get("segments"), dict):
        data["segments"] = {}
    data["segments"][str(segment_index)] = {
        "reason": reason,
        "cover_seconds": sec,
        "cover_mode": mode,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"   [INFO] Opening-cover hint for segment {segment_index} -> {path.name}")


def _is_fal_content_policy_error(exc: BaseException) -> bool:
    """True when fal/Wan rejected the request for safety or content policy."""
    s = str(exc).lower()
    return (
        "content_policy" in s
        or "content checker" in s
        or "flagged by" in s
        or "content_policy_violation" in s
    )


def _sanitize_fal_prompt(text: str, *, aggressive: bool = False) -> str:
    """
    Soften wording that often trips remote safety filters on historical expedition
    prompts (hunting, field dressing, etc.) without changing the intended scene much.
    First pass is always applied; aggressive=True adds more swaps on retry after 422/policy errors.
    """
    if not text:
        return text
    out = text

    def _sub(pat: str, repl: str, flags: int = re.IGNORECASE) -> None:
        nonlocal out
        out = re.sub(pat, repl, out, flags=flags)

    # Strong swaps (journal vocabulary)
    _sub(r"\bcarcasses\b", "animal remains")
    _sub(r"\bcarcass\b", "animal remains")
    _sub(r"\bcorpses\b", "remains")
    _sub(r"\bcorpse\b", "remains")
    _sub(r"\bcadaver\b", "remains")
    _sub(r"\bskinned\b", "cleaned")
    _sub(r"\bskinning\b", "cleaning")
    _sub(r"\bgutted\b", "cleaned")
    _sub(r"\bslaughter\b", "harvest")
    _sub(r"\bslaughtered\b", "taken")
    _sub(r"\bgore\b", "mud")
    _sub(r"\brattlesnakes?\b", "small snakes")
    _sub(r"\bvenomous\b", "wild")
    _sub(r"\bden of snakes\b", "patch of trail with snakes")
    _sub(r"\bdispatch(?:ing|ed)?\s+the\s+threat\b", "keeping their distance", re.I)
    _sub(r"\bcoiling and striking\b", "resting on warm stones", re.I)
    _sub(r"\bstriking in defense\b", "on the ground", re.I)
    from .fal_prompt_policy import (
        should_apply_aggressive_weapon_softening,
        should_apply_weapon_to_longarms_rewrite,
    )

    if should_apply_weapon_to_longarms_rewrite(out):
        _sub(r"\bdraw their weapons\b", "ready their longarms", re.I)
        _sub(r"\bweapons\b", "longarms")
    # Ice / body-motion phrasing often flagged as harm (i2i + safety checker).
    _sub(r"\bbreak(?:s|ing)?\s+through\s+the\s+ice\b", "moving through thin ice", re.I)
    _sub(r"\bbreak(?:s|ing)?\s+through\s+ice\b", "moving through thin ice", re.I)
    _sub(r"\bsoaking\b", "dampening", re.I)

    if aggressive and should_apply_aggressive_weapon_softening(out):
        _sub(r"\bdraw their weapons\b", "raise walking sticks", re.I)
        _sub(r"\bweapons\b", "walking sticks")
        _sub(r"\blongarms?\b", "walking sticks")
        _sub(r"\bmuskets?\b", "walking sticks")
        _sub(r"\brifles?\b", "walking sticks")
    if aggressive:
        _sub(r"\bsnakes?\b", "small reptiles on the trail")
        _sub(r"\bbloody\b", "rust-stained")
        _sub(r"\bblood\b", "soil")
        _sub(r"\bkilled\b", "taken in the hunt")
        _sub(r"\bkilling\b", "hunting")
        _sub(r"\bkill\b", "catch")
        _sub(r"\bdead\b", "fallen")
        _sub(r"\bdeaths?\b", "loss")
        _sub(r"\bdied\b", "did not survive")
        _sub(r"\bmurder\b", "violence")
        _sub(r"\bhang(ed|ing)?\b", "secured")
        _sub(r"\bnaked\b", "unclothed")
        _sub(r"\bdecapitat\w*\b", "removed")

    return out


def _fal_wan_extra_args() -> dict:
    """Optional fal model flags; env overrides for debugging false-positive blocks."""
    extra: dict = {
        # Prompt expansion can amplify flagged words; default off for stability.
        "enable_prompt_expansion": os.environ.get("FAL_ENABLE_PROMPT_EXPANSION", "").lower()
        in (
            "1",
            "true",
            "yes",
        ),
        "enable_safety_checker": os.environ.get("FAL_DISABLE_SAFETY_CHECKER", "").lower()
        not in ("1", "true", "yes"),
    }
    return extra


class FalVendor:
    """Generates one clip per prompt via fal.ai Wan 2.5. Requires FAL_KEY."""

    name = "fal"

    _PROMPT_MARKER_CHAR_RE = re.compile(r"^\s*FAL_IMAGE_CHAR=([a-zA-Z0-9_-]+)\s*")
    _PROMPT_MARKER_SCENE_RE = re.compile(r"^\s*FAL_SCENE_ANCHOR=1\s*")

    def __init__(
        self,
        model: str = "fal-ai/wan-25-preview/text-to-video",
        resolution: str = "720p",
        duration: str = "5",
        **kwargs,
    ):
        self.model = model
        self.resolution = resolution
        self.duration = duration

    @staticmethod
    def _parse_markers(prompt: str) -> tuple[str, str | None, bool]:
        """Return (prompt_without_markers, character_id_or_None, scene_anchor_enabled)."""
        rest = (prompt or "").lstrip()
        cid: str | None = None
        scene_anchor = False
        while True:
            m_char = FalVendor._PROMPT_MARKER_CHAR_RE.match(rest)
            if m_char:
                cid = m_char.group(1)
                rest = rest[m_char.end() :].lstrip()
                continue
            m_scene = FalVendor._PROMPT_MARKER_SCENE_RE.match(rest)
            if m_scene:
                scene_anchor = True
                rest = rest[m_scene.end() :].lstrip()
                continue
            break
        return rest, cid, scene_anchor

    @staticmethod
    def _portrait_path_for_character(repo_root: Path, character_id: str) -> Path | None:
        base = repo_root / "character-portraits"
        for ext in (".png", ".jpg", ".jpeg"):
            p = base / f"{character_id}{ext}"
            if p.exists():
                return p
        return None

    @staticmethod
    def _data_uri_for_image(path: Path) -> str:
        ext = path.suffix.lower()
        if ext == ".png":
            mime = "image/png"
        elif ext in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif ext == ".webp":
            mime = "image/webp"
        else:
            raise ValueError(f"Unsupported image extension for data URI: {path.name}")
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"

    @staticmethod
    def _existing_segment_anchor_path(output_dir: Path, segment_index: int) -> Path | None:
        """On-disk opening still under ``output_dir/anchors/NN_*`` (any stem)."""
        anchors = output_dir / "anchors"
        if not anchors.is_dir() or segment_index < 1:
            return None
        exts = (".png", ".jpg", ".jpeg", ".webp")
        for ext in exts:
            for p in sorted(anchors.glob(f"{segment_index:02d}_*{ext}")):
                if p.is_file():
                    return p
        return None

    @staticmethod
    def _existing_scene_anchor_path(
        output_dir: Path, segment_index: int, character_id: str
    ) -> Path | None:
        """
        On-disk FAL scene-anchor still under ``output_dir/anchors/`` (same layout as the Pipeline UI).
        Prefer ``NN_<character_id>.<ext>``, else any ``NN_*`` image for that segment.
        """
        cid = (character_id or "").strip()
        if cid:
            anchors = output_dir / "anchors"
            if anchors.is_dir():
                for ext in (".png", ".jpg", ".jpeg", ".webp"):
                    p = anchors / f"{segment_index:02d}_{cid}{ext}"
                    if p.is_file():
                        return p
        return FalVendor._existing_segment_anchor_path(output_dir, segment_index)

    def generate(
        self,
        date_id: str,
        prompts: list[str],
        output_dir: Path,
        segment_indices: set[int] | None = None,
        **kwargs,
    ) -> list[Path]:
        """Generate one MP4 per prompt; saves to output_dir as 01.mp4, 02.mp4, ..."""
        try:
            import fal_client
        except ImportError as e:
            raise ImportError(
                "fal_client required for fal vendor. Install with: pip install fal-client"
            ) from e

        api_key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
        if not api_key:
            raise ValueError("Set FAL_KEY environment variable for fal vendor")

        # Default 720p: matches the 720x1280 Shorts delivery target, so ffmpeg no longer upscales
        # (and the 9:16 pad becomes a no-op). Set FAL_WAN_RESOLUTION=480p to cut cost per second.
        resolution = kwargs.get("resolution", self.resolution)
        env_res = (os.environ.get("FAL_WAN_RESOLUTION") or "").strip()
        if env_res:
            resolution = env_res
        default_duration = kwargs.get("duration", self.duration)
        aspect_ratio = kwargs.get("aspect_ratio", "9:16")
        concurrency = max(1, int(kwargs.get("concurrency", 1)))
        # Per-segment durations from audio (capped at 10s); Wan 2.5 only allows "5" or "10"
        target_durations: list[float] | None = kwargs.get("target_durations")
        opening_frames: list[str | None] | None = kwargs.get("opening_frames")
        if opening_frames is None:
            opening_frames = [None] * len(prompts)
        elif len(opening_frames) < len(prompts):
            opening_frames = list(opening_frames) + [None] * (len(prompts) - len(opening_frames))
        world_segments: list[str] | None = kwargs.get(
            "world_prefix_for_i2i_segments"
        )  # parallel to prompts
        core_location_overrides: list[str | None] | None = kwargs.get("core_location_overrides")
        negative_extras: list[str | None] | None = kwargs.get("negative_extras")
        world_prefix_for_i2i: str = (kwargs.get("world_prefix_for_i2i") or "").strip()
        cfg = load_narration_config()
        env_engine = (os.environ.get("FAL_BROLL_ENGINE") or "").strip()
        env_i2v = (os.environ.get("FAL_BROLL_I2V_ENGINE") or "").strip()
        env_t2v = (os.environ.get("FAL_BROLL_T2V_ENGINE") or "").strip()
        i2v_engine, t2v_engine = resolve_broll_route_engines(
            cfg,
            fal_broll_engine=kwargs.get("broll_engine") or env_engine or None,
            fal_broll_i2v_engine=kwargs.get("broll_i2v_engine") or env_i2v or None,
            fal_broll_t2v_engine=kwargs.get("broll_t2v_engine") or env_t2v or None,
        )
        use_kling_i2v = i2v_engine == KLING_BROLL_ENGINE
        use_kling_t2v = t2v_engine == KLING_BROLL_ENGINE
        kling_i2v_model = str(
            cfg.get("fal_kling_broll_i2v_model") or KLING_I2V_MODEL_DEFAULT
        ).strip()
        kling_t2v_model = str(
            cfg.get("fal_kling_broll_t2v_model") or KLING_T2V_MODEL_DEFAULT
        ).strip()
        kling_dur_min, kling_dur_max = kling_broll_duration_limits(cfg)
        broll_output_suffix = (kwargs.get("broll_output_suffix") or "").strip()
        if use_kling_i2v or use_kling_t2v:
            print(
                "[INFO] FAL B-roll routes: "
                f"i2v={'Kling' if use_kling_i2v else 'Wan'} "
                f"({'Kling ' + str(int(kling_dur_min)) + '–' + str(int(kling_dur_max)) + 's' if use_kling_i2v else 'Wan 5/10s'}), "
                f"t2v={'Kling' if use_kling_t2v else 'Wan'}",
                file=sys.stderr,
            )
        broll_summary = (
            f"B-roll (i2v={'Kling' if use_kling_i2v else 'Wan'}, "
            f"t2v={'Kling' if use_kling_t2v else 'Wan'})"
        )

        def _segment_out_path(seg_idx: int) -> Path:
            if broll_output_suffix:
                return output_dir / f"{seg_idx:02d}_{broll_output_suffix}.mp4"
            return output_dir / f"{seg_idx:02d}.mp4"

        def _world_for_segment(seg_idx: int) -> str:
            if isinstance(world_segments, list) and 0 < seg_idx <= len(world_segments):
                return (world_segments[seg_idx - 1] or "").strip()
            return world_prefix_for_i2i

        def _core_location_for_segment(seg_idx: int) -> str | None:
            if isinstance(core_location_overrides, list) and 0 < seg_idx <= len(
                core_location_overrides
            ):
                raw = core_location_overrides[seg_idx - 1]
                return raw.strip() if isinstance(raw, str) and raw.strip() else None
            return None

        def _fal_duration(sec: float) -> str:
            sec = min(10.0, max(0, sec))
            return "10" if sec > 7.5 else "5"

        output_dir.mkdir(parents=True, exist_ok=True)
        # Empty set means "no Wan segments" (e.g. --segments lists only talking_head rows);
        # do not treat that as falsy and regenerate every segment.
        if segment_indices is None:
            indices = set(range(1, len(prompts) + 1))
        else:
            indices = set(segment_indices)
        repo_root = Path(__file__).resolve().parent.parent
        paths_by_index: dict[int, Path] = {}
        run_stats: dict[str, list[int]] = {
            "wan_generated": [],
            "wan_skipped_existing": [],
            "wan_out_of_scope_reused": [],
        }

        for idx, _ in enumerate(prompts, start=1):
            if idx not in indices:
                existing = output_dir / f"{idx:02d}.mp4"
                if existing.exists():
                    paths_by_index[idx] = existing
                    run_stats["wan_out_of_scope_reused"].append(idx)

        work_items: list[tuple[int, str]] = []
        for idx, prompt in enumerate(prompts, start=1):
            if idx not in indices:
                continue
            out_path = _segment_out_path(idx)
            if out_path.exists():
                if broll_output_suffix:
                    try:
                        out_path.unlink()
                        print(
                            f"   B-roll segment {idx}: removed {out_path.name} for spike regen",
                            file=sys.stderr,
                        )
                    except OSError as e:
                        print(
                            f"   [WARN] Could not remove {out_path.name} for spike regen: {e}",
                            file=sys.stderr,
                        )
                else:
                    print(
                        f"   B-roll segment {idx}: reuse — {out_path.name} already exists "
                        f"(delete file to force regenerate)"
                    )
                    paths_by_index[idx] = out_path
                    run_stats["wan_skipped_existing"].append(idx)
                    continue
            work_items.append((idx, prompt))

        def _generate_one(idx: int, prompt_in: str) -> Path:
            out_path = _segment_out_path(idx)
            audio_sec = (
                target_durations[idx - 1]
                if target_durations and idx <= len(target_durations)
                else float(default_duration)
            )
            duration = (
                _fal_duration(audio_sec)
                if target_durations and idx <= len(target_durations)
                else default_duration
            )
            wan_extra = _fal_wan_extra_args()
            prompt_state = {"text": prompt_in}

            def _segment_once(aggressive: bool) -> Path:
                used_portrait_fallback = False
                prompt, character_id, scene_anchor = self._parse_markers(prompt_state["text"])
                prompt = _sanitize_fal_prompt(prompt, aggressive=aggressive)

                from .fal_scene_anchor_i2i import compose_fal_wan_negative_prompt

                negative = compose_fal_wan_negative_prompt(
                    prompt,
                    character_id=character_id,
                    extra_negative=(
                        negative_extras[idx - 1]
                        if negative_extras and idx - 1 < len(negative_extras)
                        else None
                    ),
                )
                portrait_data_uri: str | None = None
                use_image_to_video = False
                if character_id:
                    portrait_path = self._portrait_path_for_character(repo_root, character_id)
                    if portrait_path is not None:
                        portrait_data_uri = self._data_uri_for_image(portrait_path)
                        use_image_to_video = True

                visual_modes: list[str] | None = kwargs.get("visual_modes")
                scene_anchor_eligible = True
                if visual_modes and 0 < idx <= len(visual_modes):
                    from pipeline.broll_scene_anchor import broll_uses_scene_anchor

                    scene_anchor_eligible = broll_uses_scene_anchor(
                        visual_mode=visual_modes[idx - 1],
                        character_id=character_id or "",
                    )

                image_url_for_video: str | None = None
                anchor_url_for_i2v: str | None = None
                anchor_disk: Path | None = None
                if use_image_to_video and scene_anchor_eligible:
                    image_url_for_video = portrait_data_uri
                    if scene_anchor and character_id:
                        anchor_disk = self._existing_scene_anchor_path(
                            output_dir, idx, character_id
                        )
                        if anchor_disk is not None:
                            image_url_for_video = self._data_uri_for_image(anchor_disk)
                            anchor_url_for_i2v = image_url_for_video
                            print(
                                f"   [INFO] Segment {idx}: reusing on-disk scene anchor "
                                f"{anchor_disk.name} (skipping i2i; i2v only)"
                            )
                        else:
                            from .fal_scene_anchor_i2i import run_scene_anchor_i2i_to_disk

                            edited_url, _anchor_saved = run_scene_anchor_i2i_to_disk(
                                repo_root=repo_root,
                                segment_index=idx,
                                prompt_without_markers_sanitized=prompt,
                                character_id=character_id,
                                output_dir=output_dir,
                                opening_frames=opening_frames,
                                world_prefix_for_i2i=_world_for_segment(idx),
                                core_location_override=_core_location_for_segment(idx),
                                aspect_ratio=aspect_ratio,
                                aggressive=aggressive,
                            )
                            if edited_url:
                                image_url_for_video = edited_url
                                anchor_url_for_i2v = edited_url
                elif use_image_to_video and not scene_anchor_eligible and character_id:
                    anchor_disk = self._existing_scene_anchor_path(output_dir, idx, character_id)
                    if anchor_disk is not None:
                        print(
                            f"   [INFO] Segment {idx}: B-roll has no portrait-backed "
                            f"character (ignoring {anchor_disk.name}) -> text-to-video",
                            file=sys.stderr,
                        )

                use_i2v = bool(use_image_to_video and scene_anchor_eligible and image_url_for_video)
                use_kling = use_kling_i2v if use_i2v else use_kling_t2v
                route_label = (
                    ("Kling i2v" if use_kling_i2v else "Wan i2v")
                    if use_i2v
                    else ("Kling t2v" if use_kling_t2v else "Wan t2v")
                )
                clip_duration = (
                    kling_duration_seconds(
                        audio_sec,
                        min_seconds=kling_dur_min,
                        max_seconds=kling_dur_max,
                    )
                    if use_kling
                    else duration
                )
                if use_kling and target_durations and idx <= len(target_durations):
                    raw = float(target_durations[idx - 1])
                    if raw > kling_dur_max + 0.05:
                        print(
                            f"   [INFO] Segment {idx}: TTS {raw:.1f}s capped to Kling max "
                            f"{int(kling_dur_max)}s (assembly still matches full audio)",
                            file=sys.stderr,
                        )

                print(f"fal.ai generating segment {idx}/{len(prompts)} ({route_label})...")
                if use_i2v and image_url_for_video is not None:
                    if anchor_disk is None:
                        if anchor_url_for_i2v:
                            print(f"   [INFO] Segment {idx}: scene-anchor i2i -> image-to-video")
                        else:
                            print(f"   [INFO] Segment {idx}: portrait reference -> image-to-video")
                    if use_kling_i2v:
                        endpoint, i2v_args = kling_i2v_arguments(
                            prompt=prompt,
                            start_image_url=image_url_for_video,
                            duration_seconds=float(audio_sec),
                            aspect_ratio=aspect_ratio,
                            negative_prompt=negative,
                            i2v_model=kling_i2v_model,
                            min_seconds=kling_dur_min,
                            max_seconds=kling_dur_max,
                        )
                        try:
                            result = fal_subscribe_with_retries(
                                f"Kling i2v segment {idx}",
                                lambda: fal_client.subscribe(endpoint, arguments=i2v_args),
                            )
                        except Exception as e_i2v:
                            if (
                                anchor_url_for_i2v
                                and portrait_data_uri
                                and portrait_data_uri != image_url_for_video
                                and _is_fal_content_policy_error(e_i2v)
                            ):
                                print(
                                    f"   [WARN] Segment {idx}: i2v rejected scene anchor — "
                                    f"retrying with portrait only...",
                                    file=sys.stderr,
                                )
                                i2v_args["start_image_url"] = portrait_data_uri
                                result = fal_subscribe_with_retries(
                                    f"Kling i2v segment {idx} (portrait fallback)",
                                    lambda: fal_client.subscribe(endpoint, arguments=i2v_args),
                                )
                                used_portrait_fallback = True
                            else:
                                raise
                        log_api_call_with_bodies(
                            "fal",
                            "fal_client.subscribe",
                            request_body={"endpoint": endpoint, "arguments": i2v_args},
                            response_body=result,
                            model=endpoint,
                            extra={
                                "segment": idx,
                                "duration": clip_duration,
                                "broll_route": "i2v",
                                "broll_engine": i2v_engine,
                            },
                        )
                    else:
                        i2v_args = {
                            "prompt": prompt[:800],
                            "image_url": image_url_for_video,
                            "resolution": resolution,
                            "duration": clip_duration,
                            "negative_prompt": negative,
                        }
                        i2v_args.update(wan_extra)
                        try:
                            result = fal_subscribe_with_retries(
                                f"Wan i2v segment {idx}",
                                lambda: fal_client.subscribe(
                                    "fal-ai/wan-25-preview/image-to-video",
                                    arguments=i2v_args,
                                ),
                            )
                        except Exception as e_i2v:
                            if (
                                anchor_url_for_i2v
                                and portrait_data_uri
                                and portrait_data_uri != image_url_for_video
                                and _is_fal_content_policy_error(e_i2v)
                            ):
                                print(
                                    f"   [WARN] Segment {idx}: i2v rejected scene anchor — "
                                    f"retrying with portrait only...",
                                    file=sys.stderr,
                                )
                                i2v_args["image_url"] = portrait_data_uri
                                result = fal_subscribe_with_retries(
                                    f"Wan i2v segment {idx} (portrait fallback)",
                                    lambda: fal_client.subscribe(
                                        "fal-ai/wan-25-preview/image-to-video",
                                        arguments=i2v_args,
                                    ),
                                )
                                used_portrait_fallback = True
                            else:
                                raise
                        log_api_call_with_bodies(
                            "fal",
                            "fal_client.subscribe",
                            request_body={
                                "endpoint": "fal-ai/wan-25-preview/image-to-video",
                                "arguments": i2v_args,
                            },
                            response_body=result,
                            model="fal-ai/wan-25-preview/image-to-video",
                            extra={
                                "segment": idx,
                                "resolution": resolution,
                                "duration": clip_duration,
                            },
                        )
                else:
                    if use_image_to_video and not scene_anchor_eligible:
                        print(
                            f"   [INFO] Segment {idx}: portrait-backed B-roll not eligible "
                            f"for scene anchor -> text-to-video",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"   [INFO] Segment {idx}: no portrait-backed i2v path -> text-to-video",
                            file=sys.stderr,
                        )
                    if use_kling_t2v:
                        endpoint, t2v_args = kling_t2v_arguments(
                            prompt=prompt,
                            duration_seconds=float(audio_sec),
                            aspect_ratio=aspect_ratio,
                            negative_prompt=negative,
                            t2v_model=kling_t2v_model,
                            min_seconds=kling_dur_min,
                            max_seconds=kling_dur_max,
                        )
                        result = fal_subscribe_with_retries(
                            f"Kling t2v segment {idx}",
                            lambda: fal_client.subscribe(endpoint, arguments=t2v_args),
                        )
                        log_api_call_with_bodies(
                            "fal",
                            "fal_client.subscribe",
                            request_body={"endpoint": endpoint, "arguments": t2v_args},
                            response_body=result,
                            model=endpoint,
                            extra={
                                "segment": idx,
                                "duration": clip_duration,
                                "broll_route": "t2v",
                                "broll_engine": t2v_engine,
                            },
                        )
                    else:
                        t2v_args = {
                            "prompt": prompt[:800],
                            "resolution": resolution,
                            "duration": clip_duration,
                            "aspect_ratio": aspect_ratio,
                            "negative_prompt": negative,
                        }
                        t2v_args.update(wan_extra)
                        result = fal_subscribe_with_retries(
                            f"Wan t2v segment {idx}",
                            lambda: fal_client.subscribe(
                                self.model,
                                arguments=t2v_args,
                            ),
                        )
                        log_api_call_with_bodies(
                            "fal",
                            "fal_client.subscribe",
                            request_body={"endpoint": self.model, "arguments": t2v_args},
                            response_body=result,
                            model=self.model,
                            extra={
                                "segment": idx,
                                "resolution": resolution,
                                "duration": clip_duration,
                            },
                        )
                video_url = result.get("video", {}).get("url")
                if not video_url:
                    raise RuntimeError(f"fal.ai returned no video URL: {result}")

                http_stream_to_file_with_retries(
                    video_url,
                    out_path,
                    label=f"{route_label} segment {idx} mp4",
                )
                dl_bytes = out_path.stat().st_size if out_path.is_file() else 0
                log_api_call_with_bodies(
                    "fal",
                    "requests.get",
                    request_body={"url": video_url},
                    response_body=f"[mp4 stream saved bytes={dl_bytes} path={out_path.name}]",
                    model="download_mp4",
                    extra={
                        "segment": idx,
                        "url_host": video_url.split("/")[2] if "/" in video_url else "unknown",
                    },
                )
                try:
                    import ffmpeg

                    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4", prefix="fal_")
                    os.close(tmp_fd)
                    try:
                        inp = ffmpeg.input(str(out_path))
                        ffmpeg.output(inp.video, tmp_path, vcodec="copy").overwrite_output().run(
                            capture_stdout=True, capture_stderr=True
                        )
                        Path(tmp_path).replace(out_path)
                    finally:
                        if Path(tmp_path).exists():
                            Path(tmp_path).unlink(missing_ok=True)
                except Exception as e:
                    print(f"   [WARN] Could not strip audio from {out_path.name}: {e}")
                log_file_created(out_path, out_path.stat().st_size)
                print(f"   Saved {out_path.name}")
                if used_portrait_fallback:
                    _record_fal_segment_cover(output_dir, idx, "i2v_rejected_scene_anchor")
                return out_path

            last_policy_error: BaseException | None = None
            for aggressive in (False, True):
                try:
                    path_done = _segment_once(aggressive)
                    run_stats["wan_generated"].append(idx)
                    return path_done
                except Exception as e:
                    if not _is_fal_content_policy_error(e):
                        raise
                    last_policy_error = e
                    if not aggressive:
                        print(
                            f"   [WARN] Segment {idx}: fal content policy — retrying with softer wording...",
                            file=sys.stderr,
                        )
                        continue
                    from pipeline.fal_content_policy_rewrite import (
                        rewrite_and_persist_after_fal_policy,
                    )

                    print(
                        f"   [WARN] Segment {idx}: fal content policy after sanitizer — "
                        "asking OpenAI to rewrite opening_frame/video_prompt...",
                        file=sys.stderr,
                    )
                    of_now = opening_frames[idx - 1] if 0 <= idx - 1 < len(opening_frames) else None
                    rewritten = rewrite_and_persist_after_fal_policy(
                        date_id,
                        idx,
                        flagged_prompt=str(of_now or prompt_state["text"]),
                        error_text=str(e),
                        character_id=self._parse_markers(prompt_state["text"])[1] or "",
                    )
                    if not rewritten:
                        raise
                    opening_frames[idx - 1] = rewritten["opening_frame"]
                    try:
                        from video_vendors import build_prompts

                        rebuilt = build_prompts(date_id, vendor="fal")
                        if 0 <= idx - 1 < len(rebuilt):
                            prompt_state["text"] = rebuilt[idx - 1]
                    except Exception as rebuild_exc:
                        print(
                            f"   [WARN] Segment {idx}: could not rebuild vendor prompt "
                            f"after rewrite ({rebuild_exc}); retrying with updated opening_frame only",
                            file=sys.stderr,
                        )
                    print(
                        f"   [INFO] Segment {idx}: narration JSON updated; retrying FAL...",
                        file=sys.stderr,
                    )
                    path_done = _segment_once(True)
                    run_stats["wan_generated"].append(idx)
                    return path_done
            if last_policy_error is not None:
                raise last_policy_error
            raise RuntimeError(f"fal segment {idx} produced no clip")

        if not work_items:
            paths = [paths_by_index[i] for i in sorted(paths_by_index)]
            print(
                f"[INFO] FAL {broll_summary} summary: "
                f"generated=[{_fal_wan_stat_segments(run_stats['wan_generated'])}]; "
                f"skipped_existing=[{_fal_wan_stat_segments(run_stats['wan_skipped_existing'])}]; "
                f"out_of_scope_reused=[{_fal_wan_stat_segments(run_stats['wan_out_of_scope_reused'])}]"
            )
            if paths:
                print(
                    f"[OK] fal.ai: no new B-roll clips to render; "
                    f"{len(paths)} existing segment file(s) in {output_dir}"
                )
            else:
                print(f"[OK] fal.ai: no B-roll clips in scope for {output_dir}")
            return paths

        if concurrency == 1 or len(work_items) == 1:
            for idx, prompt in work_items:
                paths_by_index[idx] = _generate_one(idx, prompt)
        else:
            max_workers = min(concurrency, len(work_items))
            print(f"[INFO] fal concurrency: {max_workers}")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                fut_to_idx = {
                    executor.submit(_generate_one, idx, prompt): idx for idx, prompt in work_items
                }
                for fut in as_completed(fut_to_idx):
                    idx = fut_to_idx[fut]
                    paths_by_index[idx] = fut.result()

        paths = [paths_by_index[i] for i in sorted(paths_by_index)]

        print(
            f"[INFO] FAL {broll_summary} summary: "
            f"generated=[{_fal_wan_stat_segments(run_stats['wan_generated'])}]; "
            f"skipped_existing=[{_fal_wan_stat_segments(run_stats['wan_skipped_existing'])}]; "
            f"out_of_scope_reused=[{_fal_wan_stat_segments(run_stats['wan_out_of_scope_reused'])}]"
        )
        print(f"[OK] fal.ai generated {len(paths)} clips in {output_dir}")
        return paths
