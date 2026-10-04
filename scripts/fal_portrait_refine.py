#!/usr/bin/env python3
"""
Run fal-ai/wan-25-preview/image-to-image on a portrait still, then optionally rembg → RGBA PNG.

Requires FAL_KEY (or FAL_API_KEY). Uses same Wan extras as video_vendors/fal (_fal_wan_extra_args).

Example (Lewis: remove staff, cleaner boots):
  python scripts/fal_portrait_refine.py character-portraits/lewis.jpg \\
    --output-rgb character-portraits/lewis.fal_edit.png \\
    --output-png character-portraits/lewis.png
"""

from __future__ import annotations

import argparse
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

from video_vendors.fal import FalVendor, _fal_wan_extra_args

DEFAULT_PROMPT = """Edit this reference portrait. Keep the same person: identity, face, expression, hat, coat, and overall pose.
FRAMING: Full-length portrait — show the COMPLETE figure from hat to ground. BOTH boots must be FULLY visible: entire shafts, ankles, soles, and heels on the ground; leave a clear margin of empty field below the feet. Do NOT crop the legs or cut off half a boot at the bottom edge of the image.
GROUND / BACKGROUND: Wide uniform open field only — one consistent flat expanse of short grass OR one even earth tone (no planks, boat pieces, rocks, or clutter). Same simple field texture behind the legs and under the boots so footwear reads clearly.
Remove the walking staff, pole, or stick from his LEFT hand entirely — hand empty or resting naturally at his side.
Remove any BOAT, canoe, hull, oar, or river craft — replace with that same uniform field; no watercraft.
Boots: reduce heavy mud; LEFT boot slightly lighter midtones, warm brown leather, readable toe and welt, not a black silhouette; period-worn.
Keep the journal and coat unchanged. Photorealistic, early 1800s U.S. Army explorer."""

DEFAULT_NEGATIVE = (
    "walking stick, staff, pole, cane in hand, boat, canoe, kayak, rowboat, hull, oar, paddle, "
    "watercraft behind person, river boat, cropped feet, cut-off boots, boots clipped, feet out of frame, "
    "half boot missing, tight crop on legs, ground clutter, planks, dock, busy foreground, "
    "muddy boots fused with ground, feet vanishing into dirt, left boot pure black, boot silhouette, "
    "crushed shadows on boots, legs merged with earth, cartoon, illustration, painting, low resolution, "
    "blurry, deformed hands, extra fingers, duplicate person, modern clothing, text, watermark"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="FAL i2i portrait refine + optional rembg")
    parser.add_argument("input", type=Path, help="Source image (.jpg / .png)")
    parser.add_argument(
        "--output-rgb",
        type=Path,
        required=True,
        help="Where to save the FAL result (e.g. lewis.fal_edit.png)",
    )
    parser.add_argument(
        "--output-png",
        type=Path,
        default=None,
        help="RGBA path after rembg (writes transparent PNG)",
    )
    parser.add_argument("--no-rembg", action="store_true", help="Do not run rembg")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="image-to-image prompt")
    parser.add_argument("--negative-prompt", default=DEFAULT_NEGATIVE, help="negative prompt")
    parser.add_argument(
        "--image-size",
        default="portrait_16_9",
        help="Wan i2i image_size enum (default portrait_16_9 — more vertical room for full boots)",
    )
    args = parser.parse_args()

    inp = args.input.resolve()
    if not inp.exists():
        raise SystemExit(f"Input not found: {inp}")

    api_key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
    if not api_key:
        raise SystemExit("Set FAL_KEY (or FAL_API_KEY) for fal.ai")

    try:
        import fal_client
    except ImportError as e:
        raise SystemExit("Install fal-client: pip install fal-client") from e

    data_uri = FalVendor._data_uri_for_image(inp)
    ti_args = {
        "prompt": args.prompt[:2000],
        "image_urls": [data_uri],
        "negative_prompt": args.negative_prompt[:2000],
        "image_size": args.image_size,
    }
    ti_args.update(_fal_wan_extra_args())

    print("Calling fal-ai/wan-25-preview/image-to-image ...")
    result = fal_client.subscribe(
        "fal-ai/wan-25-preview/image-to-image",
        arguments=ti_args,
    )
    images = result.get("images") or []
    if not images:
        raise SystemExit(f"No images in response: {result!r}")
    url = images[0].get("url")
    if not url:
        raise SystemExit(f"No URL in first image: {images[0]!r}")

    out_rgb = args.output_rgb.resolve()
    out_rgb.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    ext = ".png"
    if "." in url.rsplit("/", 1)[-1]:
        tail = url.rsplit("/", 1)[-1].split("?")[0]
        if "." in tail:
            cand = "." + tail.rsplit(".", 1)[-1]
            if len(cand) <= 5:
                ext = cand
    if out_rgb.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        out_rgb = out_rgb.with_suffix(ext)
    out_rgb.write_bytes(r.content)
    print(f"[OK] Saved FAL output ({len(r.content)} bytes) -> {out_rgb}")

    if args.output_png and not args.no_rembg:
        try:
            from rembg import remove
        except ImportError as e:
            raise SystemExit("Install rembg: pip install 'rembg[cpu]'") from e
        png_path = args.output_png.resolve()
        png_path.parent.mkdir(parents=True, exist_ok=True)
        rgba = remove(out_rgb.read_bytes())
        png_path.write_bytes(rgba)
        print(f"[OK] rembg -> {png_path} ({png_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
