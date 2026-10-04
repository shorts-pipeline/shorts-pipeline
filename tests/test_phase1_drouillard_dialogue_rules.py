"""Phase 1: Drouillard dialogue must not boost settlement/colonization."""

from __future__ import annotations

import pytest

from pipeline.narration_phase1 import _PACK_DIALOGUE_STANDARD, _validate_phase1
from pipeline.phase1_speaker_dialogue_rules import validate_speaker_dialogue_rules

# Fixtures must clear MIN_SPOKEN_NARRATION_WORDS (18) so _validate_phase1's spoken
# word-limit gate does not trip before the Drouillard content rule under test.
_HOOK_NARRATION = (
    "Opening beat for the expedition as a thin morning fog lifts slowly along the muddy "
    "Missouri bank, and the camp begins to stir before dawn."
)
_DIRECTOR_NARRATION = (
    "They return from the scout with news of the trail and what lies ahead for the party."
)


def _phase1_with_drouillard_line(text: str, *, segment_index: int = 5) -> dict:
    segs = [
        {
            "segment_type": "hook",
            "visual_mode": "b_roll",
            "narration": _HOOK_NARRATION,
            "dialogue": [],
        },
        {
            "segment_type": "action",
            "segment_index": segment_index,
            "visual_mode": "b_roll",
            "narration": _DIRECTOR_NARRATION,
            "dialogue": [{"speaker_id": "drouillard", "text": text}],
        },
    ]
    return {"title": "t", "tone_register": "light", "segments": segs}


def test_drouillard_settlement_boosterism_rejected() -> None:
    parsed = _phase1_with_drouillard_line("Few spots we've seen hold such promise for settlement.")
    with pytest.raises(ValueError, match=r"drouillard.*settlement"):
        validate_speaker_dialogue_rules(parsed["segments"])


def test_drouillard_trail_hardship_ok() -> None:
    parsed = _phase1_with_drouillard_line(
        "Seven days out, the creeks ran high and we swam more than we waded, and the mud "
        "pulled at every single step we took."
    )
    validate_speaker_dialogue_rules(parsed["segments"])
    _validate_phase1(parsed, dialogue_mode=True, prompt_pack=_PACK_DIALOGUE_STANDARD)


def test_validate_phase1_wires_speaker_dialogue_rules() -> None:
    parsed = _phase1_with_drouillard_line(
        "Good country to settle, the captain said today, though we surely ought not speak aloud "
        "of homesteads or townsites while the hard work is still ahead of us."
    )
    with pytest.raises(ValueError, match=r"speaker dialogue content"):
        _validate_phase1(parsed, dialogue_mode=True, prompt_pack=_PACK_DIALOGUE_STANDARD)
