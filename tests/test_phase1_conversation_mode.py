from pipeline.narration_phase1 import build_phase1_user_prompt


def test_build_phase1_user_prompt_includes_conversation_block_when_enabled() -> None:
    prompt = build_phase1_user_prompt(
        entry_text="Sample entry text.",
        date_id="18040523",
        dialogue_mode=True,
        conversation_mode={"enabled": True, "min_segments": 3, "min_lines_per_segment": 5},
    )

    assert "CONVERSATION BEAT (enabled for this run)" in prompt
    assert "at least 3 consecutive" in prompt
    assert "at least 5 dialogue lines per segment" in prompt


def test_build_phase1_user_prompt_omits_conversation_block_when_disabled() -> None:
    prompt = build_phase1_user_prompt(
        entry_text="Sample entry text.",
        date_id="18040523",
        dialogue_mode=True,
        conversation_mode={"enabled": False},
    )

    assert "CONVERSATION BEAT (enabled for this run)" not in prompt
