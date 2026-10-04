#!/usr/bin/env python3
"""
Generate character-portraits/<id>.jpg from portrait_prompts.json using
fal-ai/wan-25-preview/text-to-image (no source photo required).

Requires FAL_KEY. Example:
  python scripts/fal_portrait_text_to_image.py york
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
except ImportError:
    pass

import requests

from video_vendors.fal import _fal_wan_extra_args

PROMPTS_JSON = _REPO / "character-portraits" / "portrait_prompts.json"

DEFAULT_NEGATIVE = (
    "modern clothing, jewelry, watch, sunglasses, cartoon, illustration, painting, anime, "
    "low resolution, blurry, worst quality, deformed hands, extra fingers, duplicate face, "
    "text, watermark, logo"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FAL text-to-image portrait JPG from portrait_prompts.json"
    )
    parser.add_argument("character_id", help="e.g. york, charbonneau")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Override output path (default: character-portraits/<id>.jpg)",
    )
    parser.add_argument(
        "--image-size",
        default=None,
        help="Override image_size enum (default: from JSON or portrait_16_9)",
    )
    parser.add_argument(
        "--negative-prompt",
        default=None,
        metavar="TEXT",
        help="Override negative prompt (default: portrait_prompts input.negative_prompt, else built-in)",
    )
    args = parser.parse_args()

    cid = args.character_id.strip().lower()
    if not PROMPTS_JSON.exists():
        raise SystemExit(f"Missing {PROMPTS_JSON}")

    data = json.loads(PROMPTS_JSON.read_text(encoding="utf-8"))
    portraits = data.get("portraits") or {}
    if cid not in portraits:
        raise SystemExit(f"No entry in portrait_prompts.json for {cid!r}")

    entry = portraits[cid]
    inp = entry.get("input") or {}
    prompt = (inp.get("prompt") or "").strip()
    if not prompt:
        raise SystemExit(f"No input.prompt for {cid!r}")

    image_size = args.image_size or inp.get("image_size") or "portrait_16_9"
    # JSON may store legacy "1024x1536" — map to portrait-ish enum
    if isinstance(image_size, str) and "x" in image_size.lower():
        image_size = "portrait_16_9"

    negative_prompt = args.negative_prompt
    if negative_prompt is None:
        negative_prompt = (inp.get("negative_prompt") or "").strip() or DEFAULT_NEGATIVE

    api_key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
    if not api_key:
        raise SystemExit("Set FAL_KEY (or FAL_API_KEY)")

    try:
        import fal_client
    except ImportError as e:
        raise SystemExit("pip install fal-client") from e

    extra = _fal_wan_extra_args()
    # Text-to-image uses enable_prompt_expansion in schema; align with pipeline defaults
    t2i_args = {
        "prompt": prompt[:2000],
        "negative_prompt": negative_prompt[:500],
        "num_images": 1,
        "image_size": image_size,
        "enable_prompt_expansion": extra.get("enable_prompt_expansion", False),
        "enable_safety_checker": extra.get("enable_safety_checker", True),
    }

    print(f"Calling fal-ai/wan-25-preview/text-to-image (image_size={image_size})...")
    result = fal_client.subscribe(
        "fal-ai/wan-25-preview/text-to-image",
        arguments=t2i_args,
    )
    images = result.get("images") or []
    if not images:
        raise SystemExit(f"No images in response: {result!r}")
    url = images[0].get("url")
    if not url:
        raise SystemExit(f"No URL: {images[0]!r}")

    out = args.output or (_REPO / "character-portraits" / f"{cid}.jpg")
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    r = requests.get(url, timeout=120)
    r.raise_for_status()
    # API returns PNG; save as .jpg by re-encoding for smaller canonical asset, or save as PNG renamed
    # User asked for jpg — convert PNG bytes to JPEG with Pillow if possible
    raw = r.content
    if out.suffix.lower() in (".jpg", ".jpeg"):
        try:
            from io import BytesIO

            from PIL import Image

            im = Image.open(BytesIO(raw)).convert("RGB")
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=92, optimize=True)
            out.write_bytes(buf.getvalue())
        except Exception:
            # Fallback: write PNG to .jpg path would be wrong; write .png instead
            out = out.with_suffix(".png")
            out.write_bytes(raw)
            print(f"[WARN] Saved as PNG (install Pillow for JPEG): {out}")
    else:
        out.write_bytes(raw)

    print(f"[OK] {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
