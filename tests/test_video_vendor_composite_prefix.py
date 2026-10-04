import json

from video_vendors import build_prompts


def test_build_prompts_adds_separation_guardrail_for_composite_portrait(tmp_path, monkeypatch):
    narrations_dir = tmp_path / "narrations"
    narrations_dir.mkdir(parents=True, exist_ok=True)
    portraits_dir = tmp_path / "character-portraits"
    portraits_dir.mkdir(parents=True, exist_ok=True)
    (portraits_dir / "lewis_clark.png").write_bytes(b"fake")

    narration = {
        "scene_spine": {"core_location": "Camp Dubois", "visual_mood": "grounded"},
        "narration_script": [
            {
                "segment_index": 1,
                "stage_direction": "static",
                "video_prompt": "Lewis and Clark confer over a folded map near camp.",
                "reference_character_id": "lewis_clark",
            }
        ],
    }
    (narrations_dir / "narration18040520.json").write_text(json.dumps(narration), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    prompts = build_prompts("18040520", narrations_dir=narrations_dir, vendor="fal")

    assert len(prompts) == 1
    assert "FAL_IMAGE_CHAR=lewis_clark" in prompts[0]
    assert "Two distinct people in frame, clearly separated bodies and faces" in prompts[0]
