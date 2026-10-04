"""Format optional per-character dialogue_profile cues for Phase 1 dialogue polish (not TTS audio)."""

from __future__ import annotations

from pipeline.narration_characters.storage import load_characters


def dialogue_profile_prompt_section() -> str:
    """Return a block for expedition cast with dialogue_profile, or empty string."""
    rows: list[str] = []
    for c in sorted((x for x in load_characters() if x.dialogue_profile), key=lambda x: x.id):
        dp = c.dialogue_profile
        if dp is None:
            continue
        lines: list[str] = [f"**{c.name}** (`{c.id}`)"]
        if dp.habitual_words:
            joined = ", ".join(dp.habitual_words)
            lines.append(
                f"- Words/phrases often associated with this figure's journal voice "
                f"(sprinkle occasionally—not every line): {joined}"
            )
        if dp.speech_rhythm:
            lines.append(f"- Speech rhythm / structure: {dp.speech_rhythm}")
        for cue in dp.cues:
            lines.append(f"- {cue}")
        rows.append("\n".join(lines))
    if not rows:
        return ""
    return (
        "DIALOGUE PROFILE (spoken-line temperament from config; ground in surviving "
        "journals/letters—avoid caricature):\n\n" + "\n\n".join(rows)
    )
