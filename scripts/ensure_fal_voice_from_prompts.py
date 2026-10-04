"""
Create a FAL MiniMax custom voice from character-portraits/voice_prompts.json and
persist custom_voice_id plus the API response. Skips if custom_voice_id is already
set (unless --force).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _utc_date_str() -> str:
    return datetime.now(UTC).date().isoformat()


def _portraits_dir(repo: Path) -> Path:
    return repo / "character-portraits"


def preview_audio_relpath(voice_key: str) -> str:
    """Path under character-portraits/ (forward slashes for JSON)."""
    return f"voice_previews/{voice_key}_preview.mp3"


def preview_audio_url_from_block(block: dict) -> str:
    pa = block.get("preview_audio")
    if isinstance(pa, dict):
        u = str(pa.get("url") or "").strip()
        if u:
            return u
    fr = block.get("fal_voice_design_response")
    if isinstance(fr, dict):
        audio = fr.get("audio")
        if isinstance(audio, dict):
            u = str(audio.get("url") or "").strip()
            if u:
                return u
    return ""


def download_preview_mp3(repo: Path, voice_key: str, url: str) -> Path:
    try:
        import requests
    except ImportError:
        raise RuntimeError("pip install requests") from None
    out_dir = _portraits_dir(repo) / "voice_previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{voice_key}_preview.mp3"
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(_repo_root() / ".env")
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="Ensure FAL voice-design id exists in voice_prompts.json"
    )
    parser.add_argument(
        "--voice",
        default="clark",
        help="Key under voices/ (default: clark)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Call voice-design even if custom_voice_id is already set",
    )
    parser.add_argument(
        "--download-preview",
        action="store_true",
        help="Download preview MP3 from saved URL to character-portraits/voice_previews/ (no API call)",
    )
    args = parser.parse_args()
    key = (args.voice or "").strip().lower()
    if not key:
        print("ERROR: empty --voice", file=sys.stderr)
        return 2

    path = _repo_root() / "character-portraits" / "voice_prompts.json"
    if not path.exists():
        print(f"ERROR: missing {path}", file=sys.stderr)
        return 2

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: read {path}: {e}", file=sys.stderr)
        return 2

    voices = data.get("voices")
    if not isinstance(voices, dict) or key not in voices:
        print(f"ERROR: no voices['{key}'] in {path}", file=sys.stderr)
        return 2

    block = voices[key]
    if not isinstance(block, dict):
        print(f"ERROR: voices['{key}'] must be an object", file=sys.stderr)
        return 2

    repo = _repo_root()

    if args.download_preview:
        url = preview_audio_url_from_block(block)
        if not url:
            print(
                f"ERROR: no preview URL for voices['{key}'] (preview_audio.url or fal_voice_design_response)",
                file=sys.stderr,
            )
            return 2
        try:
            dest = download_preview_mp3(repo, key, url)
        except Exception as e:
            print(f"ERROR: download failed: {e}", file=sys.stderr)
            return 2
        rel = preview_audio_relpath(key)
        pa = block.get("preview_audio")
        if not isinstance(pa, dict):
            pa = {}
        pa["local_file"] = rel
        if not pa.get("url"):
            pa["url"] = url
        if not pa.get("note"):
            pa["note"] = (
                "local_file is under character-portraits/; url is FAL CDN (may expire). "
                "Re-download with: scripts/ensure_fal_voice_from_prompts.py --voice "
                f"{key} --download-preview"
            )
        block["preview_audio"] = pa
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Saved preview to {dest} ({rel})")
        return 0

    existing = (block.get("custom_voice_id") or "").strip()
    if existing and not args.force:
        print(
            f"voices['{key}'].custom_voice_id already set ({existing}); skip. Use --force to recreate."
        )
        return 0

    req = block.get("fal_voice_design_request")
    if not isinstance(req, dict):
        print(f"ERROR: voices['{key}'] missing fal_voice_design_request", file=sys.stderr)
        return 2
    inp = req.get("input")
    if not isinstance(inp, dict):
        print(f"ERROR: fal_voice_design_request.input missing for '{key}'", file=sys.stderr)
        return 2
    prompt = str(inp.get("prompt") or "").strip()
    preview_text = str(inp.get("preview_text") or "").strip()
    if not prompt or not preview_text:
        print(
            f"ERROR: prompt and preview_text required in fal_voice_design_request.input for '{key}'",
            file=sys.stderr,
        )
        return 2

    endpoint = str(req.get("endpoint") or "fal-ai/minimax/voice-design").strip()

    try:
        import fal_client
    except ImportError:
        print("ERROR: pip install fal-client", file=sys.stderr)
        return 2

    api_key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
    if not api_key:
        print("ERROR: set FAL_KEY (or FAL_API_KEY)", file=sys.stderr)
        return 2

    print(f"Calling {endpoint} for voices['{key}']...")
    result = fal_client.subscribe(
        endpoint,
        {"prompt": prompt, "preview_text": preview_text},
    )
    if not isinstance(result, dict):
        print(f"ERROR: unexpected response type: {type(result)}", file=sys.stderr)
        return 2

    vid = str(result.get("custom_voice_id") or "").strip()
    if not vid:
        print(f"ERROR: response missing custom_voice_id: {result!r}", file=sys.stderr)
        return 2

    audio = result.get("audio")
    preview_url = ""
    if isinstance(audio, dict):
        preview_url = str(audio.get("url") or "").strip()

    block["custom_voice_id"] = vid
    block["custom_voice_id_source"] = (
        f"FAL MiniMax voice-design response ({_utc_date_str()} UTC date; id embeds server timestamp)."
    )
    block["fal_voice_design_response"] = result
    block["voice_design_completed_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
    if preview_url:
        try:
            download_preview_mp3(repo, key, preview_url)
            rel = preview_audio_relpath(key)
            local_note = f" Also saved as character-portraits/{rel}."
        except Exception as e:
            print(f"WARN: could not save local preview: {e}", file=sys.stderr)
            rel = ""
            local_note = ""
        pa: dict = {
            "url": preview_url,
            "note": (
                "CDN URL; local copy under character-portraits/ when present."
                + local_note
                + " Re-download: scripts/ensure_fal_voice_from_prompts.py --voice "
                + f"{key} --download-preview"
            ),
        }
        if rel:
            pa["local_file"] = rel
        block["preview_audio"] = pa

    log = block.get("retention_log")
    if not isinstance(log, list):
        log = []
    log.append(
        {
            "recorded_at": _utc_date_str(),
            "event": "voice_design",
            "custom_voice_id": vid,
            "note": "Initial FAL response saved to voice_prompts.json; run TTS with this id and append when verified.",
        }
    )
    block["retention_log"] = log

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote custom_voice_id={vid} to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
