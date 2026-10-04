"""Load show / project profile JSON (sources, prompt pack, output prefix, feature flags)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Phase1UserPromptFields:
    use_tension_arcs: bool
    use_expedition_user_notes: bool
    use_journal_author_line: bool
    use_macro_event_editorial: bool
    focus_topic_compares_1803: bool
    user_opening_template: str
    source_body_heading: str
    tei_notes_heading: str


@dataclass(frozen=True)
class Phase2PromptFields:
    focus_expedition_era: bool
    historical_only_visuals: bool
    seasonal_ambient_from_date_id: bool


@dataclass(frozen=True)
class LongDocumentHooks:
    """Reserved for multi-chunk runs; ignored for single-artifact episodes."""

    parent_document_id: str | None
    chunk_index: int | None
    chunk_note: str


@dataclass(frozen=True)
class PipelineProfile:
    id: str
    label: str
    prompt_pack: str
    output_prefix: str
    source_type: str
    source_xml_dir: str
    phase1_user: Phase1UserPromptFields
    phase2: Phase2PromptFields
    use_theme_engine: bool
    use_character_hints: bool
    long_document: LongDocumentHooks


def _dget(d: dict[str, Any], key: str, default: Any) -> Any:
    v = d.get(key, default)
    return default if v is None else v


def load_pipeline_profile(path: Path) -> PipelineProfile:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Profile must be a JSON object: {path}")
    src = raw.get("source") or {}
    if not isinstance(src, dict):
        src = {}
    p1 = raw.get("phase1_user_prompt") or {}
    if not isinstance(p1, dict):
        p1 = {}
    p2 = raw.get("phase2") or {}
    if not isinstance(p2, dict):
        p2 = {}
    feat = raw.get("features") or {}
    if not isinstance(feat, dict):
        feat = {}
    ld = raw.get("long_document") or {}
    if not isinstance(ld, dict):
        ld = {}
    pid = str(raw.get("id") or Path(path).stem).strip()
    phase1_user = Phase1UserPromptFields(
        use_tension_arcs=bool(_dget(p1, "use_tension_arcs", True)),
        use_expedition_user_notes=bool(_dget(p1, "use_expedition_user_notes", True)),
        use_journal_author_line=bool(_dget(p1, "use_journal_author_line", True)),
        use_macro_event_editorial=bool(_dget(p1, "use_macro_event_editorial", True)),
        focus_topic_compares_1803=bool(_dget(p1, "focus_topic_compares_1803", True)),
        user_opening_template=str(
            _dget(
                p1,
                "user_opening_template",
                "Generate the narration JSON for journal date_id {date_id}.",
            )
        ),
        source_body_heading=str(_dget(p1, "source_body_heading", "JOURNAL ENTRY:")),
        tei_notes_heading=str(
            _dget(
                p1,
                "tei_notes_heading",
                "TEI EDITORIAL NOTES (footnotes linked from this entry; use for historical color, third-person):",
            )
        ),
    )
    phase2 = Phase2PromptFields(
        focus_expedition_era=bool(_dget(p2, "focus_expedition_era", True)),
        historical_only_visuals=bool(_dget(p2, "historical_only_visuals", True)),
        seasonal_ambient_from_date_id=bool(_dget(p2, "seasonal_ambient_from_date_id", True)),
    )
    parent = ld.get("parent_document_id")
    chunk_i = ld.get("chunk_index")
    long_doc = LongDocumentHooks(
        parent_document_id=str(parent).strip() if parent not in (None, "") else None,
        chunk_index=int(chunk_i) if isinstance(chunk_i, int) else None,
        chunk_note=str(ld.get("chunk_note") or ""),
    )
    return PipelineProfile(
        id=pid,
        label=str(raw.get("label") or pid).strip(),
        prompt_pack=str(raw.get("prompt_pack") or "lewis_clark").strip(),
        output_prefix=str(raw.get("output_prefix") or "lewis_clark").strip() or "lewis_clark",
        source_type=str(src.get("type") or "tei_journal").strip(),
        source_xml_dir=str(src.get("xml_dir") or "journal-entries").strip(),
        phase1_user=phase1_user,
        phase2=phase2,
        use_theme_engine=bool(_dget(feat, "use_theme_engine", True)),
        use_character_hints=bool(_dget(feat, "use_character_hints", True)),
        long_document=long_doc,
    )


def default_profile_path(repo_root: Path) -> Path:
    return repo_root / "config" / "profiles" / "lewis_clark.json"


def lewis_clark_phase1_user_defaults() -> Phase1UserPromptFields:
    """Explicit defaults matching config/profiles/lewis_clark.json (for callers without a profile)."""
    return Phase1UserPromptFields(
        use_tension_arcs=True,
        use_expedition_user_notes=True,
        use_journal_author_line=True,
        use_macro_event_editorial=True,
        focus_topic_compares_1803=True,
        user_opening_template="Generate the narration JSON for journal date_id {date_id}.",
        source_body_heading="JOURNAL ENTRY:",
        tei_notes_heading=(
            "TEI EDITORIAL NOTES (footnotes linked from this entry; use for historical color, third-person):"
        ),
    )


def phase2_defaults_expedition() -> Phase2PromptFields:
    return Phase2PromptFields(
        focus_expedition_era=True,
        historical_only_visuals=True,
        seasonal_ambient_from_date_id=True,
    )
