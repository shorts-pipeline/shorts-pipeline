#!/usr/bin/env python3
"""
Mine CDRH TEI journal XML for Lewis vs Clark voice statistics (dialogue_profile exploration).

Reads journal-entries/*.xml, attributes paragraphs to lewis/clark via TEI labels, and writes
a JSON + Markdown report under config/dialogue_profile_mining/ (not wired into Phase 1 polish).

Usage (from repo root):
  python scripts/mine_journal_dialogue_profiles.py
  python scripts/mine_journal_dialogue_profiles.py --journal-dir journal-entries --out-dir config/dialogue_profile_mining
  python scripts/mine_journal_dialogue_profiles.py --authors lewis,clark --top-n 50
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(_REPO_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_REPO_FOR_IMPORT))

from pipeline.narration_common import iter_entry_labeled_paragraphs

# Expedition / function-word stoplist (extend b_roll_mine style).
_STOPWORDS = frozenset(
    """
    a an the and or but if in on at to for of as is are was were be been being
    with from by it its this that these those into over under upon through
    about than then so not no yes very more most some any all each both few
    such same other another one two first second their they them his her she
    he him we you our your my me i
    day days morning evening night today yesterday
    camp party men man river water wind fair cloudy rain
    set out continued passed mile miles course
    """.split()
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _normalize_tokens(text: str) -> list[str]:
    t = text.lower()
    t = re.sub(r"[^a-z0-9\s]+", " ", t)
    return [w for w in t.split() if w and len(w) > 1 and w not in _STOPWORDS]


def _bigrams(tokens: list[str]) -> list[str]:
    if len(tokens) < 2:
        return []
    return [f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)]


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _sentence_stats(paragraphs: list[str]) -> dict[str, float]:
    lengths: list[int] = []
    semicolon_count = 0
    amp_count = 0
    for para in paragraphs:
        semicolon_count += para.count(";")
        amp_count += para.count("&")
        for sent in _split_sentences(para):
            lengths.append(len(_normalize_tokens(sent)))
    if not lengths:
        return {
            "sentence_count": 0,
            "mean_tokens_per_sentence": 0.0,
            "median_tokens_per_sentence": 0.0,
            "semicolons_per_1k_words": 0.0,
            "ampersands_per_1k_words": 0.0,
        }
    lengths.sort()
    mid = len(lengths) // 2
    median = float(lengths[mid]) if len(lengths) % 2 else (lengths[mid - 1] + lengths[mid]) / 2.0
    word_count = sum(lengths)
    return {
        "sentence_count": len(lengths),
        "mean_tokens_per_sentence": round(sum(lengths) / len(lengths), 2),
        "median_tokens_per_sentence": round(median, 2),
        "semicolons_per_1k_words": round(1000.0 * semicolon_count / max(word_count, 1), 2),
        "ampersands_per_1k_words": round(1000.0 * amp_count / max(word_count, 1), 2),
    }


def _label_to_character_id(label: str, author_id_map: dict[str, str]) -> str | None:
    """Map TEI voice label or header author name to lewis | clark | None."""
    raw = label.strip()
    if not raw:
        return None
    low = raw.casefold()
    if low in author_id_map:
        return author_id_map[low]
    if "meriwether" in low and "lewis" in low:
        return "lewis"
    if "william" in low and "clark" in low:
        return "clark"
    if low == "lewis" or low.endswith(" lewis"):
        return "lewis" if "clark" not in low else None
    if low == "clark" or low.startswith("clark "):
        return "clark" if "lewis" not in low else None
    if "clark" in low and "lewis" not in low and "meriwether" not in low:
        return "clark"
    if "lewis" in low and "clark" not in low:
        return "lewis"
    if "ordway" in low or low == "odway":
        return "ordway"
    if "gass" in low:
        return "gass"
    return None


def _register_author_name(name: str, author_id_map: dict[str, str]) -> None:
    cid = _label_to_character_id(name, author_id_map)
    if cid:
        author_id_map[name.strip().casefold()] = cid


def _load_curated_habitual_words(repo: Path, character_ids: list[str]) -> dict[str, list[str]]:
    path = repo / "config" / "narration_characters.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    people = data.get("people") or []
    out: dict[str, list[str]] = {}
    for p in people:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "").strip().lower()
        if pid not in character_ids:
            continue
        dp = p.get("dialogue_profile") or p.get("narration_voice")
        if not isinstance(dp, dict):
            continue
        hw = dp.get("habitual_words") or dp.get("lexical_tics") or []
        words = [str(w).strip().lower() for w in hw if str(w).strip()]
        if words:
            out[pid] = words
    return out


def _contrastive_terms(
    counts_a: Counter[str],
    total_a: int,
    counts_b: Counter[str],
    total_b: int,
    *,
    min_count: int = 5,
    top_n: int = 40,
) -> list[dict[str, Any]]:
    """Terms comparatively more common in corpus A than B (log-odds ratio)."""
    vocab = set(counts_a) | set(counts_b)
    scored: list[tuple[float, str, int, int]] = []
    for term in vocab:
        ca = counts_a.get(term, 0)
        cb = counts_b.get(term, 0)
        if ca < min_count:
            continue
        pa = (ca + 0.5) / (total_a + 1.0)
        pb = (cb + 0.5) / (total_b + 1.0)
        score = math.log(pa / pb)
        scored.append((score, term, ca, cb))
    scored.sort(key=lambda t: (-t[0], -t[2]))
    return [
        {"term": term, "log_odds_vs_other": round(score, 3), "count": ca, "other_count": cb}
        for score, term, ca, cb in scored[:top_n]
    ]


def _overlap_report(
    mined_top: list[str],
    curated: list[str],
) -> dict[str, Any]:
    mined_set = {t.lower() for t in mined_top}
    curated_set = {c.lower() for c in curated}
    return {
        "curated_habitual_words": curated,
        "mined_distinctive_terms": mined_top,
        "in_both": sorted(mined_set & curated_set),
        "only_in_curated": sorted(curated_set - mined_set),
        "only_in_mined": sorted(mined_set - curated_set),
    }


def _spoken_dialogue_hints(character_id: str, sentence_stats: dict[str, float]) -> list[str]:
    if character_id == "lewis":
        return [
            "Spoken polish: favor short observational clauses (instruments, species, terrain)—not long past-tense journal chains.",
            "Sprinkle natural-history diction sparingly; avoid copying dense list-of-specimens rhythm into every line.",
            f"Journal sentence shape (median ~{sentence_stats.get('median_tokens_per_sentence', 0)} tokens/sentence): can shorten for in-scene present tense.",
        ]
    if character_id == "clark":
        return [
            "Spoken polish: favor practical movement and river/camp logistics—mileage and disposition as fragments, not surveyor field notes.",
            "Keep Kentucky directness; light journal spelling flavor only if the draft already supports it.",
            f"Journal sentence shape (median ~{sentence_stats.get('median_tokens_per_sentence', 0)} tokens/sentence): often shorter than Lewis—use that for punchy orders.",
        ]
    if character_id == "ordway":
        return [
            "Spoken polish: sergeant-journal register—guard, rations, party routine, orderly camp business.",
            f"Journal sentence shape (median ~{sentence_stats.get('median_tokens_per_sentence', 0)} tokens/sentence).",
        ]
    if character_id == "gass":
        return [
            "Spoken polish: carpenter/builder and sergeant—timber, tools, construction; plain Irish-tinged cadence in longer lines.",
            f"Journal sentence shape (median ~{sentence_stats.get('median_tokens_per_sentence', 0)} tokens/sentence).",
        ]
    return []


def mine_corpus(
    journal_dir: Path,
    character_ids: list[str],
    *,
    top_n: int = 50,
    min_term_count: int = 5,
) -> dict[str, Any]:
    xml_files = sorted(journal_dir.glob("*.xml"))
    unigrams: dict[str, Counter[str]] = {c: Counter() for c in character_ids}
    bigrams: dict[str, Counter[str]] = {c: Counter() for c in character_ids}
    paragraphs_by: dict[str, list[str]] = {c: [] for c in character_ids}
    other_paragraphs: list[str] = []

    files_parsed = 0
    files_with_target = 0
    label_counts: Counter[str] = Counter()
    unmapped_labels: Counter[str] = Counter()
    header_author_ids: Counter[str] = Counter()
    author_name_registry: dict[str, str] = {}

    for path in xml_files:
        try:
            labeled, header_map = iter_entry_labeled_paragraphs(path)
        except Exception:
            continue
        files_parsed += 1
        for aid in header_map:
            header_author_ids[aid.lower()] += 1
            _register_author_name(header_map[aid], author_name_registry)
        day_has_target = False
        for label, para in labeled:
            label_counts[label] += 1
            cid = _label_to_character_id(label, author_name_registry)
            if cid not in character_ids:
                unmapped_labels[label] += 1
                other_paragraphs.append(para)
                continue
            day_has_target = True
            paragraphs_by[cid].append(para)
            toks = _normalize_tokens(para)
            unigrams[cid].update(toks)
            bigrams[cid].update(_bigrams(toks))
        if day_has_target:
            files_with_target += 1

    totals = {c: sum(unigrams[c].values()) for c in character_ids}
    report_authors: dict[str, Any] = {}
    for cid in character_ids:
        if len(character_ids) == 2:
            other_id = character_ids[1] if cid == character_ids[0] else character_ids[0]
        else:
            other_id = character_ids[0]
        pooled_other = Counter()
        other_total = 0
        for oid in character_ids:
            if oid == cid:
                continue
            pooled_other.update(unigrams[oid])
            other_total += totals[oid]
        report_authors[cid] = {
            "token_count": totals[cid],
            "paragraph_count": len(paragraphs_by[cid]),
            "sentence_stats": _sentence_stats(paragraphs_by[cid]),
            "top_unigrams": [{"term": t, "count": c} for t, c in unigrams[cid].most_common(top_n)],
            "top_bigrams": [{"term": t, "count": c} for t, c in bigrams[cid].most_common(top_n)],
            "distinctive_vs_other": _contrastive_terms(
                unigrams[cid],
                totals[cid],
                pooled_other if len(character_ids) != 2 else unigrams[other_id],
                other_total if len(character_ids) != 2 else totals[other_id],
                min_count=min_term_count,
                top_n=top_n,
            ),
            "spoken_dialogue_hints": _spoken_dialogue_hints(
                cid, _sentence_stats(paragraphs_by[cid])
            ),
        }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "journal_dir": str(journal_dir),
        "corpus_coverage": {
            "xml_files_found": len(xml_files),
            "xml_files_parsed": files_parsed,
            "files_with_lewis_or_clark_paragraphs": files_with_target,
            "paragraphs_lewis": len(paragraphs_by.get("lewis", [])),
            "paragraphs_clark": len(paragraphs_by.get("clark", [])),
            "paragraphs_unmapped": len(other_paragraphs),
            "token_count_lewis": totals.get("lewis", 0),
            "token_count_clark": totals.get("clark", 0),
            "token_count_unmapped": sum(len(_normalize_tokens(p)) for p in other_paragraphs),
        },
        "attribution_notes": [
            "Labels follow pipeline TEI rules (author header + <sp who> / <speaker> fallback).",
            "Some days repeat one @who across different <speaker> names; speaker labels are used when detected.",
            "Clark's corpus is often larger than Lewis's on the outbound journal leg.",
            "Mined diction is written journal prose—adapt for present-tense spoken dialogue, do not paste verbatim.",
        ],
        "tei_author_ids_seen": dict(header_author_ids.most_common(30)),
        "voice_labels_seen": dict(label_counts.most_common(40)),
        "unmapped_voice_labels_top": dict(unmapped_labels.most_common(25)),
        "authors": report_authors,
    }


def render_markdown(report: dict[str, Any], curated: dict[str, list[str]]) -> str:
    cov = report.get("corpus_coverage") or {}
    lines = [
        "# Lewis & Clark journal voice mining report",
        "",
        f"Generated: {report.get('generated_at', '')}",
        f"Journal dir: `{report.get('journal_dir', '')}`",
        "",
        "## Corpus coverage",
        "",
        f"- XML files found: **{cov.get('xml_files_found', 0)}**",
        f"- Parsed: **{cov.get('xml_files_parsed', 0)}**",
        f"- Files with Lewis or Clark paragraphs: **{cov.get('files_with_lewis_or_clark_paragraphs', 0)}**",
        f"- Lewis tokens: **{cov.get('token_count_lewis', 0)}** ({cov.get('paragraphs_lewis', 0)} paragraphs)",
        f"- Clark tokens: **{cov.get('token_count_clark', 0)}** ({cov.get('paragraphs_clark', 0)} paragraphs)",
        f"- Unmapped paragraphs: **{cov.get('paragraphs_unmapped', 0)}**",
        "",
        "## Attribution caveats",
        "",
    ]
    for note in report.get("attribution_notes") or []:
        lines.append(f"- {note}")
    lines.extend(["", "## Comparison to curated dialogue_profile", ""])

    authors = report.get("authors") or {}
    for cid in ("lewis", "clark"):
        block = authors.get(cid) or {}
        overlap = _overlap_report(
            [x["term"] for x in (block.get("distinctive_vs_other") or [])[:25]],
            curated.get(cid, []),
        )
        name = "Meriwether Lewis" if cid == "lewis" else "William Clark"
        lines.append(f"### {name} (`{cid}`)")
        lines.append("")
        lines.append(f"- In both curated and mined: `{', '.join(overlap['in_both']) or '(none)'}`")
        lines.append(
            f"- Only in curated `habitual_words`: `{', '.join(overlap['only_in_curated']) or '(none)'}`"
        )
        lines.append(
            f"- Only in mined distinctive (top): `{', '.join(overlap['only_in_mined'][:20]) or '(none)'}`"
        )
        lines.append("")
        lines.append("**Spoken dialogue hints (from mining):**")
        for hint in block.get("spoken_dialogue_hints") or []:
            lines.append(f"- {hint}")
        lines.append("")
        ss = block.get("sentence_stats") or {}
        lines.append(
            f"**Sentence shape:** median {ss.get('median_tokens_per_sentence', 0)} tokens/sentence; "
            f"mean {ss.get('mean_tokens_per_sentence', 0)}; "
            f"{ss.get('sentence_count', 0)} sentences sampled."
        )
        lines.append("")
        lines.append("**Distinctive terms vs other captain (log-odds, top 15):**")
        for row in (block.get("distinctive_vs_other") or [])[:15]:
            lines.append(
                f"- `{row['term']}` (count {row['count']}, other {row['other_count']}, log-odds {row['log_odds_vs_other']})"
            )
        lines.append("")

    lines.append("## Next steps")
    lines.append("")
    lines.append(
        "If distinctive terms look useful, curate ~15–25 into `dialogue_profile.habitual_words` "
        "in `config/narration_characters.json`—do not inject the full frequency tables into the polish pass."
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mine journal TEI for Lewis/Clark dialogue_profile exploration."
    )
    parser.add_argument(
        "--journal-dir",
        type=Path,
        default=_repo_root() / "journal-entries",
        help="Directory of YYYY-MM-DD.xml TEI files",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_repo_root() / "config" / "dialogue_profile_mining",
        help="Output directory for JSON and Markdown reports",
    )
    parser.add_argument(
        "--authors",
        default="lewis,clark",
        help="Comma-separated character ids to mine (default: lewis,clark)",
    )
    parser.add_argument(
        "--top-n", type=int, default=50, help="Top unigrams/bigrams/distinctive terms per author"
    )
    parser.add_argument(
        "--min-term-count",
        type=int,
        default=5,
        help="Minimum token count for distinctive-term ranking",
    )
    parser.add_argument(
        "--report-stem",
        default="lewis_clark_journal_voice_report",
        help="Output basename (writes <stem>.json and <stem>.md)",
    )
    args = parser.parse_args()

    journal_dir: Path = args.journal_dir
    out_dir: Path = args.out_dir
    character_ids = [a.strip().lower() for a in args.authors.split(",") if a.strip()]
    if not character_ids:
        print("[ERROR] --authors must list at least one id", flush=True)
        return 2

    if not journal_dir.is_dir():
        print(f"[ERROR] Journal dir not found: {journal_dir}", flush=True)
        print("Fetch XML with: python scripts/scrape-journal-entries.py YYYY-MM-DD", flush=True)
        return 2

    report = mine_corpus(
        journal_dir,
        character_ids,
        top_n=args.top_n,
        min_term_count=args.min_term_count,
    )
    curated = _load_curated_habitual_words(_repo_root(), character_ids)
    for cid in character_ids:
        mined_terms = [
            x["term"]
            for x in (report.get("authors", {}).get(cid, {}).get("distinctive_vs_other") or [])[:25]
        ]
        report.setdefault("comparison_to_curated", {})[cid] = _overlap_report(
            mined_terms, curated.get(cid, [])
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = (args.report_stem or "lewis_clark_journal_voice_report").strip()
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report, curated), encoding="utf-8")

    cov = report.get("corpus_coverage") or {}
    print(f"[OK] Wrote {json_path}", flush=True)
    print(f"[OK] Wrote {md_path}", flush=True)
    print(
        f"     Lewis tokens={cov.get('token_count_lewis', 0)} "
        f"Clark tokens={cov.get('token_count_clark', 0)} "
        f"files={cov.get('xml_files_parsed', 0)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
