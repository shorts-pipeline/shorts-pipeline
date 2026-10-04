#!/usr/bin/env python3
"""One-off: pick best Reuben t2i seed (vivid red count), rembg, no face suppress."""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from PIL import Image

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
from dotenv import load_dotenv

load_dotenv(_REPO / ".env")
import fal_client

from video_vendors.fal import _fal_wan_extra_args

PROMPTS = _REPO / "character-portraits" / "portrait_prompts.json"


def vivid_red_in_face(arr: np.ndarray) -> int:
    h, w = arr.shape[:2]
    face = arr[int(h * 0.08) : int(h * 0.34), int(w * 0.30) : int(w * 0.70)]
    r, g, b = face[..., 0].astype(float), face[..., 1].astype(float), face[..., 2].astype(float)
    return int(((r > 155) & (r > g + 35) & (r > b + 30)).sum())


def face_detail_score(arr: np.ndarray) -> float:
    """Higher = more luminance variation (likely real features)."""
    h, w = arr.shape[:2]
    face = arr[int(h * 0.10) : int(h * 0.32), int(w * 0.33) : int(w * 0.67)]
    gray = face.mean(axis=2)
    return float(gray.std())


def main() -> None:
    inp = json.loads(PROMPTS.read_text(encoding="utf-8"))["portraits"]["reuben_fields"]["input"]
    prompt, neg = inp["prompt"], inp["negative_prompt"]
    extra = _fal_wan_extra_args()
    seeds = [42, 4242, 99173, 18040531, 314159, 8675309, 120604, 5551212]
    best: tuple[int, float, int, np.ndarray] | None = None

    for seed in seeds:
        print(f"t2i seed={seed}...")
        result = fal_client.subscribe(
            "fal-ai/wan-25-preview/text-to-image",
            arguments={
                "prompt": prompt[:2000],
                "negative_prompt": neg[:500],
                "num_images": 1,
                "image_size": "portrait_16_9",
                "seed": seed,
                **extra,
            },
        )
        raw = requests.get(result["images"][0]["url"], timeout=120).content
        arr = np.asarray(Image.open(BytesIO(raw)).convert("RGB"))
        red = vivid_red_in_face(arr)
        detail = face_detail_score(arr)
        print(f"  vivid_red={red} face_std={detail:.2f}")
        key = (red, -detail, seed)
        if best is None or key < (best[0], best[1], best[2]):
            best = (red, -detail, seed, arr)

    assert best is not None
    _, _, win_seed, out = best
    print(f"winner seed={win_seed}")
    jpg = _REPO / "character-portraits" / "reuben_fields.jpg"
    Image.fromarray(out).save(jpg, quality=92)
    from rembg import remove

    png = _REPO / "character-portraits" / "reuben_fields.png"
    png.write_bytes(remove(jpg.read_bytes()))
    print(f"[OK] {jpg} and {png}")


if __name__ == "__main__":
    main()
