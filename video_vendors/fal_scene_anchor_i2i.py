"""
FAL Wan scene-anchor image-to-image (portrait → opening still) for one segment.

Used by ``video_vendors/fal.py`` full clip generation and by the Pipeline UI / CLI
for regenerating anchor frames without running image-to-video.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pipeline.prompt_pack_text import load_shared_prompt_text
from pipeline_logging import log_api_call_with_bodies, log_file_created

from .fal_retry import fal_subscribe_with_retries, http_get_bytes_with_retries
from .prompt_parts import NO_ON_SCREEN_TEXT_PREFIX

ImageSizeArg = str | dict[str, int]

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Strip bookends copied from build_prompts before i2i reuse; keep in sync with fal.py / __init__.py.
_FAL_PROMPT_TAIL_RE = re.compile(
    r"\s*Family-friendly(?:, no nudity)?(?:, YouTube-appropriate)?\.\s*$",
    re.IGNORECASE,
)

# Shared with fal.generate negative block (mild PG + anti-text + physics).
# Keep this short and free of explicit anatomy terms — FAL's checker scans
# negative_prompt too and has rejected Wan requests for listing body-part bans.
FAL_I2I_NEGATIVE_BASE = (
    "nsfw, adult content, on-screen text, watermarks, logos, readable words, "
    "object interpenetration, impossible physics, clipping through rigid surfaces, "
    "excessive camera shake, chaotic motion, revolver, cap-and-ball cylinder pistol, "
    "modern firearm"
)

# fal-ai/wan-25-preview image-to-image / image-to-video: negative_prompt max 500 characters (API schema).
_FAL_I2I_NEGATIVE_MAX_LEN = 500
_FAL_WAN_NEGATIVE_MAX_LEN = 500

# Expedition wildlife and stock — used to append animal anatomy negatives for Wan i2v/t2v only.
_VIDEO_PROMPT_ANIMAL_RE = re.compile(
    r"(?i)\b(?:"
    r"deer|elk|antelope|moose|caribou|"
    r"snake|serpent|rattlesnake|reptile|"
    r"fish|salmon|trout|catfish|sturgeon|"
    r"dog|newfoundland|horse|mule|ox|oxen|cattle|livestock|"
    r"beaver|buffalo|bison|bear|wolf|"
    r"eagle|hawk|osprey|bird|birds|geese|goose|duck|ducks|"
    r"game\s+animal|wildlife|herd|antler"
    r")\b"
)


def fal_prompt_core_for_i2i(prompt: str) -> str:
    """Remove injected legibility fragment and trailing safety suffix."""
    out = (prompt or "").strip()
    out = _FAL_PROMPT_TAIL_RE.sub("", out).strip()
    if NO_ON_SCREEN_TEXT_PREFIX in out:
        out = out.replace(NO_ON_SCREEN_TEXT_PREFIX, "", 1).strip()
    return out


def _i2i_human_anatomy_suffix() -> str:
    body = load_shared_prompt_text(_REPO_ROOT, "fal_i2i_human_anatomy_suffix.txt").strip()
    return "\n\n" + body if body else ""


def _i2i_composite_human_anatomy_suffix() -> str:
    body = load_shared_prompt_text(_REPO_ROOT, "fal_i2i_composite_human_anatomy_suffix.txt").strip()
    return "\n\n" + body if body else ""


def _i2i_animal_anatomy_suffix() -> str:
    body = load_shared_prompt_text(_REPO_ROOT, "fal_i2i_animal_anatomy_suffix.txt").strip()
    return "\n\n" + body if body else ""


def _fal_i2i_policy_suffix(
    prompt_without_markers_sanitized: str,
    character_id: str,
) -> str:
    """Optional weapon/animal composition suffixes — only when the prompt explicitly requests them."""
    from .fal_prompt_policy import (
        prompt_has_empty_hands_policy,
        prompt_requests_animal_focal_composition,
    )

    text = prompt_without_markers_sanitized or ""
    parts: list[str] = []
    if prompt_has_empty_hands_policy(text):
        body = load_shared_prompt_text(_REPO_ROOT, "fal_i2i_empty_hands_suffix.txt").strip()
        if body:
            parts.append(body)
    if prompt_requests_animal_focal_composition(text):
        body = load_shared_prompt_text(_REPO_ROOT, "fal_i2i_animal_focal_suffix.txt").strip()
        if body:
            parts.append(body)
    if not parts:
        return ""
    return "\n\n" + " ".join(parts)


def clip_fal_i2i_negative(text: str, max_len: int = _FAL_I2I_NEGATIVE_MAX_LEN) -> str:
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    return s[:max_len].rstrip(" ,")


def video_prompt_mentions_animal(prompt: str) -> bool:
    """True when the Wan prompt text references expedition animals or game."""
    return bool(_VIDEO_PROMPT_ANIMAL_RE.search(prompt or ""))


def _fal_wan_negative_suffix(name: str) -> str:
    return load_shared_prompt_text(_REPO_ROOT, name).strip()


def _fit_fal_wan_negative(base: str, suffixes: list[str], max_len: int) -> str:
    """Keep specialized suffixes intact; trim only the generic PG/physics ``base`` tail."""
    parts = [s for s in suffixes if s]
    extra = ", ".join(parts)
    base = (base or "").strip()
    if not extra:
        return clip_fal_i2i_negative(base, max_len)
    sep = ", "
    room = max_len - len(sep) - len(extra)
    if room < 60:
        return clip_fal_i2i_negative(extra, max_len)
    prefix = base[:room].rstrip(" ,")
    out = prefix + sep + extra
    if len(out) <= max_len:
        return out
    return clip_fal_i2i_negative(out, max_len)


def _wan_needs_animal_negatives(
    prompt: str,
    *,
    character_id: str | None = None,
    environment_still: bool = False,
) -> bool:
    """Animal fusion/handling bans only when wildlife is in-frame or likely to be hallucinated."""
    from . import _is_animal_character

    cid = (character_id or "").strip()
    return bool(
        environment_still
        or video_prompt_mentions_animal(prompt)
        or (cid and _is_animal_character(cid))
    )


def _wan_needs_weapon_negatives(prompt: str) -> bool:
    from .fal_prompt_policy import prompt_mentions_weapon_prop

    return prompt_mentions_weapon_prop(prompt)


def compose_fal_wan_negative_prompt(
    prompt: str,
    *,
    character_id: str | None = None,
    max_len: int = _FAL_WAN_NEGATIVE_MAX_LEN,
    environment_still: bool = False,
    extra_negative: str | None = None,
) -> str:
    """
    Wan text-to-video / image-to-video negative_prompt.

    Always includes a mild PG/physics base, object-permanence, and human limb-safety.
    Weapon prop permanence and animal fusion/handling terms are appended only when the
    prompt (or animal character / environment still) makes them relevant — keeps
    negatives short so FAL's content checker is less likely to reject the request.
    """
    suffixes: list[str] = []
    extra = (extra_negative or "").strip()
    if extra:
        suffixes.append(extra)
    object_perm = _fal_wan_negative_suffix("fal_i2v_negative_suffix_object_permanence.txt")
    if object_perm:
        suffixes.append(object_perm)
    human = _fal_wan_negative_suffix("fal_i2i_negative_suffix_human.txt")
    if human:
        suffixes.append(human)
    if _wan_needs_weapon_negatives(prompt):
        weapons = _fal_wan_negative_suffix("fal_i2v_negative_suffix_weapons.txt")
        if weapons:
            suffixes.append(weapons)
    if _wan_needs_animal_negatives(
        prompt, character_id=character_id, environment_still=environment_still
    ):
        animal = _fal_wan_negative_suffix("fal_i2v_negative_suffix_animal.txt")
        if animal:
            suffixes.append(animal)
    return _fit_fal_wan_negative(FAL_I2I_NEGATIVE_BASE, suffixes, max_len)


def compose_fal_i2i_negative_for_human(
    max_len: int = _FAL_I2I_NEGATIVE_MAX_LEN,
    *,
    prompt: str | None = None,
) -> str:
    """
    Build a negative_prompt under ``max_len`` that **always** includes the human limb-safety suffix.

    Weapon and animal terms are context-gated from ``prompt`` (same rules as Wan t2v/i2v).
    Specialized suffixes are kept intact; only the mild PG/physics base is trimmed.
    """
    text = prompt or ""
    suffixes: list[str] = []
    human = load_shared_prompt_text(_REPO_ROOT, "fal_i2i_negative_suffix_human.txt").strip()
    if human:
        suffixes.append(human)
    object_perm = load_shared_prompt_text(
        _REPO_ROOT, "fal_i2v_negative_suffix_object_permanence.txt"
    ).strip()
    if object_perm:
        suffixes.append(object_perm)
    if _wan_needs_weapon_negatives(text):
        weapons = load_shared_prompt_text(_REPO_ROOT, "fal_i2v_negative_suffix_weapons.txt").strip()
        if weapons:
            suffixes.append(weapons)
    if video_prompt_mentions_animal(text):
        animal = load_shared_prompt_text(_REPO_ROOT, "fal_i2v_negative_suffix_animal.txt").strip()
        if animal:
            suffixes.append(animal)
    if not suffixes:
        return clip_fal_i2i_negative(FAL_I2I_NEGATIVE_BASE, max_len)
    return _fit_fal_wan_negative(FAL_I2I_NEGATIVE_BASE, suffixes, max_len)


def optional_fal_scene_anchor_i2i_seed() -> int | None:
    raw = (os.environ.get("FAL_SCENE_ANCHOR_I2I_SEED") or "").strip()
    if not raw:
        return None
    try:
        return int(raw, 10)
    except ValueError:
        return None


def _fal_wan_extra_args() -> dict:
    return {
        "enable_prompt_expansion": os.environ.get("FAL_ENABLE_PROMPT_EXPANSION", "").lower()
        in (
            "1",
            "true",
            "yes",
        ),
        "enable_safety_checker": os.environ.get("FAL_DISABLE_SAFETY_CHECKER", "").lower()
        not in ("1", "true", "yes"),
    }


def build_scene_anchor_i2i_prompt(
    *,
    prompt_without_markers_sanitized: str,
    character_id: str,
    segment_index: int,
    opening_frames: list[str | None] | None,
    world_prefix_for_i2i: str,
    aggressive: bool,
    sanitize: Callable[..., str],
    dual_reference_speakers: tuple[str, str] | None = None,
) -> str:
    """
    Build the Wan image-to-image prompt string for a scene anchor.

    ``sanitize`` must be ``video_vendors.fal._sanitize_fal_prompt`` (passed in to avoid import cycles).
    """
    from . import _is_animal_character, _is_composite_character

    is_animal_anchor = bool(character_id) and _is_animal_character(character_id)
    is_human_anchor = bool(character_id) and not is_animal_anchor

    i2i_intro = load_shared_prompt_text(_REPO_ROOT, "fal_i2i_create_still_intro.txt")
    i2i_animal_intro = load_shared_prompt_text(_REPO_ROOT, "fal_i2i_animal_create_still_intro.txt")
    i2i_exact = load_shared_prompt_text(_REPO_ROOT, "fal_i2i_opening_frame_exact_line.txt")
    i2i_fallback_intro = load_shared_prompt_text(
        _REPO_ROOT, "fal_i2i_fallback_no_opening_frame_intro.txt"
    )

    prompt_wo_safety = fal_prompt_core_for_i2i(prompt_without_markers_sanitized)
    opening_text: str | None = None
    if opening_frames and (segment_index - 1) < len(opening_frames):
        o = opening_frames[segment_index - 1]
        if isinstance(o, str) and o.strip():
            opening_text = o.strip()

    if is_human_anchor and _is_composite_character(character_id) and dual_reference_speakers:
        left_id, right_id = dual_reference_speakers
        left_id = (left_id or "").strip().lower()
        right_id = (right_id or "").strip().lower()
        scene = opening_text or prompt_wo_safety or ""
        from pipeline.dual_reference_i2i_prompt import build_dual_reference_i2i_prompt

        core = build_dual_reference_i2i_prompt(
            left_speaker_id=left_id,
            right_speaker_id=right_id,
            scene_text=scene,
            world_prefix=world_prefix_for_i2i or "",
            shot_framing="Wide medium two-shot.",
        )
        policy = _fal_i2i_policy_suffix(prompt_without_markers_sanitized, character_id)
        return sanitize(core + NO_ON_SCREEN_TEXT_PREFIX + policy, aggressive=aggressive)

    if opening_text:
        # Humans: lead with pose/limb constraints and the opening beat (many diffusion models weight
        # early tokens more than a long location prefix). Setting and no-text rules follow.
        wp = (world_prefix_for_i2i or "").strip()
        if is_animal_anchor:
            # Animal anchors must NOT reuse the human intro (it invites "clothing" and
            # "held objects"), and must keep the breed/species line — otherwise i2i can
            # blend the dog portrait with the period-dressed men into a hybrid figure.
            intro = i2i_animal_intro.strip() or i2i_intro.strip()
            core = intro + "\n\n" if intro else ""
            breed = prompt_wo_safety.strip()
            if breed:
                core += breed + "\n\n"
            if wp:
                core += wp + "\n\n"
            core += NO_ON_SCREEN_TEXT_PREFIX
            core += i2i_exact.strip() + "\n" if i2i_exact.strip() else ""
            core += opening_text.strip()
            core += _i2i_animal_anatomy_suffix()
            policy = _fal_i2i_policy_suffix(prompt_without_markers_sanitized, character_id)
            return sanitize(core + policy, aggressive=aggressive)
        if is_human_anchor:
            if _is_composite_character(character_id):
                core = (
                    "PRIMARY — two distinct people: keep both portrait subjects visible and separate, "
                    "side by side with stable left/right placement. Preserve each reference portrait's "
                    "face, general likeness, and garment identity (coat color, facings, insignia, hat) "
                    "only; do not copy standing pose, gaze/eyeline, head angle, or held props from the "
                    "reference. Pose, body position, gaze direction, and held objects follow the opening "
                    "frame below; the opening frame may add wear condition (mud, wet, worn) to that same "
                    "locked garment, or override the lock entirely when it explicitly states a coat or "
                    "hat is removed, set aside, or absent (e.g. swimming, bathing). Natural arm boundaries "
                    "for each person; no fused body, merged face, extra limbs, or overlap hiding one "
                    "subject. "
                )
            else:
                core = (
                    "PRIMARY — single figure: preserve the reference portrait's face, general likeness, "
                    "and garment identity (coat color, facings, insignia, hat) only; do not copy standing "
                    "pose, gaze/eyeline, head angle, or held props from the reference. Pose, body position, "
                    "gaze direction, and held objects follow the opening frame below; the opening frame "
                    "may add wear condition (mud, wet, worn) to that same locked garment, or override the "
                    "lock entirely when it explicitly states a coat or hat is removed, set aside, or "
                    "absent (e.g. swimming, bathing). Exactly two arms and two hands; no extra limbs, "
                    "merged hands, or duplicate arms. "
                )
            core += i2i_exact.strip() + "\n" if i2i_exact.strip() else ""
            core += opening_text.strip() + "\n\n"
            core += i2i_intro.strip() + "\n\n" if i2i_intro.strip() else ""
            if wp:
                core += wp + "\n\n"
            core += NO_ON_SCREEN_TEXT_PREFIX
            if _is_composite_character(character_id):
                core += _i2i_composite_human_anatomy_suffix()
            else:
                core += _i2i_human_anatomy_suffix()
        else:
            core = i2i_intro.strip() + "\n\n" if i2i_intro.strip() else ""
            if wp:
                core += wp + "\n\n"
            core += NO_ON_SCREEN_TEXT_PREFIX
            core += i2i_exact + opening_text
        policy = _fal_i2i_policy_suffix(prompt_without_markers_sanitized, character_id)
        return sanitize(core + policy, aggressive=aggressive)

    image_to_image_prompt = i2i_fallback_intro + f"{prompt_wo_safety}"
    if is_animal_anchor:
        policy = _fal_i2i_policy_suffix(prompt_without_markers_sanitized, character_id)
        return sanitize(
            image_to_image_prompt + _i2i_animal_anatomy_suffix() + policy,
            aggressive=aggressive,
        )
    if is_human_anchor:
        suffix = (
            _i2i_composite_human_anatomy_suffix()
            if _is_composite_character(character_id)
            else _i2i_human_anatomy_suffix()
        )
        policy = _fal_i2i_policy_suffix(prompt_without_markers_sanitized, character_id)
        return sanitize(image_to_image_prompt + suffix + policy, aggressive=aggressive)
    policy = _fal_i2i_policy_suffix(prompt_without_markers_sanitized, character_id)
    return image_to_image_prompt + policy


def build_scene_anchor_t2i_prompt(
    *,
    prompt_without_markers_sanitized: str,
    segment_index: int,
    opening_frames: list[str | None] | None,
    world_prefix_for_i2i: str,
    aggressive: bool,
    sanitize: Callable[..., str],
    character_id: str = "",
) -> str:
    """Wan text-to-image prompt for b-roll segments without a portrait reference."""
    from . import _is_animal_character

    cid = (character_id or "").strip()
    is_animal_anchor = bool(cid) and _is_animal_character(cid)

    i2i_intro = load_shared_prompt_text(_REPO_ROOT, "fal_i2i_create_still_intro.txt")
    if is_animal_anchor:
        i2i_intro = (
            load_shared_prompt_text(_REPO_ROOT, "fal_i2i_animal_create_still_intro.txt")
            or i2i_intro
        )
    i2i_exact = load_shared_prompt_text(_REPO_ROOT, "fal_i2i_opening_frame_exact_line.txt")
    animal_suffix = _i2i_animal_anatomy_suffix() if is_animal_anchor else ""

    prompt_wo_safety = fal_prompt_core_for_i2i(prompt_without_markers_sanitized)
    opening_text: str | None = None
    if opening_frames and (segment_index - 1) < len(opening_frames):
        o = opening_frames[segment_index - 1]
        if isinstance(o, str) and o.strip():
            opening_text = o.strip()

    wp = (world_prefix_for_i2i or "").strip()
    policy = _fal_i2i_policy_suffix(prompt_without_markers_sanitized, cid)
    from .fal_prompt_policy import prompt_is_animal_focal

    env_guard = ""
    if not cid and not prompt_is_animal_focal(prompt_wo_safety, character_id=cid or None):
        env_guard = (
            "Open landscape and river only; no wildlife, birds, or deer in frame "
            "unless explicitly described below. "
        )

    if opening_text:
        core = i2i_intro.strip() + "\n\n" if i2i_intro.strip() else ""
        if wp:
            core += wp + "\n\n"
        core += NO_ON_SCREEN_TEXT_PREFIX
        if env_guard:
            core += env_guard
        core += i2i_exact.strip() + "\n" if i2i_exact.strip() else ""
        core += opening_text.strip()
        core += animal_suffix
        return sanitize(core + policy, aggressive=aggressive)

    # No opening_frame: Wan t2i has no reference portrait — use the segment video prompt as-is.
    parts: list[str] = []
    if wp:
        parts.append(wp)
    body = env_guard + prompt_wo_safety if env_guard else prompt_wo_safety
    parts.append(NO_ON_SCREEN_TEXT_PREFIX + body)
    return sanitize("\n\n".join(parts) + animal_suffix + policy, aggressive=aggressive)


def _anchor_stem_for_segment(character_id: str | None) -> str:
    stem = _safe_anchor_character_file_stem(character_id or "")
    return stem if stem and stem != "character" else "scene"


def _save_anchor_image_bytes(
    *,
    segment_index: int,
    character_id: str | None,
    body: bytes,
    edited_url: str,
    anchors_dir: Path,
    anchor_save_basename: str | None = None,
) -> Path | None:
    char_stem = _anchor_stem_for_segment(character_id)
    ext = ".png"
    if "." in edited_url.rsplit("/", 1)[-1]:
        ext = "." + edited_url.rsplit("/", 1)[-1].split("?")[0]
        if len(ext) > 5:
            ext = ".png"
    if anchor_save_basename:
        safe_base = re.sub(r"[^a-z0-9_-]+", "_", anchor_save_basename.strip().lower())
        anchor_path = anchors_dir / f"{safe_base or char_stem}{ext}"
    else:
        anchor_path = anchors_dir / f"{segment_index:02d}_{char_stem}{ext}"
    try:
        if anchor_path.is_file():
            bak_dir = anchors_dir / "_overwrite_backup"
            bak_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            bak_path = bak_dir / f"{anchor_path.stem}_{stamp}{anchor_path.suffix}"
            shutil.copy2(anchor_path, bak_path)
            print(f"   [OK] Previous anchor preserved: {bak_path.name}")
        anchor_path.write_bytes(body)
        log_file_created(anchor_path, anchor_path.stat().st_size)
        print(f"   Saved anchor image: {anchor_path.name}")
    except Exception as e:
        print(f"   [WARN] Could not save anchor image for segment {segment_index}: {e}")
        return None
    return anchor_path


def _invoke_wan_scene_anchor_t2i(
    *,
    segment_index: int,
    text_to_image_prompt: str,
    negative_t2i: str,
    image_size: ImageSizeArg,
) -> str | None:
    import fal_client

    t2i_args: dict = {
        "prompt": text_to_image_prompt[:2000],
        "negative_prompt": clip_fal_i2i_negative(negative_t2i),
        "num_images": 1,
        "image_size": image_size,
    }
    seed_i2i = optional_fal_scene_anchor_i2i_seed()
    if seed_i2i is not None:
        t2i_args["seed"] = seed_i2i
    t2i_args.update(_fal_wan_extra_args())

    ti_result = fal_subscribe_with_retries(
        f"Wan scene-anchor t2i segment {segment_index}",
        lambda: fal_client.subscribe(
            "fal-ai/wan-25-preview/text-to-image",
            arguments=t2i_args,
        ),
    )
    log_api_call_with_bodies(
        "fal",
        "fal_client.subscribe",
        request_body={
            "endpoint": "fal-ai/wan-25-preview/text-to-image",
            "arguments": t2i_args,
        },
        response_body=ti_result,
        model="fal-ai/wan-25-preview/text-to-image",
        extra={"segment": segment_index, "scene_anchor_only": True},
    )
    try:
        images = ti_result.get("images") or []
        if images and isinstance(images, list):
            url = images[0].get("url")
            return url if isinstance(url, str) and url.strip() else None
    except Exception:
        pass
    return None


def run_scene_anchor_t2i_to_disk(
    *,
    repo_root: Path,
    segment_index: int,
    prompt_without_markers_sanitized: str,
    output_dir: Path,
    opening_frames: list[str | None] | None,
    world_prefix_for_i2i: str,
    core_location_override: str | None = None,
    aspect_ratio: str = "9:16",
    aggressive: bool = False,
    character_id: str | None = None,
    anchor_save_basename: str | None = None,
    image_size: ImageSizeArg | None = None,
) -> tuple[str | None, Path | None]:
    """Text-to-image opening still for segments without a portrait (b-roll environment shots)."""
    from .fal import _sanitize_fal_prompt

    output_dir = _normalize_scene_anchor_parent_dir(output_dir)
    api_key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
    if not api_key:
        raise ValueError("Set FAL_KEY environment variable for fal vendor")

    negative_t2i = compose_fal_wan_negative_prompt(
        prompt_without_markers_sanitized,
        character_id=character_id,
        environment_still=True,
    )
    image_size_resolved: ImageSizeArg = (
        image_size
        if image_size is not None
        else ("portrait_16_9" if str(aspect_ratio).strip() == "9:16" else "landscape_16_9")
    )
    retry_world = _merge_world_prefix_for_i2i(world_prefix_for_i2i, core_location_override)
    attempts: list[tuple[bool, str]] = [(aggressive, world_prefix_for_i2i), (True, retry_world)]

    edited_url: str | None = None
    body: bytes | None = None
    for attempt_aggressive, world_wp in attempts:
        image_prompt = build_scene_anchor_t2i_prompt(
            prompt_without_markers_sanitized=prompt_without_markers_sanitized,
            segment_index=segment_index,
            opening_frames=opening_frames,
            world_prefix_for_i2i=world_wp,
            aggressive=attempt_aggressive,
            sanitize=_sanitize_fal_prompt,
            character_id=character_id or "",
        )
        edited_url = _invoke_wan_scene_anchor_t2i(
            segment_index=segment_index,
            text_to_image_prompt=image_prompt,
            negative_t2i=negative_t2i,
            image_size=image_size_resolved,
        )
        if not edited_url:
            return None, None
        body = http_get_bytes_with_retries(
            edited_url,
            label=f"Scene-anchor t2i image seg {segment_index}",
        )
        if body:
            break

    if not edited_url or body is None:
        return None, None

    anchors_dir = output_dir / "anchors"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    saved = _save_anchor_image_bytes(
        segment_index=segment_index,
        character_id=character_id,
        body=body,
        edited_url=edited_url,
        anchors_dir=anchors_dir,
        anchor_save_basename=anchor_save_basename,
    )
    return edited_url, saved


def run_segment_anchor_to_disk(
    *,
    repo_root: Path,
    segment_index: int,
    prompt_without_markers_sanitized: str,
    output_dir: Path,
    opening_frames: list[str | None] | None,
    world_prefix_for_i2i: str,
    core_location_override: str | None = None,
    aspect_ratio: str = "9:16",
    aggressive: bool = False,
    character_id: str | None = None,
    anchor_save_basename: str | None = None,
    image_size: ImageSizeArg | None = None,
    portrait_data_uris: list[str] | None = None,
    dual_reference_speakers: tuple[str, str] | None = None,
    image_to_image_prompt_override: str | None = None,
) -> tuple[str | None, Path | None]:
    """
    Build one segment opening still: portrait i2i when a portrait exists, else Wan text-to-image.
    """
    from .fal import FalVendor

    cid = (character_id or "").strip()
    if cid and not portrait_data_uris:
        try:
            from pipeline.dynamic_pair_scene_anchor import broll_dual_portrait_kwargs

            pair_kwargs = broll_dual_portrait_kwargs(repo_root, cid)
            if pair_kwargs:
                portrait_data_uris = pair_kwargs.get("portrait_data_uris")
                if dual_reference_speakers is None:
                    dual_reference_speakers = pair_kwargs.get("dual_reference_speakers")
        except Exception:
            pass

    portrait_ready = bool(
        cid
        and (
            portrait_data_uris or FalVendor._portrait_path_for_character(repo_root, cid) is not None
        )
    )
    if not portrait_ready and cid:
        try:
            from pipeline.dynamic_pair_scene_anchor import pair_reference_ready

            portrait_ready = pair_reference_ready(cid)
        except Exception:
            portrait_ready = False

    if cid and portrait_ready:
        return run_scene_anchor_i2i_to_disk(
            repo_root=repo_root,
            segment_index=segment_index,
            prompt_without_markers_sanitized=prompt_without_markers_sanitized,
            character_id=cid,
            output_dir=output_dir,
            opening_frames=opening_frames,
            world_prefix_for_i2i=world_prefix_for_i2i,
            core_location_override=core_location_override,
            aspect_ratio=aspect_ratio,
            aggressive=aggressive,
            anchor_save_basename=anchor_save_basename,
            image_size=image_size,
            portrait_data_uris=portrait_data_uris,
            dual_reference_speakers=dual_reference_speakers,
            image_to_image_prompt_override=image_to_image_prompt_override,
        )
    return run_scene_anchor_t2i_to_disk(
        repo_root=repo_root,
        segment_index=segment_index,
        prompt_without_markers_sanitized=prompt_without_markers_sanitized,
        output_dir=output_dir,
        opening_frames=opening_frames,
        world_prefix_for_i2i=world_prefix_for_i2i,
        core_location_override=core_location_override,
        aspect_ratio=aspect_ratio,
        aggressive=aggressive,
        character_id=cid or None,
        anchor_save_basename=anchor_save_basename,
        image_size=image_size,
    )


def _normalize_scene_anchor_parent_dir(output_dir: Path) -> Path:
    """
    Stills are saved under ``<parent>/anchors``. If ``output_dir`` already ends with a
    component named ``anchors`` (misconfigured caller), use the parent directory so we
    never create ``.../anchors/anchors``.
    """
    p = output_dir
    try:
        p = p.resolve()
    except OSError:
        pass
    if p.name.lower() == "anchors":
        return p.parent
    return p


def _safe_anchor_character_file_stem(character_id: str) -> str:
    """Filename-safe fragment for ``NN_<id>.png`` (no path separators)."""
    s = (character_id or "").strip().lower()
    s = re.sub(r"[^a-z0-9_-]+", "_", s)
    return s or "character"


def _merge_world_prefix_for_i2i(base: str, core_location_override: str | None) -> str:
    """Append narration ``core_location_override`` to the i2i world/setting prefix (retry path)."""
    wp = (base or "").strip()
    loc = (core_location_override or "").strip()
    if not loc:
        return wp
    if loc.lower() in wp.lower():
        return wp
    return f"{wp}\n\n{loc}".strip() if wp else loc


def _invoke_wan_scene_anchor_i2i(
    *,
    segment_index: int,
    portrait_data_uris: list[str],
    image_to_image_prompt: str,
    negative_i2i: str,
    image_size: ImageSizeArg,
) -> str | None:
    import fal_client

    uris = [u for u in portrait_data_uris if u][:2]
    if not uris:
        return None

    ti_args: dict = {
        "prompt": image_to_image_prompt[:2000],
        "image_urls": uris,
        "negative_prompt": clip_fal_i2i_negative(negative_i2i),
        "image_size": image_size,
    }
    seed_i2i = optional_fal_scene_anchor_i2i_seed()
    if seed_i2i is not None:
        ti_args["seed"] = seed_i2i
    ti_args.update(_fal_wan_extra_args())

    ti_result = fal_subscribe_with_retries(
        f"Wan scene-anchor i2i segment {segment_index}",
        lambda: fal_client.subscribe(
            "fal-ai/wan-25-preview/image-to-image",
            arguments=ti_args,
        ),
    )
    log_api_call_with_bodies(
        "fal",
        "fal_client.subscribe",
        request_body={
            "endpoint": "fal-ai/wan-25-preview/image-to-image",
            "arguments": ti_args,
        },
        response_body=ti_result,
        model="fal-ai/wan-25-preview/image-to-image",
        extra={"segment": segment_index, "scene_anchor_only": True},
    )
    try:
        images = ti_result.get("images") or []
        if images and isinstance(images, list):
            url = images[0].get("url")
            return url if isinstance(url, str) and url.strip() else None
    except Exception:
        pass
    return None


def run_scene_anchor_i2i_to_disk(
    *,
    repo_root: Path,
    segment_index: int,
    prompt_without_markers_sanitized: str,
    character_id: str,
    output_dir: Path,
    opening_frames: list[str | None] | None,
    world_prefix_for_i2i: str,
    core_location_override: str | None = None,
    aspect_ratio: str = "9:16",
    aggressive: bool = False,
    anchor_save_basename: str | None = None,
    image_size: ImageSizeArg | None = None,
    portrait_data_uris: list[str] | None = None,
    dual_reference_speakers: tuple[str, str] | None = None,
    image_to_image_prompt_override: str | None = None,
) -> tuple[str | None, Path | None]:
    """
    Run FAL Wan image-to-image for one scene-anchored segment; save under ``output_dir/anchors/``.

    ``prompt_without_markers_sanitized`` is the vendor string **after** stripping ``FAL_*`` markers
    and applying ``_sanitize_fal_prompt``.

    After download, runs a composite-seam quality check. On failure, retries once with
    ``aggressive`` sanitization and ``core_location_override`` appended to the world prefix.
    If the retry still fails, raises ``SceneAnchorQualityError`` (segment should abort).

    Returns ``(fal_image_url_or_none, saved_file_or_none)``. On API failure, ``(None, None)``.
    """
    from pipeline.scene_anchor_quality import (
        SceneAnchorQualityError,
        detect_composite_seam_anchor,
        scene_anchor_quality_check_enabled,
    )

    output_dir = _normalize_scene_anchor_parent_dir(output_dir)
    try:
        import fal_client  # noqa: F401
    except ImportError as e:
        raise ImportError("fal_client required. Install with: pip install fal-client") from e

    api_key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
    if not api_key:
        raise ValueError("Set FAL_KEY environment variable for fal vendor")

    from .fal import FalVendor, _sanitize_fal_prompt

    if portrait_data_uris:
        uris = [u for u in portrait_data_uris if u][:2]
        if len(uris) < 1:
            raise ValueError("portrait_data_uris must include at least one data URI")
    else:
        portrait_path = FalVendor._portrait_path_for_character(repo_root, character_id)
        if portrait_path is None:
            raise FileNotFoundError(
                f"No portrait for character_id={character_id!r} under character-portraits/"
            )
        uris = [FalVendor._data_uri_for_image(portrait_path)]

    from . import _is_animal_character

    is_human_anchor = bool(character_id) and not _is_animal_character(character_id)
    neg_probe_parts = [prompt_without_markers_sanitized or ""]
    if opening_frames and (segment_index - 1) < len(opening_frames):
        of_raw = opening_frames[segment_index - 1]
        if of_raw and str(of_raw).strip():
            neg_probe_parts.append(str(of_raw).strip())
    negative_i2i = (
        compose_fal_i2i_negative_for_human(prompt=" ".join(neg_probe_parts))
        if is_human_anchor
        else clip_fal_i2i_negative(FAL_I2I_NEGATIVE_BASE)
    )

    image_size_resolved: ImageSizeArg = (
        image_size
        if image_size is not None
        else ("portrait_16_9" if str(aspect_ratio).strip() == "9:16" else "landscape_16_9")
    )
    quality_on = scene_anchor_quality_check_enabled()
    retry_world = _merge_world_prefix_for_i2i(world_prefix_for_i2i, core_location_override)
    attempts: list[tuple[bool, str]] = [(aggressive, world_prefix_for_i2i)]
    if quality_on:
        attempts.append((True, retry_world))

    last_reason = ""
    edited_url: str | None = None
    body: bytes | None = None

    for attempt_i, (attempt_aggressive, world_wp) in enumerate(attempts):
        if attempt_i > 0:
            loc_note = (core_location_override or "").strip()
            suffix = (
                f" (core_location_override: {loc_note})"
                if loc_note
                else " (aggressive prompt sanitize)"
            )
            print(
                f"   [WARN] Segment {segment_index}: scene anchor quality retry{suffix}",
                file=sys.stderr,
            )
        if image_to_image_prompt_override is not None:
            image_to_image_prompt = _sanitize_fal_prompt(
                image_to_image_prompt_override, aggressive=attempt_aggressive
            )
        else:
            image_to_image_prompt = build_scene_anchor_i2i_prompt(
                prompt_without_markers_sanitized=prompt_without_markers_sanitized,
                character_id=character_id,
                segment_index=segment_index,
                opening_frames=opening_frames,
                world_prefix_for_i2i=world_wp,
                aggressive=attempt_aggressive,
                sanitize=_sanitize_fal_prompt,
                dual_reference_speakers=dual_reference_speakers,
            )
        edited_url = _invoke_wan_scene_anchor_i2i(
            segment_index=segment_index,
            portrait_data_uris=uris,
            image_to_image_prompt=image_to_image_prompt,
            negative_i2i=negative_i2i,
            image_size=image_size_resolved,
        )
        if not edited_url:
            return None, None
        body = http_get_bytes_with_retries(
            edited_url,
            label=f"Scene-anchor image seg {segment_index}",
            timeout=120,
        )
        if not quality_on:
            break
        bad, last_reason = detect_composite_seam_anchor(body)
        if not bad:
            break
        print(
            f"   [WARN] Segment {segment_index}: scene anchor quality check failed ({last_reason})",
            file=sys.stderr,
        )
        if attempt_i >= len(attempts) - 1:
            raise SceneAnchorQualityError(
                segment_index, last_reason or "composite_seam", last_reason
            )

    if not edited_url or body is None:
        return None, None

    anchors_dir = output_dir / "anchors"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    saved = _save_anchor_image_bytes(
        segment_index=segment_index,
        character_id=character_id,
        body=body,
        edited_url=edited_url,
        anchors_dir=anchors_dir,
        anchor_save_basename=anchor_save_basename,
    )
    return edited_url, saved


def main() -> None:
    """CLI: regenerate one scene-anchor still from repo root (requires FAL_KEY, narration JSON)."""
    import argparse

    parser = argparse.ArgumentParser(description="Run FAL Wan scene-anchor i2i for one segment.")
    parser.add_argument("date_id", help="Eight-digit date id, e.g. 18040513")
    parser.add_argument("segment", type=int, help="1-based segment index")
    parser.add_argument(
        "--aspect-ratio",
        choices=("9:16", "16:9"),
        default="9:16",
        help="Output image aspect (default 9:16)",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    from video_vendors import (
        build_prompts,
        load_fal_scene_anchor_i2i_meta,
        opening_period_line_for_narration_segment,
    )
    from video_vendors.fal import FalVendor, _sanitize_fal_prompt

    did = str(args.date_id).strip()
    narr_dir = root / "narrations"
    narr_path = narr_dir / f"narration{did}.json"
    narr_data = json.loads(narr_path.read_text(encoding="utf-8-sig"))
    prompts = build_prompts(did, narrations_dir=narr_dir, vendor="fal")
    openings, _worlds, core_overrides = load_fal_scene_anchor_i2i_meta(did, narrations_dir=narr_dir)
    if args.segment < 1 or args.segment > len(prompts):
        raise SystemExit(f"segment must be 1..{len(prompts)}")
    world = opening_period_line_for_narration_segment(narr_data, args.segment, date_id=did)
    raw = prompts[args.segment - 1]
    rest, cid, _scene_anchor = FalVendor._parse_markers(raw)
    if not cid:
        script = narr_data.get("narration_script") or []
        if 0 < args.segment <= len(script) and isinstance(script[args.segment - 1], dict):
            row = script[args.segment - 1]
            cid = (
                row.get("reference_character_id") or row.get("talking_head_subject") or ""
            ).strip()
    sanitized = _sanitize_fal_prompt(rest, aggressive=False)
    out_dir = root / "movie-images" / did
    out_dir.mkdir(parents=True, exist_ok=True)
    clo = None
    if 0 < args.segment <= len(core_overrides):
        clo = core_overrides[args.segment - 1]
    url, path = run_segment_anchor_to_disk(
        repo_root=root,
        segment_index=args.segment,
        prompt_without_markers_sanitized=sanitized,
        character_id=cid or None,
        output_dir=out_dir,
        opening_frames=openings,
        world_prefix_for_i2i=world,
        core_location_override=clo,
        aspect_ratio=args.aspect_ratio,
        aggressive=False,
    )
    if url:
        print(url)
    if path:
        print(path)
    if not url:
        raise SystemExit("FAL returned no image URL.")


if __name__ == "__main__":
    main()
