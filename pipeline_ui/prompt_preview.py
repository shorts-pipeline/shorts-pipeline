"""Phase 1 prompt-preview payload builder for pipeline_ui (Options tab "preview prompt").

Split out of ``server.py`` (see ``ai-plans/pipeline-ui-server-split.md``, step 3).
"""

from __future__ import annotations

import json
from typing import Any

from pipeline.narration_characters.voice_prompt import dialogue_profile_prompt_section
from pipeline.narration_common import load_narration_config
from pipeline.narration_phase1 import build_phase1_system_prompt, build_phase1_user_prompt
from pipeline.narration_phase1_dialogue import (
    build_phase1_dialogue_system_prompt,
    build_phase1_dialogue_user_prompt,
)
from pipeline.phase1_prompt_prepare import prepare_phase1_prompt_context
from pipeline.recent_episode_diversity import build_episode_diversity_bundle
from pipeline_ui.paths import _journal_date_to_date_id, _safe_journal_date
from pipeline_ui.runtime import srv as _srv


def phase1_prompt_preview_payload(
    journal_date: str,
    skip_automatic_focus_topic: bool,
    dialogue: bool = False,
    long_conversation: bool = False,
) -> dict[str, Any]:
    """JSON for UI: system + user strings sent to Phase 1 (same as generate-narration-two-phase)."""
    repo_root = _srv()._REPO_ROOT
    safe = _safe_journal_date(journal_date)
    if not safe:
        return {
            "ok": False,
            "error": "invalid_date",
            "message": "Use YYYY-MM-DD within expedition years.",
        }
    date_id = _journal_date_to_date_id(safe)
    if not date_id:
        return {"ok": False, "error": "invalid_date", "message": "Could not derive date_id."}
    ctx = prepare_phase1_prompt_context(
        repo_root,
        date_id,
        no_theme_selector=skip_automatic_focus_topic,
    )
    if not ctx.get("ok"):
        return ctx
    focus_topic = ctx.get("focus_topic")
    lc_eff = bool(long_conversation) and not bool(focus_topic)
    dialogue_effective = (bool(dialogue) or lc_eff) and not bool(focus_topic)
    if lc_eff:
        prompt_pack = "lewis_clark_long_conversation"
    elif dialogue_effective:
        prompt_pack = "lewis_clark_dialogue"
    else:
        prompt_pack = "lewis_clark"
    narr_dir = repo_root / "narrations"
    cfg = load_narration_config()
    ediv_bundle = build_episode_diversity_bundle(
        repo_root,
        narr_dir,
        date_id,
        prompt_pack,
        cfg,
    )
    recent_digest = str(ediv_bundle.get("digest") or "")
    system = build_phase1_system_prompt(repo_root=repo_root, prompt_pack=prompt_pack)
    user = build_phase1_user_prompt(
        ctx["entry_text"],
        date_id,
        entry_author=ctx.get("entry_author"),
        focus_topic=focus_topic,
        editorial_notes=ctx.get("editorial_notes"),
        dialogue_mode=dialogue_effective,
        prompt_pack=prompt_pack,
        recent_episodes_digest=recent_digest or None,
    )
    pretty = "=== SYSTEM ===\n\n" + system + "\n\n=== USER ===\n\n" + user + "\n"
    out: dict[str, Any] = {
        "ok": True,
        "date_id": date_id,
        "journal_date": safe,
        "system": system,
        "user": user,
        "pretty": pretty,
        "focus_topic": focus_topic,
        "dialogue_suppressed_for_focus_topic": bool(dialogue) and bool(focus_topic),
        "long_conversation_suppressed_for_focus_topic": bool(long_conversation)
        and bool(focus_topic),
        "long_conversation_effective": lc_eff,
        "dialogue_effective": dialogue_effective,
        "xml_path": ctx.get("xml_path"),
        "episode_diversity": ediv_bundle,
    }
    if dialogue_effective:
        d_sys = build_phase1_dialogue_system_prompt(repo_root=repo_root, prompt_pack=prompt_pack)
        out["phase1_dialogue_system"] = d_sys
        d_user = ""
        user_err: str | None = None
        voice_sidecar = repo_root / "narrations" / f"narration{date_id}_voice.json"
        if voice_sidecar.is_file():
            try:
                phase1_body = json.loads(voice_sidecar.read_text(encoding="utf-8"))
                d_user = build_phase1_dialogue_user_prompt(phase1_body, date_id)
                out["phase1_dialogue_user_source"] = f"narrations/narration{date_id}_voice.json"
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                user_err = str(exc)
                out["phase1_dialogue_user_error"] = user_err
        if not d_user:
            if user_err:
                d_user = f"(Could not build phase1-dialogue user preview: {user_err})"
            else:
                vb = dialogue_profile_prompt_section()
                d_user = (
                    f"No narrations/narration{date_id}_voice.json yet. Run two-phase narration for this date to "
                    "see the full user message (Phase 1 JSON + optional journal-voice cues).\n\n"
                    "Optional block appended to that user message when character cues exist:\n\n"
                )
                d_user += vb if vb.strip() else "(No DIALOGUE PROFILE cues configured.)"
        truncated = False
        if len(d_user) > 280_000:
            d_user = d_user[:280_000] + "\n\n… [truncated for browser preview]\n"
            truncated = True
        out["phase1_dialogue_user_truncated"] = truncated
        out["phase1_dialogue_user"] = d_user
        out["phase1_dialogue_pretty"] = (
            "=== phase1-dialogue (2nd call: polish dialogue[].text only) — SYSTEM ===\n\n"
            + d_sys
            + "\n\n=== phase1-dialogue — USER ===\n\n"
            + d_user
            + "\n"
        )
    tr = ctx.get("theme_recommend_result")
    if isinstance(tr, dict):
        out["focus_topic_similarity"] = {
            "reason": tr.get("reason"),
            "closest_prior_date_id": tr.get("closest_prior_date_id"),
            "closest_prior_similarity": tr.get("closest_prior_similarity"),
        }
    return out
