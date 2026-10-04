#!/usr/bin/env python3
"""
Shared narration helpers: config loading, visual style selection, XML extraction,
JSON cleaning, prompt replacements, and schema validation.
Used by generate-narration-two-phase and (via stub) generate-narration.
"""

import json
import random
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

DEFAULT_NARRATION_CONFIG: dict[str, Any] = {
    "allow_stylization": True,
    "stylization_probability": 0.35,
    "visual_themes": [],
    "prompt_replacements": [],
    "video_prompt_replacements": [],
    "character_injection": {
        "enabled": True,
        "max_per_episode": 4,
        "max_per_character": 3,
    },
    "style_diversity": {
        "enabled": True,
        "recent_window": 6,
        "avoid_immediate_reuse": 2,
        "default_theme_probability_mass_boost": 0.0,
    },
    "episode_diversity": {
        "enabled": True,
        "recent_window": 5,
    },
    "dialogue_conversation_mode": {
        "enabled": False,
        "min_segments": 2,
        "min_lines_per_segment": 4,
    },
    # OpenAI TTS voice names per character id when narration_script[].dialogue is present.
    "voice_by_speaker": {},
    # FAL talking-head model (image + driven audio -> video). Used when narration_script[].visual_mode is talking_head.
    "fal_talking_head_model": "fal-ai/sadtalker",
    "fal_talking_head_fallback_model": "",
    # SadTalker only: FAL ``still_mode``. Keep True: False often yields uncanny whole-head drift vs. a static torso.
    "fal_sadtalker_still_mode": True,
    "fal_broll_engine": "wan",
    "fal_broll_i2v_engine": "kling-v3-standard",
    "fal_broll_t2v_engine": "wan",
    "fal_kling_broll_i2v_model": "fal-ai/kling-video/v3/standard/image-to-video",
    "fal_kling_broll_t2v_model": "fal-ai/kling-video/v3/standard/text-to-video",
    "fal_kling_talking_head_model": "fal-ai/kling-video/ai-avatar/v2/pro",
    "fal_kling_broll_min_seconds": 3,
    "fal_kling_broll_max_seconds": 15,
    "fal_wan_broll_max_seconds": 10,
}


def load_narration_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load narration config from JSON. Returns default config if path missing or invalid.

    Search order when config_path is not provided:
    1) config/narration_config.json
    2) pipeline/narration_config.json (legacy)
    3) <repo_root>/narration_config.json (legacy)
    """
    repo_root = Path(__file__).resolve().parent.parent
    pipeline_dir = Path(__file__).resolve().parent
    config_dir = repo_root / "config"
    if config_path is None:
        candidate_config = config_dir / "narration_config.json"
        candidate_legacy_pipeline = pipeline_dir / "narration_config.json"
        candidate_legacy_root = repo_root / "narration_config.json"
        if candidate_config.exists():
            config_path = candidate_config
        elif candidate_legacy_pipeline.exists():
            config_path = candidate_legacy_pipeline
        else:
            config_path = candidate_legacy_root
    if not config_path.exists():
        out = dict(DEFAULT_NARRATION_CONFIG)
        out["voice_by_speaker"] = dict(out.get("voice_by_speaker") or {})
        return out
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        vbs = data.get("voice_by_speaker")
        if not isinstance(vbs, dict):
            vbs = {}
        else:
            vbs = {
                str(k).strip(): str(v).strip()
                for k, v in vbs.items()
                if str(k).strip() and str(v).strip()
            }
        return {
            "allow_stylization": data.get(
                "allow_stylization", DEFAULT_NARRATION_CONFIG["allow_stylization"]
            ),
            "stylization_probability": float(
                data.get(
                    "stylization_probability", DEFAULT_NARRATION_CONFIG["stylization_probability"]
                )
            ),
            "visual_themes": data.get("visual_themes", []),
            "prompt_replacements": data.get("prompt_replacements", []),
            "video_prompt_replacements": data.get("video_prompt_replacements", []),
            "character_injection": data.get(
                "character_injection", DEFAULT_NARRATION_CONFIG["character_injection"]
            ),
            "style_diversity": data.get(
                "style_diversity", DEFAULT_NARRATION_CONFIG["style_diversity"]
            ),
            "episode_diversity": data.get(
                "episode_diversity", DEFAULT_NARRATION_CONFIG["episode_diversity"]
            ),
            "dialogue_conversation_mode": data.get(
                "dialogue_conversation_mode",
                DEFAULT_NARRATION_CONFIG["dialogue_conversation_mode"],
            ),
            "voice_by_speaker": vbs,
            "fal_talking_head_model": str(
                data.get("fal_talking_head_model")
                or DEFAULT_NARRATION_CONFIG["fal_talking_head_model"]
            ).strip(),
            "fal_talking_head_fallback_model": str(
                data.get(
                    "fal_talking_head_fallback_model",
                    DEFAULT_NARRATION_CONFIG["fal_talking_head_fallback_model"],
                )
                or ""
            ).strip(),
            "fal_sadtalker_still_mode": bool(
                data.get(
                    "fal_sadtalker_still_mode",
                    DEFAULT_NARRATION_CONFIG["fal_sadtalker_still_mode"],
                )
            ),
            "fal_conversation_scene_anchor": data.get("fal_conversation_scene_anchor"),
            "fal_scene_anchor_quality": data.get("fal_scene_anchor_quality"),
            "fal_broll_engine": str(
                data.get("fal_broll_engine") or DEFAULT_NARRATION_CONFIG["fal_broll_engine"]
            ).strip(),
            "fal_broll_i2v_engine": str(
                data.get("fal_broll_i2v_engine") or DEFAULT_NARRATION_CONFIG["fal_broll_i2v_engine"]
            ).strip(),
            "fal_broll_t2v_engine": str(
                data.get("fal_broll_t2v_engine") or DEFAULT_NARRATION_CONFIG["fal_broll_t2v_engine"]
            ).strip(),
            "fal_kling_broll_i2v_model": str(
                data.get("fal_kling_broll_i2v_model")
                or DEFAULT_NARRATION_CONFIG["fal_kling_broll_i2v_model"]
            ).strip(),
            "fal_kling_broll_t2v_model": str(
                data.get("fal_kling_broll_t2v_model")
                or DEFAULT_NARRATION_CONFIG["fal_kling_broll_t2v_model"]
            ).strip(),
            "fal_kling_talking_head_model": str(
                data.get("fal_kling_talking_head_model")
                or DEFAULT_NARRATION_CONFIG["fal_kling_talking_head_model"]
            ).strip(),
            "fal_kling_broll_min_seconds": int(
                data.get(
                    "fal_kling_broll_min_seconds",
                    DEFAULT_NARRATION_CONFIG["fal_kling_broll_min_seconds"],
                )
            ),
            "fal_kling_broll_max_seconds": int(
                data.get(
                    "fal_kling_broll_max_seconds",
                    DEFAULT_NARRATION_CONFIG["fal_kling_broll_max_seconds"],
                )
            ),
            "fal_wan_broll_max_seconds": int(
                data.get(
                    "fal_wan_broll_max_seconds",
                    DEFAULT_NARRATION_CONFIG["fal_wan_broll_max_seconds"],
                )
            ),
        }
    except (json.JSONDecodeError, OSError):
        out = dict(DEFAULT_NARRATION_CONFIG)
        out["voice_by_speaker"] = dict(out.get("voice_by_speaker") or {})
        return out


def _apply_default_theme_mass_boost(
    pool: list[dict[str, Any]],
    weights: list[float],
    boost: float,
) -> list[float]:
    """
    Add boost * sum(weights) to the weight of the theme marked default: true.
    Roughly shifts that much probability mass toward the default theme (e.g. 0.05 ≈ +5%).
    """
    if boost <= 0 or not pool or len(weights) != len(pool):
        return weights
    out = list(weights)
    s = float(sum(out))
    if s <= 0:
        return out
    for i, theme in enumerate(pool):
        if isinstance(theme, dict) and theme.get("default") is True:
            out[i] += boost * s
            break
    return out


def select_visual_style(
    config: dict[str, Any], recent_style_names: list[str] | None = None
) -> dict[str, str] | None:
    """Pick one visual style with randomness + optional short-term diversity pressure."""
    if not config.get("allow_stylization", True):
        return None
    if random.random() > config.get("stylization_probability", 0.35):
        return None
    styles = config.get("visual_themes") or []
    if not styles:
        return None
    diversity = config.get("style_diversity") or {}
    mass_boost = float(diversity.get("default_theme_probability_mass_boost", 0.0) or 0.0)

    if not diversity.get("enabled", True) or not recent_style_names:
        pool = list(styles)
        weights = [1.0] * len(pool)
        weights = _apply_default_theme_mass_boost(pool, weights, mass_boost)
        return random.choices(pool, weights=weights, k=1)[0]

    avoid_immediate_reuse = int(diversity.get("avoid_immediate_reuse", 2) or 0)
    blocked = set(
        n
        for n in (recent_style_names[: max(0, avoid_immediate_reuse)] or [])
        if isinstance(n, str) and n.strip()
    )
    pool = [s for s in styles if str((s or {}).get("name", "")).strip() not in blocked]
    if not pool:
        pool = list(styles)

    # Weighted random: styles seen less in the recent window are more likely.
    freqs: dict[str, int] = {}
    for n in recent_style_names:
        if not isinstance(n, str):
            continue
        name = n.strip()
        if not name:
            continue
        freqs[name] = freqs.get(name, 0) + 1
    weights: list[float] = []
    for s in pool:
        name = str((s or {}).get("name", "")).strip()
        weights.append(1.0 / (1 + freqs.get(name, 0)))
    weights = _apply_default_theme_mass_boost(pool, weights, mass_boost)
    return random.choices(pool, weights=weights, k=1)[0]


def recent_visual_style_names(
    narrations_dir: Path,
    *,
    current_date_id: str | None = None,
    limit: int = 6,
) -> list[str]:
    """Return most-recent visual style names from prior narration JSON files."""
    narrations_dir = Path(narrations_dir)
    if not narrations_dir.exists() or limit <= 0:
        return []
    items: list[tuple[str, Path]] = []
    for p in narrations_dir.glob("narration*.json"):
        m = re.match(r"narration(\d{8})\.json$", p.name)
        if not m:
            continue
        date_id = m.group(1)
        if current_date_id and date_id >= current_date_id:
            continue
        items.append((date_id, p))
    items.sort(key=lambda t: t[0], reverse=True)
    out: list[str] = []
    for _date_id, path in items:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        vs = data.get("visual_style") or {}
        name = (vs.get("name") or "").strip() if isinstance(vs, dict) else ""
        if not name:
            continue
        out.append(name)
        if len(out) >= limit:
            break
    return out


def get_default_visual_style(config: dict[str, Any]) -> dict[str, str] | None:
    """Return the theme marked \"default\": true in visual_themes, or None. Returns only name and description."""
    styles = config.get("visual_themes") or []
    for theme in styles:
        if isinstance(theme, dict) and theme.get("default") is True:
            name = theme.get("name")
            desc = theme.get("description")
            if name and isinstance(name, str):
                return {
                    "name": name,
                    "description": (desc or name) if isinstance(desc, str) else name,
                }
    return None


def _text_from_tei_el(el: ET.Element, ns: dict[str, str]) -> str:
    """
    Extract text from a TEI element. Inside <choice>, prefer <corr> or <reg>
    over <orig>/<sic> so journal spelling corrections (e.g. drewer -> Drouillard) appear in extracted text.
    """
    # TEI choice: use corrected/regularized form when present
    tag = el.tag
    if tag == "{" + ns["tei"] + "}choice" or tag == "choice":
        for preferred in ("corr", "reg"):
            child = el.find(f"tei:{preferred}", ns)
            if child is not None:
                return _text_from_tei_el(child, ns)
        for fallback in ("orig", "sic"):
            child = el.find(f"tei:{fallback}", ns)
            if child is not None:
                return _text_from_tei_el(child, ns)
        return ""
    parts = [el.text or ""]
    for child in el:
        parts.append(_text_from_tei_el(child, ns))
        parts.append(child.tail or "")
    return "".join(parts)


def extract_entry_text(xml_path: Path) -> str:
    """Extract entry paragraph text from TEI journal XML. Uses <corr>/<reg> inside <choice> over <orig>/<sic>."""
    text, _author = extract_entry_text_and_author(xml_path)
    return text


def _tei_sp_display_name(
    who: str,
    speaker_name: str,
    author_by_id: dict[str, str],
) -> str:
    """Resolve a short display label for one <sp> (used when multiple journal voices appear the same day)."""
    if who and who in author_by_id:
        return author_by_id[who].strip()
    if speaker_name.strip():
        return speaker_name.strip()
    if who:
        return who.strip()
    return ""


def iter_entry_labeled_paragraphs(
    xml_path: Path,
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """
    Extract (voice_label, paragraph_text) for each non-empty <p> under entry <sp> blocks.

    Uses the same label resolution as extract_entry_text_and_author (including speaker
    fallback when every <sp> shares one @who). Returns author_by_id from the TEI header.
    """
    ns = {"tei": "http://www.tei-c.org/ns/1.0"}
    tree = ET.parse(xml_path)
    root = tree.getroot()
    xml_ns = "{http://www.w3.org/XML/1998/namespace}"

    author_by_id: dict[str, str] = {}
    for a in root.findall(".//tei:sourceDesc//tei:author", ns):
        aid = (a.get(f"{xml_ns}id") or "").strip().lower()
        name = ("".join(a.itertext())).strip() if a is not None else ""
        if aid and name:
            author_by_id[aid] = name

    sp_nodes = root.findall(".//tei:div[@type='entry']//tei:sp", ns)
    raw_rows: list[tuple[str, str, list[str]]] = []
    for sp in sp_nodes:
        who = (sp.get("who") or "").strip().lstrip("#").lower()
        speaker_el = sp.find("tei:speaker", ns)
        speaker_name = ("".join(speaker_el.itertext())).strip() if speaker_el is not None else ""
        paras = sp.findall("tei:p", ns)
        chunk: list[str] = []
        for p in paras:
            text = _text_from_tei_el(p, ns).strip()
            if text:
                chunk.append(text)
        if chunk:
            raw_rows.append((who, speaker_name, chunk))

    prefer_speaker_label = False
    if len(raw_rows) >= 2:
        nonempty_whos = [w for w, _, _ in raw_rows if w]
        if nonempty_whos and len(set(nonempty_whos)) == 1:
            spk_distinct = {s.strip().casefold() for _, s, _ in raw_rows if s.strip()}
            prefer_speaker_label = len(spk_distinct) >= 2

    out: list[tuple[str, str]] = []
    for who, speaker_name, chunk in raw_rows:
        if prefer_speaker_label and speaker_name.strip():
            label = speaker_name.strip()
        else:
            label = _tei_sp_display_name(who, speaker_name, author_by_id)
        if not label:
            label = "Journal"
        for para in chunk:
            out.append((label, para))

    if not out:
        paras = root.findall(".//tei:div[@type='entry']//tei:p", ns)
        for p in paras:
            text = _text_from_tei_el(p, ns).strip()
            if text:
                out.append(("Journal", text))

    return out, author_by_id


def extract_entry_text_and_author(xml_path: Path) -> tuple[str, str | None]:
    """Extract entry paragraph text and best-effort journal author name from TEI XML.

    When the entry contains **multiple** distinct ``<sp>`` voices (by resolved display name),
    each voice's paragraphs are prefixed with ``[Display Name]`` so Phase 1 sees explicit
    attribution. If every ``<sp>`` repeats the same non-empty ``@who`` but ``<speaker>`` names
    differ (a known TEI inconsistency), labels fall back to those speaker names so the day
    is not treated as single-author. Single-voice days keep a flat paragraph run (no per-block labels).
    """
    labeled, author_by_id = iter_entry_labeled_paragraphs(xml_path)
    first_author: str | None = None
    if labeled:
        first_author = labeled[0][0]

    sp_blocks: list[tuple[str, list[str]]] = []
    if labeled:
        by_label: dict[str, list[str]] = {}
        for label, para in labeled:
            by_label.setdefault(label, []).append(para)
        sp_blocks = [(lbl, paras) for lbl, paras in by_label.items()]

    if sp_blocks:
        order_unique: list[str] = []
        seen_lower: set[str] = set()
        for label, _ in sp_blocks:
            key = label.strip().lower()
            if key not in seen_lower:
                seen_lower.add(key)
                order_unique.append(label)

        if len(order_unique) <= 1:
            flat: list[str] = []
            for _lbl, txts in sp_blocks:
                flat.extend(txts)
            author_name = order_unique[0] if order_unique else first_author
            return "\n\n".join(flat), author_name

        chunks_out: list[str] = []
        for label, txts in sp_blocks:
            body = "\n\n".join(txts)
            chunks_out.append(f"[{label}]\n\n{body}")
        multi_line = "Multiple journal authors this date: " + "; ".join(order_unique)
        return "\n\n".join(chunks_out), multi_line

    return "", first_author


def extract_entry_linked_notes(xml_path: Path) -> str:
    """
    Collect TEI <back><note> texts referenced from the day’s entry via <ref target="..."/>.

    Moulton-style journals place scholarly commentary in footnotes; the entry body alone
    is often terse. Linked notes are returned as plain text for Phase 1 context.
    """
    ns = {"tei": "http://www.tei-c.org/ns/1.0"}
    xml_ns = "{http://www.w3.org/XML/1998/namespace}"
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except (OSError, ET.ParseError):
        return ""

    notes_by_id: dict[str, ET.Element] = {}
    for note in root.findall(".//tei:back//tei:note", ns):
        nid = (note.get(f"{xml_ns}id") or "").strip()
        if nid:
            notes_by_id[nid] = note

    ordered_ids: list[str] = []
    seen: set[str] = set()
    for div in root.findall(".//tei:div[@type='entry']", ns):
        for ref in div.findall(".//tei:ref", ns):
            raw = (ref.get("target") or "").strip()
            if not raw:
                continue
            tid = raw.lstrip("#").strip()
            if not tid or tid not in notes_by_id or tid in seen:
                continue
            seen.add(tid)
            ordered_ids.append(tid)

    if not ordered_ids:
        return ""

    blocks: list[str] = []
    for tid in ordered_ids:
        el = notes_by_id[tid]
        n_attr = (el.get("n") or "").strip()
        label = f"Note {n_attr}" if n_attr else f"Note ({tid})"
        body = _text_from_tei_el(el, ns).strip()
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            blocks.append(f"{label}: {body}")
    return "\n\n".join(blocks)


def clean_json_reply(reply: str) -> str:
    """Remove Markdown fences, preamble prose, and surrounding whitespace from model JSON."""
    reply = reply.strip()
    if "```" in reply:
        fence = reply.find("```json")
        if fence == -1:
            fence = reply.find("```")
        if fence != -1:
            block = reply[fence:]
            lines = block.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            reply = "\n".join(lines).strip()
    if not reply.startswith("{"):
        first = reply.find("{")
        last = reply.rfind("}")
        if first != -1 and last > first:
            reply = reply[first : last + 1]
    return reply.strip()


def apply_prompt_replacements(
    parsed: dict,
    prompt_replacements: list[dict[str, Any]],
    video_prompt_replacements: list[dict[str, Any]] | None = None,
    *,
    video_prompt_cues_path: Path | None = None,
) -> dict[str, list[str]]:
    """Apply find/replace rules and conditional video cues before saving. Modifies parsed in place.

    Returns segment-index → cue ids from ``apply_video_prompt_cues`` (empty when disabled or no matches).
    """

    def apply_to_text(text: str, rules: list[dict[str, Any]]) -> str:
        if not text or not isinstance(text, str) or not rules:
            return text
        for rule in rules:
            find = rule.get("find") or ""
            replace = rule.get("replace") or ""
            if find:
                text = text.replace(find, replace)
        return text

    if prompt_replacements:
        if parsed.get("title"):
            parsed["title"] = apply_to_text(parsed["title"], prompt_replacements)
        for seg in parsed.get("narration_script") or []:
            for key in ("stage_direction", "narration", "video_prompt"):
                if key in seg and seg[key]:
                    seg[key] = apply_to_text(seg[key], prompt_replacements)
            rows = seg.get("dialogue")
            if isinstance(rows, list) and prompt_replacements:
                for row in rows:
                    if isinstance(row, dict) and row.get("text"):
                        t = row["text"]
                        if isinstance(t, str) and t:
                            row["text"] = apply_to_text(t, prompt_replacements)
    video_rules = video_prompt_replacements or []
    if video_rules:
        for seg in parsed.get("narration_script") or []:
            if "video_prompt" in seg and seg["video_prompt"]:
                seg["video_prompt"] = apply_to_text(seg["video_prompt"], video_rules)

    from pipeline.video_prompt_cues import apply_video_prompt_cues

    return apply_video_prompt_cues(parsed, config_path=video_prompt_cues_path)


def validate_narration_schema(data: dict) -> None:
    """Raise ValueError if data does not match the pipeline narration schema."""
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object")
    if "narration_script" not in data:
        raise ValueError("Missing 'narration_script' key")
    if "title" in data and not isinstance(data["title"], str):
        raise ValueError("'title' must be a string")
    if "title" in data and isinstance(data["title"], str) and not data["title"].strip():
        raise ValueError("'title' must be non-empty when present")
    if "visual_style" in data:
        vs = data["visual_style"]
        if vs is not None and (
            not isinstance(vs, dict)
            or not isinstance(vs.get("name"), str)
            or not isinstance(vs.get("description"), str)
        ):
            raise ValueError(
                "'visual_style' must be null or an object with string 'name' and 'description'"
            )
    if "scene_spine" in data and not isinstance(data["scene_spine"], dict):
        raise ValueError("'scene_spine' must be an object when present")
    if not isinstance(data["narration_script"], list):
        raise ValueError("'narration_script' must be an array")
    if "open_questions" in data:
        oq = data["open_questions"]
        if not isinstance(oq, list):
            raise ValueError("'open_questions' must be an array when present")
        for j, item in enumerate(oq):
            if not isinstance(item, str):
                raise ValueError(f"'open_questions[{j}]' must be a string")
    if "editor_notes" in data and not isinstance(data["editor_notes"], str):
        raise ValueError("'editor_notes' must be a string when present")
    if "dialogue_mode" in data and not isinstance(data["dialogue_mode"], bool):
        raise ValueError("'dialogue_mode' must be a boolean when present")
    if "long_conversation_mode" in data and not isinstance(data["long_conversation_mode"], bool):
        raise ValueError("'long_conversation_mode' must be a boolean when present")
    for i, segment in enumerate(data["narration_script"]):
        if not isinstance(segment, dict):
            raise ValueError(f"Segment {i} is not an object")
        from pipeline.narration_visual_mode import VISUAL_MODE_TALKING_HEAD, normalize_visual_mode

        vm = normalize_visual_mode(segment)
        for key in ("stage_direction", "narration"):
            if key not in segment:
                raise ValueError(f"Missing '{key}' in segment {i}")
            if not isinstance(segment[key], str):
                raise ValueError(f"'{key}' in segment {i} must be a string")
        if vm == VISUAL_MODE_TALKING_HEAD:
            thp = segment.get("talking_head_prompt")
            if not isinstance(thp, str) or not thp.strip():
                raise ValueError(f"Missing or empty 'talking_head_prompt' in segment {i}")
        else:
            if "video_prompt" not in segment:
                raise ValueError(f"Missing 'video_prompt' in segment {i}")
            if not isinstance(segment["video_prompt"], str):
                raise ValueError(f"'video_prompt' in segment {i} must be a string")
        if "dialogue" in segment and segment["dialogue"] is not None:
            dlg = segment["dialogue"]
            if not isinstance(dlg, list):
                raise ValueError(f"'dialogue' in segment {i} must be an array when present")
            for j, line in enumerate(dlg):
                if not isinstance(line, dict):
                    raise ValueError(f"'dialogue[{j}]' in segment {i} must be an object")
                sid = line.get("speaker_id")
                txt = line.get("text")
                if not isinstance(sid, str) or not sid.strip():
                    raise ValueError(
                        f"'dialogue[{j}].speaker_id' in segment {i} must be a non-empty string"
                    )
                if not isinstance(txt, str) or not txt.strip():
                    raise ValueError(
                        f"'dialogue[{j}].text' in segment {i} must be a non-empty string"
                    )
