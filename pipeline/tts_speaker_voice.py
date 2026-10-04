"""Rules for which speakers may use custom / non-narrator TTS (dialogue + talking head)."""

from __future__ import annotations

from typing import Any

from pipeline.narration_visual_mode import VISUAL_MODE_B_ROLL, VISUAL_MODE_TALKING_HEAD

OPENAI_TTS_VOICES = frozenset({"alloy", "echo", "fable", "onyx", "nova", "shimmer", "ash"})


def resolve_openai_tts_voice_for_speaker(
    voice_by_speaker: dict[str, Any],
    speaker_id: str,
    default_voice: str,
) -> str:
    """OpenAI TTS voice name for a speaker; falls back to default_voice (usually the narrator)."""
    sid = (speaker_id or "").strip().lower()
    raw = (voice_by_speaker or {}).get(sid) or (voice_by_speaker or {}).get(speaker_id or "")
    v = (raw or default_voice or "onyx").strip().lower()
    if v not in OPENAI_TTS_VOICES:
        dv = (default_voice or "onyx").strip().lower()
        return dv if dv in OPENAI_TTS_VOICES else "onyx"
    return v


def segment_unique_cast_dialogue_speakers(seg: dict[str, Any]) -> list[str]:
    """Distinct non-narrator speaker_ids with non-empty dialogue text, in first-seen order."""
    rows = seg.get("dialogue")
    if not isinstance(rows, list):
        return []
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("speaker_id") or "").strip().lower()
        txt = str(row.get("text") or "").strip()
        if not sid or sid == "narrator" or not txt:
            continue
        if sid not in out:
            out.append(sid)
    return out


def segment_has_narrator_dialogue_rows(seg: dict[str, Any]) -> bool:
    rows = seg.get("dialogue")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("speaker_id") or "").strip().lower()
        txt = str(row.get("text") or "").strip()
        if sid == "narrator" and txt:
            return True
    return False


def talking_head_subject_has_dedicated_voice(
    talking_head_subject: str,
    *,
    fal_voice_ids: dict[str, str],
    voice_by_speaker: dict[str, Any],
    narrator_openai_voice: str,
    use_openai_tts: bool,
    fal_credentials_ok: bool,
) -> bool:
    """
    True if this subject may use talking-head + character dialogue TTS without reusing the
    documentary narrator's OpenAI voice.

    - ``narrator`` is always allowed (same voice is intentional).
    - Non-OpenAI engines (pyttsx3): no per-speaker distinction vs narrator → False for characters.
    - Otherwise: FAL MiniMax custom_voice_id available and credentials set, **or** an OpenAI
      voice assignment that differs from ``narrator_openai_voice`` after resolution.
    """
    sid = (talking_head_subject or "").strip().lower()
    if not sid:
        return False
    if sid == "narrator":
        return True
    if not use_openai_tts:
        return False
    if fal_credentials_ok and (fal_voice_ids.get(sid) or "").strip():
        return True
    nv = (narrator_openai_voice or "onyx").strip().lower()
    if nv not in OPENAI_TTS_VOICES:
        nv = "onyx"
    resolved = resolve_openai_tts_voice_for_speaker(voice_by_speaker, sid, nv).strip().lower()
    if resolved not in OPENAI_TTS_VOICES:
        resolved = "onyx"
    return resolved != nv


def segment_audio_is_cast_dialogue_only(
    seg: dict[str, Any],
    *,
    fal_voice_ids: dict[str, str],
    voice_by_speaker: dict[str, Any],
    narrator_openai_voice: str,
    use_openai_tts: bool,
    fal_credentials_ok: bool,
) -> bool:
    """
    True when this segment should be synthesized as cast dialogue only (no narrator
    lead-in from ``narration``), same audio policy as ``talking_head``.
    """
    cast = segment_unique_cast_dialogue_speakers(seg)
    if len(cast) != 1:
        return False
    return talking_head_subject_has_dedicated_voice(
        cast[0],
        fal_voice_ids=fal_voice_ids,
        voice_by_speaker=voice_by_speaker,
        narrator_openai_voice=narrator_openai_voice,
        use_openai_tts=use_openai_tts,
        fal_credentials_ok=fal_credentials_ok,
    )


def segment_should_drop_cast_dialogue(
    seg: dict[str, Any],
    *,
    fal_voice_ids: dict[str, str],
    voice_by_speaker: dict[str, Any],
    narrator_openai_voice: str,
    use_openai_tts: bool,
    fal_credentials_ok: bool,
    long_conversation_mode: bool = False,
) -> bool:
    """True when cast dialogue must be dropped so the segment can use narrator-only TTS."""
    from pipeline.phase1_speaker_dialogue_rules import segment_expects_one_cast_speaker_per_segment

    cast = segment_unique_cast_dialogue_speakers(seg)
    if not cast:
        return False
    if not segment_expects_one_cast_speaker_per_segment(
        seg, long_conversation_mode=long_conversation_mode
    ):
        if len(cast) > 1:
            return not all(
                talking_head_subject_has_dedicated_voice(
                    sid,
                    fal_voice_ids=fal_voice_ids,
                    voice_by_speaker=voice_by_speaker,
                    narrator_openai_voice=narrator_openai_voice,
                    use_openai_tts=use_openai_tts,
                    fal_credentials_ok=fal_credentials_ok,
                )
                for sid in cast
            )
        return False
    if len(cast) > 1:
        return True
    return not talking_head_subject_has_dedicated_voice(
        cast[0],
        fal_voice_ids=fal_voice_ids,
        voice_by_speaker=voice_by_speaker,
        narrator_openai_voice=narrator_openai_voice,
        use_openai_tts=use_openai_tts,
        fal_credentials_ok=fal_credentials_ok,
    )


def apply_talking_head_voice_downgrades_to_modes(
    narration_data: dict[str, Any],
    modes: list[str],
    *,
    fal_voice_ids: dict[str, str],
    voice_by_speaker: dict[str, Any],
    narrator_openai_voice: str,
    use_openai_tts: bool,
    fal_credentials_ok: bool,
) -> list[str]:
    """
    Return a copy of ``modes`` with ``talking_head`` downgraded to ``b_roll`` when the subject
    lacks a dedicated voice (aligned with ``narration-to-mp3`` talking-head drive policy).
    """
    script = narration_data.get("narration_script") or []
    out = list(modes)
    for i, row in enumerate(script):
        if i >= len(out):
            break
        if not isinstance(row, dict) or out[i] != VISUAL_MODE_TALKING_HEAD:
            continue
        subj = (row.get("talking_head_subject") or "").strip().lower()
        dialogue = row.get("dialogue")
        has_dialogue = isinstance(dialogue, list) and any(
            isinstance(x, dict) and (str(x.get("text") or "").strip()) for x in dialogue
        )
        if not has_dialogue:
            out[i] = VISUAL_MODE_B_ROLL
            continue
        if talking_head_subject_has_dedicated_voice(
            subj,
            fal_voice_ids=fal_voice_ids,
            voice_by_speaker=voice_by_speaker,
            narrator_openai_voice=narrator_openai_voice,
            use_openai_tts=use_openai_tts,
            fal_credentials_ok=fal_credentials_ok,
        ):
            continue
        out[i] = VISUAL_MODE_B_ROLL
    return out
