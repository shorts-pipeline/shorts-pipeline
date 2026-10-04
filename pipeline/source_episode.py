"""
Resolve primary text and metadata for narration from a pipeline profile.

Journal (TEI) sources delegate to phase1_prompt_prepare; plain files read UTF-8 text.
`parent_document_id` / `chunk_index` are reserved for future long-document chunking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline.phase1_prompt_prepare import prepare_phase1_prompt_context
from pipeline.pipeline_profile import PipelineProfile


def _hooks_from_profile(profile: PipelineProfile) -> dict[str, Any]:
    return {
        "parent_document_id": profile.long_document.parent_document_id,
        "chunk_index": profile.long_document.chunk_index,
        "chunk_note": profile.long_document.chunk_note,
    }


def load_episode_source_bundle(
    repo_root: Path,
    profile: PipelineProfile,
    date_id: str,
    *,
    xml_rel: str | None = None,
    source_text_file: Path | None = None,
    no_theme_selector: bool = False,
) -> dict[str, Any]:
    """
    Return the same keys as prepare_phase1_prompt_context on success, plus optional
    parent_document_id, chunk_index, chunk_note for future multi-chunk workflows.

    On error: {"ok": False, "error": str, "message": str}
    """
    hooks = _hooks_from_profile(profile)
    stype = profile.source_type.lower().strip()

    if stype == "tei_journal":
        rel = xml_rel or profile.source_xml_dir
        effective_no_theme = bool(no_theme_selector or not profile.use_theme_engine)
        ctx = prepare_phase1_prompt_context(
            repo_root,
            date_id,
            xml_rel=rel,
            no_theme_selector=effective_no_theme,
        )
        if not ctx.get("ok"):
            return ctx
        out = {**ctx, **hooks}
        return out

    if stype == "plain_file":
        if source_text_file is None:
            return {
                "ok": False,
                "error": "missing_source_file",
                "message": "plain_file profile requires --source-text-file PATH.",
            }
        path = Path(source_text_file)
        if not path.is_file():
            return {
                "ok": False,
                "error": "source_not_found",
                "message": f"Source file not found: {path}",
            }
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": "read_error", "message": str(e)}
        if not text.strip():
            return {
                "ok": False,
                "error": "empty_source",
                "message": "Source file is empty or whitespace only.",
            }
        journal_date: str | None = None
        if len(date_id) == 8 and date_id.isdigit():
            journal_date = f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:8]}"
        try:
            rel_src = str(path.relative_to(repo_root)).replace("\\", "/")
        except ValueError:
            rel_src = str(path)
        return {
            "ok": True,
            "date_id": date_id,
            "journal_date": journal_date,
            "entry_text": text,
            "entry_author": None,
            "focus_topic": None,
            "editorial_notes": None,
            "theme_recommend_result": None,
            "is_blank_page": False,
            "xml_path": rel_src,
            **hooks,
        }

    return {
        "ok": False,
        "error": "unsupported_source",
        "message": f"Unknown source.type: {profile.source_type!r}",
    }
