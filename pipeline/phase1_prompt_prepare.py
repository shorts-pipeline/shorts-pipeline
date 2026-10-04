"""
Build Phase 1 prompt inputs from journal XML: entry text, theme focus, TEI notes.
Shared by generate-narration-two-phase.py and pipeline_ui preview.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pipeline.narration_common import extract_entry_linked_notes, extract_entry_text_and_author

try:
    _repo_root = Path(__file__).resolve().parent.parent
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))
    from theme_engine.theme_selector import recommend as theme_recommend
except ImportError:
    theme_recommend = None


def prepare_phase1_prompt_context(
    repo_root: Path,
    date_id: str,
    *,
    xml_rel: str = "journal-entries",
    no_theme_selector: bool = False,
) -> dict[str, Any]:
    """
    Return entry_text (after blank-page / theme handling), entry_author, focus_topic,
    editorial_notes, theme_recommend_result, is_blank_page.

    On failure: {"ok": False, "error": str, "message": str}
    """
    if len(date_id) != 8 or not date_id.isdigit():
        return {"ok": False, "error": "bad_date_id", "message": "date_id must be 8 digits"}

    xml_path = repo_root / xml_rel / f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:]}.xml"
    if not xml_path.is_file():
        return {
            "ok": False,
            "error": "xml_not_found",
            "message": f"Missing journal XML: {xml_path.relative_to(repo_root)}",
        }

    entry_text, entry_author = extract_entry_text_and_author(xml_path)
    tei_linked_notes = extract_entry_linked_notes(xml_path)
    is_blank_page = not entry_text.strip()

    theme_journal_dir = repo_root / xml_rel
    theme_narrations_dir = repo_root / "narrations"
    theme_state_path = repo_root / "theme_engine" / "state.json"
    theme_embeddings_path = repo_root / "theme_engine" / "embeddings.json"

    focus_topic: str | None = None
    theme_recommend_result: dict[str, Any] | None = None

    if no_theme_selector:
        pass
    elif theme_recommend is not None:
        try:
            result = theme_recommend(
                date_id,
                journal_dir=theme_journal_dir,
                narrations_dir=theme_narrations_dir,
                state_path=theme_state_path,
                embeddings_path=theme_embeddings_path,
            )
            if isinstance(result, dict):
                theme_recommend_result = result
            focus_topic = result.get("focus_topic") if isinstance(result, dict) else None
            if is_blank_page and focus_topic:
                entry_text = (
                    "No journal entry was recorded for this date. The expedition remained in camp. "
                    f"Create a short narration (3–5 segments) that reflects on {focus_topic} in the context "
                    "of the Lewis and Clark expedition at this point in the journey."
                )
            elif is_blank_page:
                entry_text = (
                    "No journal entry was recorded for this date. "
                    "Create a short reflective narration (2–4 segments) appropriate for the expedition in camp."
                )
        except Exception:
            if is_blank_page:
                entry_text = (
                    "No journal entry was recorded for this date. "
                    "Create a short reflective narration (2–4 segments) appropriate for the expedition in camp."
                )
    elif is_blank_page:
        entry_text = (
            "No journal entry was recorded for this date. "
            "Create a short reflective narration (2–4 segments) appropriate for the expedition in camp."
        )

    if is_blank_page and not (entry_text or "").strip():
        entry_text = (
            "No journal entry was recorded for this date. "
            "Create a short reflective narration (2–4 segments) appropriate for the expedition in camp."
        )

    if not (entry_text or "").strip():
        return {
            "ok": False,
            "error": "no_entry_text",
            "message": "No entry text extracted from XML and no blank-page placeholder applied.",
        }

    editorial = (tei_linked_notes or "").strip() or None
    return {
        "ok": True,
        "date_id": date_id,
        "journal_date": f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:8]}",
        "entry_text": entry_text,
        "entry_author": entry_author,
        "focus_topic": focus_topic,
        "editorial_notes": editorial,
        "theme_recommend_result": theme_recommend_result,
        "is_blank_page": is_blank_page,
        "xml_path": str(xml_path.relative_to(repo_root)).replace("\\", "/"),
    }
