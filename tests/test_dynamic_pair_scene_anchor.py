"""Dynamic pair scene anchors for b_roll (dual solo portrait i2i)."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from pipeline.dynamic_pair_scene_anchor import (
    broll_dual_portrait_kwargs,
    pair_reference_ready,
    portrait_reference_ready,
    resolve_pair_member_ids_for_composite,
)
from pipeline.narration_characters.matching import find_character_match
from video_vendors import build_prompts


def test_resolve_ad_hoc_pair_members(tmp_path: Path, monkeypatch) -> None:
    portraits = tmp_path / "character-portraits"
    portraits.mkdir()
    Image.new("RGBA", (10, 10), (1, 2, 3, 255)).save(portraits / "colter.png")
    Image.new("RGBA", (10, 10), (4, 5, 6, 255)).save(portraits / "seaman.png")

    from pipeline.narration_characters import storage

    monkeypatch.setattr(storage, "PORTRAITS_DIR", portraits)
    assert resolve_pair_member_ids_for_composite("colter_seaman") == ("colter", "seaman")
    assert pair_reference_ready("colter_seaman") is True
    assert portrait_reference_ready("colter_seaman") is True


def test_pair_reference_ready_without_composite_png(tmp_path: Path, monkeypatch) -> None:
    portraits = tmp_path / "character-portraits"
    portraits.mkdir()
    Image.new("RGBA", (10, 10), (1, 2, 3, 255)).save(portraits / "lewis.png")
    Image.new("RGBA", (10, 10), (4, 5, 6, 255)).save(portraits / "clark.png")

    from pipeline.narration_characters import storage

    monkeypatch.setattr(storage, "PORTRAITS_DIR", portraits)
    assert pair_reference_ready("lewis_clark") is True


def test_broll_dual_portrait_kwargs(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    portraits = repo / "character-portraits"
    portraits.mkdir()
    Image.new("RGBA", (20, 30), (200, 0, 0, 255)).save(portraits / "colter.png")
    Image.new("RGBA", (20, 30), (0, 0, 200, 255)).save(portraits / "seaman.png")

    from pipeline.narration_characters import storage

    monkeypatch.setattr(storage, "PORTRAITS_DIR", portraits)

    kwargs = broll_dual_portrait_kwargs(repo, "colter_seaman")
    assert kwargs is not None
    assert kwargs["dual_reference_speakers"] == ("colter", "seaman")
    assert len(kwargs["portrait_data_uris"]) == 2
    assert kwargs["portrait_data_uris"][0].startswith("data:image/")


def test_build_prompts_enables_scene_anchor_for_dynamic_pair(tmp_path: Path, monkeypatch) -> None:
    narrations_dir = tmp_path / "narrations"
    narrations_dir.mkdir(parents=True)
    portraits_dir = tmp_path / "character-portraits"
    portraits_dir.mkdir()
    Image.new("RGBA", (10, 10), (1, 2, 3, 255)).save(portraits_dir / "colter.png")
    Image.new("RGBA", (10, 10), (4, 5, 6, 255)).save(portraits_dir / "seaman.png")

    narration = {
        "scene_spine": {"core_location": "Riverbank", "visual_mood": "grounded"},
        "narration_script": [
            {
                "segment_index": 1,
                "visual_mode": "b_roll",
                "video_prompt": "Colter and Seaman walk side by side along the muddy bank.",
                "reference_character_id": "colter_seaman",
            }
        ],
    }
    (narrations_dir / "narration18040611.json").write_text(json.dumps(narration), encoding="utf-8")

    from pipeline.narration_characters import storage

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(storage, "PORTRAITS_DIR", portraits_dir)

    prompts = build_prompts("18040611", narrations_dir=narrations_dir, vendor="fal")
    assert len(prompts) == 1
    assert "FAL_IMAGE_CHAR=colter_seaman" in prompts[0]
    assert "FAL_SCENE_ANCHOR=1" in prompts[0]
    assert "Two distinct people in frame" in prompts[0]


def test_find_character_match_dynamic_pair(tmp_path: Path, monkeypatch) -> None:
    char_path = tmp_path / "config" / "narration_characters.json"
    char_path.parent.mkdir(parents=True)
    portraits = tmp_path / "character-portraits"
    portraits.mkdir()
    Image.new("RGBA", (10, 10), (1, 2, 3, 255)).save(portraits / "colter.png")
    Image.new("RGBA", (10, 10), (4, 5, 6, 255)).save(portraits / "seaman.png")

    char_path.write_text(
        json.dumps(
            {
                "pair_portraits": [],
                "people": [
                    {
                        "id": "colter",
                        "name": "John Colter",
                        "aliases": ["colter"],
                        "roles": ["hunter"],
                        "active_from": "1803-01-01",
                        "active_to": "1806-12-31",
                        "physical_description": "Lean scout.",
                        "key_skills": [],
                        "skill_keywords": ["colter"],
                    },
                    {
                        "id": "seaman",
                        "name": "Seaman",
                        "aliases": ["seaman", "the dog"],
                        "roles": ["dog"],
                        "active_from": "1803-01-01",
                        "active_to": "1806-12-31",
                        "physical_description": "Black Newfoundland.",
                        "key_skills": [],
                        "skill_keywords": ["seaman"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    from pipeline.narration_characters import storage

    monkeypatch.setattr(storage, "CHAR_PATH", char_path)
    monkeypatch.setattr(storage, "PORTRAITS_DIR", portraits)
    storage.load_characters.cache_clear()
    storage.load_pair_portrait_rules.cache_clear()
    storage.composite_portrait_ids.cache_clear()

    match = find_character_match(
        "18040611",
        "Colter and Seaman walk together.",
        {},
        max_per_character=3,
        video_prompt="Colter and Seaman walk side by side along the trail.",
    )
    assert match is not None
    assert match.reason == "dynamic_pair_portrait"
    assert match.character.id == "colter_seaman"
