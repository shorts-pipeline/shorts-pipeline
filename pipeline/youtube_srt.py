"""Build timed English SRT captions from narration JSON + audio durations.json."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from pipeline.narration_utils import load_narration, narration_script_row_1based
from pipeline.tts_stage_directions import spoken_text_only_for_tts

_SEG_FILE_RE = re.compile(r"^segments/(\d+)\.mp3$", re.IGNORECASE)


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds as SRT timestamp HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000.0))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _duration_entry_segment_index(entry: dict) -> int | None:
    """Map durations.json entry to segment index: intro -> 0, segments/07.mp3 -> 7."""
    f = str(entry.get("file") or "").replace("\\", "/").strip()
    if f == "intro":
        return 0
    m = _SEG_FILE_RE.match(f)
    if m:
        return int(m.group(1))
    return None


def caption_text_for_segment(seg: dict) -> str:
    """
    Approximate on-screen caption text for one narration segment.

    Matches TTS layout: talking_head (and cast-dialogue-only style) use dialogue only;
    other segments with dialogue include narration lead-in when present.
    """
    dialogue_parts: list[str] = []
    rows = seg.get("dialogue")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            spoken = spoken_text_only_for_tts(str(row.get("text") or ""))
            if spoken.strip():
                dialogue_parts.append(spoken.strip())

    narr = str(seg.get("narration") or "").strip()
    vm = str(seg.get("visual_mode") or "").strip().lower()
    if vm == "talking_head" and dialogue_parts:
        return " ".join(dialogue_parts)
    if dialogue_parts and narr:
        return f"{narr} {' '.join(dialogue_parts)}".strip()
    if dialogue_parts:
        return " ".join(dialogue_parts)
    return narr


def intro_caption_text(date_id: str) -> str:
    """Spoken map-intro date phrase (same wording as map_intro TTS)."""
    d = datetime.strptime(date_id, "%Y%m%d")
    return d.strftime("%B %d, %Y")


def load_durations(date_id: str, *, repo_root: Path | None = None) -> list[dict]:
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    path = root / "audio" / date_id / "durations.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def build_srt_cues(
    date_id: str,
    *,
    repo_root: Path | None = None,
    narration: dict | None = None,
    durations: list[dict] | None = None,
) -> list[tuple[float, float, str]]:
    """
    Return ordered cues as (start_sec, end_sec, text).

    Skips empty text and non-positive durations. Raises ValueError when durations
    are missing/empty (callers should skip caption upload).
    """
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    if durations is None:
        durations = load_durations(date_id, repo_root=root)
    if not durations:
        raise ValueError(f"No audio/{date_id}/durations.json (needed for caption timing)")

    if narration is None:
        narration = load_narration(date_id, narrations_dir=root / "narrations") or {}

    cues: list[tuple[float, float, str]] = []
    t = 0.0
    for entry in durations:
        if not isinstance(entry, dict):
            continue
        try:
            dur = float(entry.get("duration") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        if dur <= 0:
            continue
        start = t
        end = t + dur
        t = end

        si = _duration_entry_segment_index(entry)
        if si == 0:
            text = intro_caption_text(date_id)
        elif si is not None and si > 0:
            seg = narration_script_row_1based(narration, si) or {}
            text = caption_text_for_segment(seg)
        else:
            text = ""
        text = " ".join(str(text).split())
        if not text:
            continue
        cues.append((start, end, text))
    return cues


def render_srt(cues: list[tuple[float, float, str]]) -> str:
    """Serialize cues to SRT file contents (UTF-8 text)."""
    blocks: list[str] = []
    for i, (start, end, text) in enumerate(cues, start=1):
        if end <= start:
            end = start + 0.001
        blocks.append(f"{i}\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def build_episode_srt(
    date_id: str,
    *,
    repo_root: Path | None = None,
) -> str:
    """Build full SRT string for an episode date_id."""
    return render_srt(build_srt_cues(date_id, repo_root=repo_root))


def write_episode_srt(
    date_id: str,
    *,
    repo_root: Path | None = None,
    out_path: Path | None = None,
) -> Path:
    """Write captions.en.srt under audio/<date_id>/ (or out_path). Returns path written."""
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    path = Path(out_path) if out_path is not None else root / "audio" / date_id / "captions.en.srt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_episode_srt(date_id, repo_root=root), encoding="utf-8")
    return path
