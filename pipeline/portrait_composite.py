"""Combine two portrait images side-by-side (same height, optional gap). Used by scripts/build_portrait.py."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

_REPO = Path(__file__).resolve().parent.parent
PORTRAITS_DIR = _REPO / "character-portraits"


def composite_portrait_gap_px() -> int:
    """Side-by-side gap for runtime composite builds (``fal_conversation_scene_anchor`` config)."""
    try:
        from pipeline.narration_common import load_narration_config

        block = (load_narration_config() or {}).get("fal_conversation_scene_anchor")
        if isinstance(block, dict) and "composite_gap_px" in block:
            return max(0, int(block["composite_gap_px"]))
    except Exception:
        pass
    return 8


def composite_portrait_cached_path(
    composite_id: str,
    *,
    portraits_dir: Path | None = None,
) -> Path:
    """Canonical cached composite PNG path for ``composite_id``."""
    root = portraits_dir or PORTRAITS_DIR
    return root / f"{(composite_id or '').strip()}.png"


def load_rgba(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def resolve_portrait_stem(base: Path) -> Path:
    """Prefer .png over .jpg/.jpeg when both exist (pipeline convention). `base` has no suffix (e.g. .../lewis)."""
    for ext in (".png", ".jpg", ".jpeg"):
        p = base.with_suffix(ext)
        if p.is_file():
            return p
    return base.with_suffix(".png")


def build_side_by_side_composite(
    left_path: Path,
    right_path: Path,
    out_path: Path,
    *,
    gap_px: int = 8,
    target_height: int | None = None,
) -> None:
    a = load_rgba(left_path)
    b = load_rgba(right_path)
    h = target_height or max(a.height, b.height)

    def scale_to_height(im: Image.Image, height: int) -> Image.Image:
        if im.height == height:
            return im
        w = max(1, round(im.width * (height / im.height)))
        return im.resize((w, height), Image.Resampling.LANCZOS)

    a = scale_to_height(a, h)
    b = scale_to_height(b, h)
    gap = max(0, gap_px)
    w = a.width + gap + b.width
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(a, (0, 0), a)
    canvas.paste(b, (a.width + gap, 0), b)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")


def ensure_composite_portrait_cached(
    *,
    composite_id: str,
    left_member_id: str,
    right_member_id: str,
    portraits_dir: Path | None = None,
    gap_px: int | None = None,
    force: bool = False,
) -> Path:
    """
    Return ``character-portraits/<composite_id>.png``, building it from solo portraits when missing.

    Left/right placement follows ``left_member_id`` / ``right_member_id`` (config order or ad-hoc).
    """
    root = portraits_dir or PORTRAITS_DIR
    out_path = composite_portrait_cached_path(composite_id, portraits_dir=root)
    if not force and out_path.is_file():
        return out_path
    left_id = (left_member_id or "").strip()
    right_id = (right_member_id or "").strip()
    if not left_id or not right_id:
        raise ValueError("left_member_id and right_member_id required for composite build")
    left_p = resolve_portrait_stem(root / left_id)
    right_p = resolve_portrait_stem(root / right_id)
    if not left_p.is_file():
        raise FileNotFoundError(f"Missing solo portrait for composite left member: {left_p}")
    if not right_p.is_file():
        raise FileNotFoundError(f"Missing solo portrait for composite right member: {right_p}")
    build_side_by_side_composite(
        left_p,
        right_p,
        out_path,
        gap_px=composite_portrait_gap_px() if gap_px is None else gap_px,
    )
    return out_path
