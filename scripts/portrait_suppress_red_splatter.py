#!/usr/bin/env python3
"""
Remove saturated red/pink splatter artifacts common on FAL Wan portrait faces and shirts.

Replaces only high-saturation red/magenta pixels with median skin/cloth sampled from
nearby clean pixels (not a full-frame blur).

  python scripts/portrait_suppress_red_splatter.py character-portraits/reuben_fields.jpg
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation


def _red_splatter_mask(rgb: np.ndarray) -> np.ndarray:
    """Tight mask: vivid splatter only, not tan skin or brown leather."""
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    # Strong red/magenta splatter
    vivid = (r > 155) & (r > g + 35) & (r > b + 30)
    magenta = (r > 140) & (b > 90) & (r > g + 25) & (g < 110)
    return vivid | magenta


def _soft_cheek_red_mask(rgb: np.ndarray) -> np.ndarray:
    """Softer blush/splatter on cheeks (below vivid threshold)."""
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    return (r > 120) & (r > g + 15) & (r > b + 10) & (r + g + b > 320)


def _is_skin_like(rgb: np.ndarray) -> np.ndarray:
    """Warm natural skin / tan fabric — exclude gray studio backdrop and near-black."""
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    bright = r + g + b > 200
    not_gray = (np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)) > 22
    warm = (r >= g - 5) & (g >= b - 10) & (r > 70)
    return bright & not_gray & warm


def _sample_clean_median(
    rgb: np.ndarray, mask: np.ndarray, region: np.ndarray
) -> np.ndarray | None:
    clean = region & ~mask & _is_skin_like(rgb)
    if not clean.any():
        return None
    return np.median(rgb[clean], axis=0).astype(np.uint8)


def suppress_red_splatter(
    rgb: np.ndarray,
    dilate: int = 1,
    *,
    include_soft_cheeks: bool = False,
) -> np.ndarray:
    mask = _red_splatter_mask(rgb)
    if dilate > 0:
        mask = binary_dilation(mask, iterations=dilate)
    if not mask.any():
        return rgb

    h, w = rgb.shape[:2]
    face_region = np.zeros((h, w), dtype=bool)
    face_region[int(h * 0.06) : int(h * 0.36), int(w * 0.32) : int(w * 0.68)] = True
    torso_region = np.zeros((h, w), dtype=bool)
    torso_region[int(h * 0.28) : int(h * 0.88), int(w * 0.12) : int(w * 0.88)] = True

    out = rgb.copy()
    if include_soft_cheeks:
        face_mask = (mask | (_soft_cheek_red_mask(rgb) & face_region)) & face_region
    else:
        face_mask = mask & face_region
    if face_mask.any():
        skin = _sample_clean_median(rgb, face_mask, face_region)
        if skin is not None:
            out[face_mask] = skin

    cloth_mask = mask & torso_region & ~face_region
    if cloth_mask.any():
        cloth = _sample_clean_median(rgb, mask, torso_region)
        if cloth is not None:
            out[cloth_mask] = cloth

    other = mask & ~face_mask & ~cloth_mask
    if other.any():
        global_clean = _sample_clean_median(rgb, mask, np.ones((h, w), dtype=bool))
        if global_clean is not None:
            out[other] = global_clean

    # Second pass for leftover vivid pixels
    leftover = _red_splatter_mask(out) & mask
    if leftover.any() and face_mask.any():
        skin = _sample_clean_median(out, leftover, face_region)
        if skin is not None:
            out[leftover & face_region] = skin

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Suppress red splatter on portrait stills")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--dilate", type=int, default=1, help="Mask dilation iterations")
    parser.add_argument(
        "--include-soft-cheeks",
        action="store_true",
        help="Also mask softer pink cheeks (risk: flattens entire face)",
    )
    args = parser.parse_args()

    inp = args.input.resolve()
    if not inp.exists():
        raise SystemExit(f"Not found: {inp}")

    out = (args.output or inp).resolve()
    im = Image.open(inp).convert("RGB")
    arr = np.asarray(im)
    fixed = suppress_red_splatter(
        arr, dilate=args.dilate, include_soft_cheeks=args.include_soft_cheeks
    )
    if out.suffix.lower() in (".jpg", ".jpeg"):
        Image.fromarray(fixed).save(out, quality=92)
    else:
        Image.fromarray(fixed).save(out)

    h, w = fixed.shape[:2]
    face = fixed[int(h * 0.04) : int(h * 0.40), int(w * 0.18) : int(w * 0.82)]
    n = int(_red_splatter_mask(face).sum())
    print(f"[OK] {out} (face vivid-red pixels after fix: {n})")


if __name__ == "__main__":
    main()
