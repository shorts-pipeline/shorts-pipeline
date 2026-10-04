"""FAL-hosted Kling video models (B-roll i2v/t2v and Avatar talking-head)."""

from __future__ import annotations

from typing import Any

KLING_BROLL_ENGINE = "kling-v3-standard"

KLING_I2V_MODEL_DEFAULT = "fal-ai/kling-video/v3/standard/image-to-video"
KLING_T2V_MODEL_DEFAULT = "fal-ai/kling-video/v3/standard/text-to-video"
KLING_AVATAR_PRO_MODEL = "fal-ai/kling-video/ai-avatar/v2/pro"
KLING_AVATAR_STANDARD_MODEL = "fal-ai/kling-video/ai-avatar/v2/standard"

KLING_PROMPT_MAX_LEN = 2500
KLING_NEGATIVE_MAX_LEN = 500
KLING_BROLL_MIN_SECONDS = 3
KLING_BROLL_MAX_SECONDS = 15


def is_kling_avatar_model(model_id: str) -> bool:
    ml = (model_id or "").lower()
    return "kling" in ml and "avatar" in ml


def resolve_broll_engine(raw: str | None) -> str:
    """Return ``wan`` (default) or ``kling-v3-standard``."""
    v = (raw or "").strip().lower()
    if v in ("kling", "kling-v3", "kling-v3-standard", KLING_BROLL_ENGINE):
        return KLING_BROLL_ENGINE
    return "wan"


def resolve_broll_route_engines(
    cfg: dict | None,
    *,
    fal_broll_engine: str | None = None,
    fal_broll_i2v_engine: str | None = None,
    fal_broll_t2v_engine: str | None = None,
) -> tuple[str, str]:
    """
    Return (i2v_engine, t2v_engine) each ``wan`` or ``kling-v3-standard``.

    Per-route keys override legacy ``fal_broll_engine`` when set.
    """
    c = cfg or {}
    legacy = resolve_broll_engine(fal_broll_engine or c.get("fal_broll_engine"))
    i2v = resolve_broll_engine(fal_broll_i2v_engine or c.get("fal_broll_i2v_engine") or legacy)
    t2v = resolve_broll_engine(fal_broll_t2v_engine or c.get("fal_broll_t2v_engine") or legacy)
    return i2v, t2v


def kling_duration_seconds(
    sec: float,
    *,
    min_seconds: float = KLING_BROLL_MIN_SECONDS,
    max_seconds: float = KLING_BROLL_MAX_SECONDS,
) -> str:
    """
    Map TTS segment length to Kling v3 ``duration`` enum (integer seconds as string).

    Uses ``round`` to the nearest second, then clamps to ``min_seconds``–``max_seconds``
    (API allows 3–15 for v3 Standard).
    """
    lo = max(1.0, float(min_seconds))
    hi = max(lo, float(max_seconds))
    raw = float(sec or 5.0)
    bounded = min(hi, max(lo, raw))
    return str(int(round(bounded)))


def kling_broll_duration_limits(cfg: dict | None) -> tuple[float, float]:
    """Return (min_seconds, max_seconds) for Kling B-roll from narration config."""
    c = cfg or {}
    try:
        lo = float(c.get("fal_kling_broll_min_seconds", KLING_BROLL_MIN_SECONDS))
    except (TypeError, ValueError):
        lo = float(KLING_BROLL_MIN_SECONDS)
    try:
        hi = float(c.get("fal_kling_broll_max_seconds", KLING_BROLL_MAX_SECONDS))
    except (TypeError, ValueError):
        hi = float(KLING_BROLL_MAX_SECONDS)
    lo = max(1.0, lo)
    hi = max(lo, hi)
    return lo, hi


def kling_i2v_arguments(
    *,
    prompt: str,
    start_image_url: str,
    duration_seconds: float,
    aspect_ratio: str,
    negative_prompt: str = "",
    i2v_model: str = KLING_I2V_MODEL_DEFAULT,
    min_seconds: float = KLING_BROLL_MIN_SECONDS,
    max_seconds: float = KLING_BROLL_MAX_SECONDS,
) -> tuple[str, dict[str, Any]]:
    args: dict[str, Any] = {
        "prompt": (prompt or "")[:KLING_PROMPT_MAX_LEN],
        "start_image_url": start_image_url,
        "duration": kling_duration_seconds(
            duration_seconds,
            min_seconds=min_seconds,
            max_seconds=max_seconds,
        ),
        "generate_audio": False,
        "aspect_ratio": (aspect_ratio or "9:16").strip(),
    }
    neg = (negative_prompt or "").strip()
    if neg:
        args["negative_prompt"] = neg[:KLING_NEGATIVE_MAX_LEN]
    return i2v_model, args


def kling_t2v_arguments(
    *,
    prompt: str,
    duration_seconds: float,
    aspect_ratio: str,
    negative_prompt: str = "",
    t2v_model: str = KLING_T2V_MODEL_DEFAULT,
    min_seconds: float = KLING_BROLL_MIN_SECONDS,
    max_seconds: float = KLING_BROLL_MAX_SECONDS,
) -> tuple[str, dict[str, Any]]:
    args: dict[str, Any] = {
        "prompt": (prompt or "")[:KLING_PROMPT_MAX_LEN],
        "duration": kling_duration_seconds(
            duration_seconds,
            min_seconds=min_seconds,
            max_seconds=max_seconds,
        ),
        "generate_audio": False,
        "aspect_ratio": (aspect_ratio or "9:16").strip(),
    }
    neg = (negative_prompt or "").strip()
    if neg:
        args["negative_prompt"] = neg[:KLING_NEGATIVE_MAX_LEN]
    return t2v_model, args


def kling_avatar_arguments(
    *,
    image_url: str,
    audio_url: str,
    prompt: str | None = None,
) -> dict[str, object]:
    args: dict[str, object] = {
        "image_url": image_url,
        "audio_url": audio_url,
    }
    p = (prompt or "").strip()
    if p:
        args["prompt"] = p[:KLING_PROMPT_MAX_LEN]
    return args
