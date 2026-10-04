from pipeline.narration_utils import enforce_narrator_only_for_style_policy


def test_enforce_narrator_only_for_style_policy_uses_configured_style_names() -> None:
    data = {"visual_style": {"name": "Minimalist Schematic Visualization"}}
    assert enforce_narrator_only_for_style_policy(
        data,
        narrator_only_style_names=["Minimalist Schematic Visualization"],
    )
    assert not enforce_narrator_only_for_style_policy(
        {"visual_style": {"name": "Photoreal Documentary"}},
        narrator_only_style_names=["Minimalist Schematic Visualization"],
    )


def test_enforce_narrator_only_for_style_policy_rewrites_talking_head_segments() -> None:
    data = {
        "visual_style": {"name": "Minimalist Schematic Visualization"},
        "dialogue_mode": True,
        "fal_scene_anchor_openings": True,
        "narration_script": [
            {
                "segment_index": 3,
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "talking_head_prompt": "test",
                "dialogue": [{"speaker_id": "lewis", "text": "Hello"}],
            },
            {
                "segment_index": 4,
                "visual_mode": "b_roll",
                "dialogue": [{"speaker_id": "narrator", "text": "legacy"}],
            },
        ],
    }

    applied = enforce_narrator_only_for_style_policy(
        data,
        narrator_only_style_names=["Minimalist Schematic Visualization"],
    )

    assert applied is True
    assert data["dialogue_mode"] is False
    assert data["fal_scene_anchor_openings"] is False
    assert data["narration_script"][0]["visual_mode"] == "b_roll"
    assert "dialogue" not in data["narration_script"][0]
    assert "talking_head_subject" not in data["narration_script"][0]
    assert "talking_head_prompt" not in data["narration_script"][0]
    assert "dialogue" not in data["narration_script"][1]


def test_enforce_narrator_only_for_style_policy_noop_for_other_styles() -> None:
    data = {
        "visual_style": {"name": "Photoreal Documentary"},
        "dialogue_mode": True,
        "fal_scene_anchor_openings": True,
        "narration_script": [{"visual_mode": "talking_head", "dialogue": [{"speaker_id": "x"}]}],
    }

    applied = enforce_narrator_only_for_style_policy(
        data,
        narrator_only_style_names=["Minimalist Schematic Visualization"],
    )

    assert applied is False
    assert data["dialogue_mode"] is True
    assert data["fal_scene_anchor_openings"] is True
    assert data["narration_script"][0]["visual_mode"] == "talking_head"
