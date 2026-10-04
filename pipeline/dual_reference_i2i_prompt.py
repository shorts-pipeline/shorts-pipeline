"""
Shared Wan dual-reference i2i prompt text for two-person scene anchors.

Used by conversation master i2i and b_roll ``reference_character_id`` pair composites
(``lewis_clark``, dynamic pairs, etc.) so both paths bind FIRST/SECOND portrait URLs
the same way without blowing the 2000-char prompt cap.
"""

from __future__ import annotations

from typing import Any


def _strip(v: Any) -> str:
    return str(v or "").strip()


def _speaker_display_name(speaker_id: str) -> str:
    sid = _strip(speaker_id).lower()
    if not sid:
        return ""
    try:
        from pipeline.narration_characters import load_characters
    except Exception:
        return sid
    for c in load_characters():
        if c.id.lower() == sid:
            return c.name or sid
    return sid


def _speaker_physical_description(speaker_id: str) -> str:
    from video_vendors import _character_description_prefix

    return _strip(_character_description_prefix(speaker_id)).rstrip(".")


def dual_reference_fusion_prefix(left_speaker_id: str, right_speaker_id: str) -> str:
    """Map Wan ``image_urls[0]`` / ``image_urls[1]`` to left/right speakers."""
    left_id = _strip(left_speaker_id).lower()
    right_id = _strip(right_speaker_id).lower()
    left_name = _speaker_display_name(left_id) or left_id
    right_name = _speaker_display_name(right_id) or right_id
    return (
        "PRIMARY — FIRST reference portrait is "
        f"{left_id} ({left_name}), placed on the left; SECOND is "
        f"{right_id} ({right_name}), on the right. Match reference face and body build; "
        "do not bulk up or swap identities. Two separate figures only. "
    )


def dual_reference_physique_hint(left_speaker_id: str, right_speaker_id: str) -> str:
    """One short physique cue per speaker — not full roster paragraphs."""
    hints: list[str] = []
    for sid in (left_speaker_id, right_speaker_id):
        phys = _speaker_physical_description(sid).lower()
        name = _speaker_display_name(sid) or sid
        if not phys:
            continue
        if "lean" in phys:
            hints.append(f"{name} lean build")
        elif "broad-shoulder" in phys or "broad shoulder" in phys:
            hints.append(f"{name} broad-shouldered")
        elif "spare athletic" in phys or "athletic build" in phys:
            hints.append(f"{name} spare athletic build")
        elif "sturdy" in phys:
            hints.append(f"{name} sturdy build")
    if not hints:
        return ""
    return "Physique: " + "; ".join(hints) + ". "


def build_dual_reference_i2i_prompt(
    *,
    left_speaker_id: str,
    right_speaker_id: str,
    scene_text: str,
    world_prefix: str = "",
    expression: str = "",
    gaze_phrase: str = "",
    shot_framing: str = "Wide medium two-shot. No readable text.",
) -> str:
    """
    Trimmed two-reference Wan i2i prompt: fusion binding, backdrop/scene, short physique hints.

    ``scene_text`` is the segment ``opening_frame``, ``video_prompt`` excerpt, or
    ``conversation_micro_arc.shared_setting`` — whichever defines this still's set.
    """
    left_name = _speaker_display_name(left_speaker_id) or left_speaker_id
    right_name = _speaker_display_name(right_speaker_id) or right_speaker_id
    setting = _strip(scene_text).rstrip(".")
    wp = _strip(world_prefix).rstrip(".")
    lead = f"{wp}. " if wp else ""
    expr = _strip(expression).rstrip(".")
    expr_part = f" {expr}." if expr else ""
    gaze = _strip(gaze_phrase)
    gaze_part = f" {gaze}" if gaze else ""
    if gaze_part and not gaze_part.endswith("."):
        gaze_part += "."
    fusion = dual_reference_fusion_prefix(left_speaker_id, right_speaker_id)
    physique = dual_reference_physique_hint(left_speaker_id, right_speaker_id)
    scene_part = f"{setting}. " if setting else ""
    return (
        f"{fusion}{lead}{scene_part}"
        f"{left_name} on the left, {right_name} on the right. "
        f"{physique}{gaze_part}{expr_part} "
        f"{shot_framing}"
    )
