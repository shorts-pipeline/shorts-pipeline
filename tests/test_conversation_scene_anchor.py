"""Tests for pipeline.conversation_scene_anchor (no FAL)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pipeline.conversation_scene_anchor import (
    _speaker_adjacent_crop_bounds,
    build_conversation_master_opening_frame,
    discover_conversation_anchor_runs,
    reframe_crop_for_anchor,
    segment_to_conversation_run,
    split_master_to_speaker_images,
)
from pipeline.narration_characters.storage import (
    lookup_composite_pair,
    resolve_composite_for_speakers,
)
from pipeline.portrait_composite import ensure_composite_portrait_cached


def _rgb_png_bytes(left_rgb: tuple[int, int, int], right_rgb: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (200, 100))
    for x in range(100):
        for y in range(100):
            img.putpixel((x, y), left_rgb)
    for x in range(100, 200):
        for y in range(100):
            img.putpixel((x, y), right_rgb)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_resolve_composite_for_speakers_order(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    char_path = repo / "config" / "narration_characters.json"
    char_path.parent.mkdir(parents=True)
    char_path.write_text(
        json.dumps(
            {
                "pair_portraits": [
                    {"composite_id": "lewis_clark", "member_ids": ["lewis", "clark"]}
                ],
                "people": [],
            }
        ),
        encoding="utf-8",
    )
    portraits = repo / "character-portraits"
    portraits.mkdir()
    (portraits / "lewis.png").write_bytes(b"x")
    (portraits / "clark.png").write_bytes(b"x")

    import pipeline.narration_characters.storage as storage

    monkeypatch.setattr(storage, "CHAR_PATH", char_path)
    monkeypatch.setattr(storage, "PORTRAITS_DIR", portraits)
    storage.load_pair_portrait_rules.cache_clear()
    storage.composite_portrait_ids.cache_clear()

    resolved = lookup_composite_pair("clark", "lewis")
    assert resolved == ("lewis_clark", "lewis", "clark")
    assert resolve_composite_for_speakers("clark", "lewis") == resolved


def test_discover_conversation_runs_requires_long_mode_and_composite(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path
    char_path = repo / "config" / "narration_characters.json"
    char_path.parent.mkdir(parents=True)
    char_path.write_text(
        json.dumps(
            {
                "pair_portraits": [
                    {"composite_id": "lewis_clark", "member_ids": ["lewis", "clark"]}
                ],
                "people": [],
            }
        ),
        encoding="utf-8",
    )
    portraits = repo / "character-portraits"
    portraits.mkdir()
    (portraits / "lewis.png").write_bytes(b"x")
    (portraits / "clark.png").write_bytes(b"x")

    import pipeline.narration_characters.storage as storage

    monkeypatch.setattr(storage, "CHAR_PATH", char_path)
    monkeypatch.setattr(storage, "PORTRAITS_DIR", portraits)
    storage.load_pair_portrait_rules.cache_clear()
    storage.composite_portrait_ids.cache_clear()

    narr = {
        "long_conversation_mode": True,
        "conversation_micro_arc": {
            "shared_setting": "Windbound camp beside the Missouri.",
            "speakers": ["lewis", "clark"],
            "segment_indices": [3, 4, 5],
        },
        "narration_script": [
            {"segment_index": 1, "visual_mode": "b_roll", "narration": "n"},
            {"segment_index": 2, "visual_mode": "b_roll", "narration": "n"},
            {
                "segment_index": 3,
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "narration": "n",
            },
            {
                "segment_index": 4,
                "visual_mode": "talking_head",
                "talking_head_subject": "clark",
                "narration": "n",
            },
            {
                "segment_index": 5,
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "narration": "n",
            },
        ],
    }
    runs = discover_conversation_anchor_runs(narr)
    assert len(runs) == 1
    run = runs[0]
    assert run.composite_id == "lewis_clark"
    assert run.segment_indices == (3, 4, 5)
    assert run.left_speaker_id == "lewis"
    assert run.right_speaker_id == "clark"
    assert "Windbound camp" in run.shared_setting

    seg_map = segment_to_conversation_run(runs)
    assert seg_map[4].composite_id == "lewis_clark"


def test_speaker_adjacent_crop_bounds_when_clustered() -> None:
    bounds = _speaker_adjacent_crop_bounds(
        1280,
        {"clark": 830, "ordway": 866},
        left_speaker_id="clark",
        right_speaker_id="ordway",
        crop_width_fraction=0.40,
        right_shift_fraction=0.04,
    )
    crop_w = int(round(1280 * 0.40))
    for sid, cx in (("clark", 830), ("ordway", 866)):
        left = max(0, min(int(round(cx - crop_w / 2)), 1280 - crop_w))
        assert bounds[sid] == (left, left + crop_w)
        assert bounds[sid][0] < cx < bounds[sid][1]


def test_speaker_adjacent_crop_bounds_when_far_apart() -> None:
    bounds = _speaker_adjacent_crop_bounds(
        400,
        {"lewis": 50, "clark": 330},
        left_speaker_id="lewis",
        right_speaker_id="clark",
        crop_width_fraction=0.40,
        right_shift_fraction=0.04,
    )
    assert bounds["lewis"][0] < 50 < bounds["lewis"][1]
    assert bounds["clark"][0] < 330 < bounds["clark"][1]
    assert bounds["lewis"][1] <= bounds["clark"][0] or bounds["clark"][1] <= bounds["lewis"][0]


def test_conversation_master_split_crop_defaults() -> None:
    from pipeline.conversation_scene_anchor import (
        conversation_master_split_crop_width_fraction,
        conversation_master_split_right_shift_fraction,
    )

    assert conversation_master_split_crop_width_fraction() == 0.45
    assert conversation_master_split_right_shift_fraction() == 0.07


def test_estimate_speaker_centers_use_side_bands(tmp_path: Path, monkeypatch) -> None:
    from PIL import Image

    from pipeline.conversation_scene_anchor import _estimate_speaker_centers_x

    repo = tmp_path
    portraits = repo / "character-portraits"
    portraits.mkdir(parents=True)
    left_tpl = Image.new("RGB", (40, 60), (200, 40, 40))
    right_tpl = Image.new("RGB", (40, 60), (40, 40, 200))
    left_tpl.save(portraits / "lewis.png")
    right_tpl.save(portraits / "clark.png")

    master = Image.new("RGB", (400, 200), (30, 30, 30))
    master.paste(left_tpl, (30, 10))
    master.paste(right_tpl, (310, 10))

    import pipeline.narration_characters.storage as storage

    char_path = repo / "config" / "narration_characters.json"
    char_path.parent.mkdir(parents=True)
    char_path.write_text(json.dumps({"pair_portraits": [], "people": []}), encoding="utf-8")
    monkeypatch.setattr(storage, "CHAR_PATH", char_path)
    monkeypatch.setattr(storage, "PORTRAITS_DIR", portraits)

    centers = _estimate_speaker_centers_x(
        master,
        repo_root=repo,
        left_speaker_id="lewis",
        right_speaker_id="clark",
    )
    assert centers is not None
    assert centers["lewis"] < 120
    assert centers["clark"] > 280


def test_split_master_top_align_preserves_upper_band(tmp_path: Path, monkeypatch) -> None:
    from PIL import Image

    repo = tmp_path
    portraits = repo / "character-portraits"
    portraits.mkdir(parents=True)
    left_tpl = Image.new("RGB", (40, 60), (200, 40, 40))
    right_tpl = Image.new("RGB", (40, 60), (40, 40, 200))
    left_tpl.save(portraits / "lewis.png")
    right_tpl.save(portraits / "clark.png")

    master = Image.new("RGB", (400, 200), (30, 30, 30))
    master.paste(left_tpl, (30, 10))
    master.paste(right_tpl, (310, 10))
    buf = io.BytesIO()
    master.save(buf, format="PNG")

    import pipeline.narration_characters.storage as storage

    char_path = repo / "config" / "narration_characters.json"
    char_path.parent.mkdir(parents=True)
    char_path.write_text(json.dumps({"pair_portraits": [], "people": []}), encoding="utf-8")
    monkeypatch.setattr(storage, "CHAR_PATH", char_path)
    monkeypatch.setattr(storage, "PORTRAITS_DIR", portraits)

    split = split_master_to_speaker_images(
        buf.getvalue(),
        left_speaker_id="lewis",
        right_speaker_id="clark",
        aspect_ratio="9:16",
        repo_root=repo,
        crop_width_fraction=0.25,
    )
    lewis = Image.open(io.BytesIO(split["lewis"])).convert("RGB")
    y_frac = 0.08
    ly = int(round(lewis.height * y_frac))
    assert lewis.getpixel((lewis.width // 2, ly))[0] > lewis.getpixel((lewis.width // 2, ly))[2]


def test_estimate_speaker_centers_match_each_portrait_globally(tmp_path: Path, monkeypatch) -> None:
    """Each speaker is located by their own portrait across the full master width."""
    from PIL import Image

    from pipeline.conversation_scene_anchor import (
        _estimate_speaker_centers_x,
        split_master_to_speaker_images,
    )

    repo = tmp_path
    portraits = repo / "character-portraits"
    portraits.mkdir(parents=True)
    # Distinct flat-color portrait templates
    left_tpl = Image.new("RGB", (40, 60), (200, 40, 40))
    right_tpl = Image.new("RGB", (40, 60), (40, 40, 200))
    left_tpl.save(portraits / "lewis.png")
    right_tpl.save(portraits / "clark.png")

    master = Image.new("RGB", (400, 200), (30, 30, 30))
    master.paste(left_tpl, (30, 10))
    master.paste(right_tpl, (310, 10))
    buf = io.BytesIO()
    master.save(buf, format="PNG")

    import pipeline.narration_characters.storage as storage

    char_path = repo / "config" / "narration_characters.json"
    char_path.parent.mkdir(parents=True)
    char_path.write_text(json.dumps({"pair_portraits": [], "people": []}), encoding="utf-8")
    monkeypatch.setattr(storage, "CHAR_PATH", char_path)
    monkeypatch.setattr(storage, "PORTRAITS_DIR", portraits)

    centers = _estimate_speaker_centers_x(
        master,
        repo_root=repo,
        left_speaker_id="lewis",
        right_speaker_id="clark",
    )
    assert centers is not None
    assert centers["lewis"] < centers["clark"]

    split = split_master_to_speaker_images(
        buf.getvalue(),
        left_speaker_id="lewis",
        right_speaker_id="clark",
        repo_root=repo,
        crop_width_fraction=0.25,
    )
    lewis = Image.open(io.BytesIO(split["lewis"])).convert("RGB")
    clark = Image.open(io.BytesIO(split["clark"])).convert("RGB")
    # With scale_mode="cover" (split-master path), portrait content may move
    # vertically; sample closer to the top third where the templates live.
    y_frac = 0.15
    ly = int(round(lewis.height * y_frac))
    cy = int(round(clark.height * y_frac))
    assert lewis.getpixel((lewis.width // 2, ly))[0] > clark.getpixel((clark.width // 2, cy))[0]


def test_split_master_assigns_distinct_halves() -> None:
    master = _rgb_png_bytes((200, 40, 40), (40, 40, 200))
    split = split_master_to_speaker_images(
        master,
        left_speaker_id="lewis",
        right_speaker_id="clark",
        aspect_ratio="9:16",
    )
    assert set(split) == {"lewis", "clark"}
    left = Image.open(io.BytesIO(split["lewis"])).convert("RGB")
    right = Image.open(io.BytesIO(split["clark"])).convert("RGB")
    lx, ly = left.width // 2, left.height // 2
    rx, ry = right.width // 2, right.height // 2
    assert left.getpixel((lx, ly))[0] > right.getpixel((rx, ry))[0]


def test_discover_conversation_run_uses_raw_shared_setting_without_props() -> None:
    narr = {
        "long_conversation_mode": True,
        "conversation_micro_arc": {
            "shared_setting": "Grassy hill above the Missouri River.",
            "persistent_props": ["keelboat moored stage-left"],
            "speakers": ["lewis", "clark"],
            "segment_indices": [3, 4],
        },
        "narration_script": [
            {"segment_index": 1, "visual_mode": "b_roll", "narration": "hook"},
            {
                "segment_index": 3,
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "reference_character_id": "lewis",
                "narration": "n",
            },
            {
                "segment_index": 4,
                "visual_mode": "talking_head",
                "talking_head_subject": "clark",
                "reference_character_id": "clark",
                "narration": "n",
            },
        ],
    }
    runs = discover_conversation_anchor_runs(narr)
    assert len(runs) == 1
    assert runs[0].shared_setting == "Grassy hill above the Missouri River."
    assert "keelboat" not in runs[0].shared_setting.lower()


def test_conversation_master_world_prefix_omits_segment_place_label() -> None:
    from pipeline.conversation_scene_anchor import conversation_master_world_prefix

    narr = {
        "scene_spine": {
            "visual_mood": "reflective",
            "visual_blocks": [
                {"segment_indices": [3], "place_label": "Deck of the keelboat"},
            ],
        }
    }
    world = conversation_master_world_prefix(narr)
    assert "keelboat" not in world.lower()
    assert "reflective" in world


def test_master_opening_frame_names_both_speakers() -> None:
    text = build_conversation_master_opening_frame(
        shared_setting="Same tent mouth at dusk.",
        left_speaker_id="lewis",
        right_speaker_id="clark",
    )
    assert "on the left" in text
    assert "on the right" in text
    assert "Same tent mouth" in text
    assert "three-quarter view" in text
    assert "no smiles" not in text.lower()


def test_master_opening_frame_uses_narration_expression() -> None:
    text = build_conversation_master_opening_frame(
        shared_setting="Same tent mouth at dusk.",
        left_speaker_id="lewis",
        right_speaker_id="clark",
        master_expression="Solemn grave faces, no smiles.",
    )
    assert "Solemn grave faces" in text


def test_conversation_master_i2i_prompt_is_short() -> None:
    from pipeline.conversation_scene_anchor import (
        ConversationAnchorRun,
        build_conversation_master_i2i_prompt,
    )

    run = ConversationAnchorRun(
        first_segment_index=3,
        segment_indices=(3, 4, 5, 6),
        composite_id="clark_ordway",
        left_speaker_id="clark",
        right_speaker_id="ordway",
        shared_setting="Keelboat deck near the Chariton Rivers.",
        master_expression="Alert engaged focus, mouths relaxed before speech.",
    )
    prompt = build_conversation_master_i2i_prompt(
        run=run,
        world_prefix="Photorealistic period scene set in early 1800's, On deck near the Chariton Rivers, overall mood grounded",
        dual_portrait_reference=True,
    )
    assert len(prompt) < 1200
    assert "Clark" in prompt or "clark" in prompt.lower()
    assert "Ordway" in prompt or "ordway" in prompt.lower()
    assert "Chariton" in prompt
    assert "on the left" in prompt
    assert "Alert engaged focus" in prompt
    assert "solemn" not in prompt.lower()
    assert "FIRST reference portrait" in prompt
    assert "opening frame" not in prompt.lower()


def test_conversation_master_reference_mode_defaults_dual_portrait() -> None:
    from pipeline.conversation_scene_anchor import conversation_master_reference_mode

    assert conversation_master_reference_mode() == "dual_portrait"


def test_conversation_master_image_size_doubles_shorts_width() -> None:
    from pipeline.conversation_scene_anchor import conversation_master_image_size

    with (
        patch(
            "pipeline.conversation_scene_anchor.conversation_uses_shared_master_mask",
            return_value=False,
        ),
        patch(
            "pipeline.conversation_scene_anchor.conversation_master_width_multiplier",
            return_value=2.0,
        ),
    ):
        size = conversation_master_image_size("9:16")
    assert isinstance(size, dict)
    assert size["width"] == 1440
    assert size["height"] == 1280


def test_lookup_ad_hoc_pair_without_config() -> None:
    assert lookup_composite_pair("york", "clark") == ("clark_york", "clark", "york")


def test_ensure_composite_portrait_cached_builds_and_reuses(tmp_path: Path) -> None:
    portraits = tmp_path / "character-portraits"
    portraits.mkdir()
    left = Image.new("RGBA", (40, 60), (200, 0, 0, 255))
    right = Image.new("RGBA", (40, 60), (0, 0, 200, 255))
    left.save(portraits / "lewis.png")
    right.save(portraits / "clark.png")

    out = ensure_composite_portrait_cached(
        composite_id="lewis_clark",
        left_member_id="lewis",
        right_member_id="clark",
        portraits_dir=portraits,
        gap_px=4,
    )
    assert out.is_file()
    assert out.name == "lewis_clark.png"
    w, h = Image.open(out).size
    assert w == 40 + 4 + 40
    assert h == 60

    out.write_bytes(b"cached")
    again = ensure_composite_portrait_cached(
        composite_id="lewis_clark",
        left_member_id="lewis",
        right_member_id="clark",
        portraits_dir=portraits,
    )
    assert again.read_bytes() == b"cached"


def test_conversation_anchor_ui_enrichment() -> None:
    narr = {
        "long_conversation_mode": True,
        "conversation_micro_arc": {
            "shared_setting": "Same camp fire ring at dusk.",
            "speakers": ["lewis", "clark"],
            "segment_indices": [3, 4],
        },
        "narration_script": [
            {"visual_mode": "b_roll", "narration": "n"},
            {"visual_mode": "b_roll", "narration": "n"},
            {"visual_mode": "talking_head", "talking_head_subject": "lewis", "narration": "n"},
            {"visual_mode": "talking_head", "talking_head_subject": "clark", "narration": "n"},
        ],
    }
    from pipeline.conversation_scene_anchor import conversation_anchor_ui_enrichment

    ui = conversation_anchor_ui_enrichment(narr)
    assert len(ui["conversation_runs"]) == 1
    assert ui["conversation_runs"][0]["segment_indices"] == [3, 4]
    assert ui["conversation_by_segment"]["3"]["conversation_composite_id"]
    assert ui["conversation_by_segment"]["4"]["conversation_run_segments"] == [3, 4]


def test_discover_without_prebuilt_composite_file(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    char_path = repo / "config" / "narration_characters.json"
    char_path.parent.mkdir(parents=True)
    char_path.write_text(json.dumps({"pair_portraits": [], "people": []}), encoding="utf-8")
    portraits = repo / "character-portraits"
    portraits.mkdir()
    (portraits / "lewis.png").write_bytes(b"x")
    (portraits / "clark.png").write_bytes(b"x")

    import pipeline.narration_characters.storage as storage

    monkeypatch.setattr(storage, "CHAR_PATH", char_path)
    monkeypatch.setattr(storage, "PORTRAITS_DIR", portraits)
    storage.load_pair_portrait_rules.cache_clear()

    narr = {
        "long_conversation_mode": True,
        "conversation_micro_arc": {
            "shared_setting": "Camp fire ring.",
            "speakers": ["lewis", "clark"],
            "segment_indices": [1, 2],
        },
        "narration_script": [
            {
                "segment_index": 1,
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
                "narration": "n",
            },
            {
                "segment_index": 2,
                "visual_mode": "talking_head",
                "talking_head_subject": "clark",
                "narration": "n",
            },
        ],
    }
    runs = discover_conversation_anchor_runs(narr)
    assert len(runs) == 1
    assert runs[0].composite_id == "clark_lewis"


def test_reframe_crop_focus_places_marker_at_target() -> None:
    # Aspect ratio similar to a master split strip (narrower than 9:16).
    crop = Image.new("RGB", (162, 320), (0, 0, 0))
    marker_x, marker_y = 81, 70
    for dx in range(-3, 4):
        for dy in range(-3, 4):
            crop.putpixel((marker_x + dx, marker_y + dy), (255, 0, 0))
    face_y_fraction = 0.22
    out = reframe_crop_for_anchor(
        crop,
        target_w=90,
        target_h=160,
        focus_xy=(marker_x, marker_y),
        face_x_fraction=0.5,
        face_y_fraction=face_y_fraction,
    )
    expected_x = int(round(90 * 0.5))
    expected_y = int(round(160 * face_y_fraction))
    assert abs(out.getpixel((expected_x, expected_y))[0] - 255) < 20


def test_split_master_clustered_faces_centered(tmp_path: Path, monkeypatch) -> None:
    """When both figures are close, each anchor face should land near top-center."""
    from PIL import Image

    repo = tmp_path
    portraits = repo / "character-portraits"
    portraits.mkdir(parents=True)
    left_tpl = Image.new("RGB", (40, 60), (200, 40, 40))
    right_tpl = Image.new("RGB", (40, 60), (40, 40, 200))
    left_tpl.save(portraits / "lewis.png")
    right_tpl.save(portraits / "clark.png")

    master = Image.new("RGB", (400, 200), (30, 30, 30))
    # Cluster both figures near center so legacy split would push faces to strip edges.
    master.paste(left_tpl, (150, 10))
    master.paste(right_tpl, (210, 10))
    buf = io.BytesIO()
    master.save(buf, format="PNG")

    import pipeline.narration_characters.storage as storage

    char_path = repo / "config" / "narration_characters.json"
    char_path.parent.mkdir(parents=True)
    char_path.write_text(json.dumps({"pair_portraits": [], "people": []}), encoding="utf-8")
    monkeypatch.setattr(storage, "CHAR_PATH", char_path)
    monkeypatch.setattr(storage, "PORTRAITS_DIR", portraits)

    split = split_master_to_speaker_images(
        buf.getvalue(),
        left_speaker_id="lewis",
        right_speaker_id="clark",
        aspect_ratio="9:16",
        repo_root=repo,
        crop_width_fraction=0.45,
    )
    lewis = Image.open(io.BytesIO(split["lewis"])).convert("RGB")
    clark = Image.open(io.BytesIO(split["clark"])).convert("RGB")
    for img, ch_idx in ((lewis, 0), (clark, 2)):
        top = img.getpixel((img.width // 2, max(1, int(img.height * 0.12))))[ch_idx]
        low = img.getpixel((img.width // 2, int(img.height * 0.60)))[ch_idx]
        assert top >= low


def test_two_shot_detail_prefers_outer_peaks_over_center_gap() -> None:
    """Gap variance between figures must not become the right-speaker face x."""
    from pipeline.conversation_scene_anchor import _two_shot_detail_face_centers

    img = Image.new("RGB", (768, 256), (40, 40, 40))
    # Same proportions as a narrow 1× master: faces near quarter / three-quarter width.
    for cx in (196, 664):
        for dx in range(-18, 19):
            for dy in range(30, 90):
                x = cx + dx
                if 0 <= x < 768:
                    img.putpixel((x, dy), (220, 180, 140))
    for x in range(340, 428):
        for y in range(20, 95):
            img.putpixel((x, y), (240, 245, 250))

    pair = _two_shot_detail_face_centers(img)
    assert pair is not None
    (x_lo, _), (x_hi, _) = pair
    assert x_lo < 280
    assert x_hi > 520


def test_two_shot_detail_keeps_centered_left_face() -> None:
    """Strong mid-left face must win over a weaker peak near the expected 28% column.

    Regression for 18040624: Wan placed Lewis near mid-left; old target-column
    bias locked onto empty river detail and clipped his face.
    """
    from pipeline.conversation_scene_anchor import _two_shot_detail_face_centers

    w, h = 1280, 640
    img = Image.new("RGB", (w, h), (40, 40, 40))

    def _face_blob(cx: int, half_w: int, y0: int, y1: int, color: tuple[int, int, int]) -> None:
        for dx in range(-half_w, half_w + 1):
            for dy in range(y0, y1):
                x = cx + dx
                if 0 <= x < w:
                    img.putpixel((x, dy), color)

    # Weak shoulder/hat detail near expected left quarter (~0.28w).
    _face_blob(360, 10, 40, 100, (90, 100, 110))
    # Strong left face in the middle third (Lewis-like placement).
    _face_blob(560, 24, 35, 120, (220, 180, 140))
    # Right speaker near three-quarter width.
    _face_blob(820, 24, 35, 120, (200, 160, 120))

    pair = _two_shot_detail_face_centers(img)
    assert pair is not None
    (x_lo, _), (x_hi, _) = pair
    assert 480 <= x_lo <= 620, f"left face x={x_lo} drifted toward empty quarter column"
    assert 760 <= x_hi <= 900, f"right face x={x_hi}"


def test_reframe_crop_for_anchor_produces_target_size() -> None:
    crop = Image.new("RGB", (120, 200), (100, 120, 80))
    out = reframe_crop_for_anchor(crop, target_w=720, target_h=1280)
    assert out.size == (720, 1280)


def test_composite_i2i_prompt_avoids_one_person_suffix() -> None:
    from video_vendors.fal import _sanitize_fal_prompt
    from video_vendors.fal_scene_anchor_i2i import (
        _i2i_composite_human_anatomy_suffix,
        build_scene_anchor_i2i_prompt,
    )

    prompt = build_scene_anchor_i2i_prompt(
        prompt_without_markers_sanitized="River camp scene.",
        character_id="lewis_clark",
        segment_index=1,
        opening_frames=["Both captains on the bank."],
        world_prefix_for_i2i="Early 1800s Missouri.",
        aggressive=False,
        sanitize=_sanitize_fal_prompt,
    )
    assert "two distinct people" in prompt.lower()
    assert "one person" not in prompt.lower()
    assert "two distinct people in frame" in _i2i_composite_human_anatomy_suffix().lower()


def test_reframe_crop_top_align_preserves_upper_band() -> None:
    crop = Image.new("RGB", (100, 200))
    for y in range(200):
        color = (255, 0, 0) if y < 50 else (0, 0, 0)
        for x in range(100):
            crop.putpixel((x, y), color)
    top_out = reframe_crop_for_anchor(crop, target_w=100, target_h=100, vertical_align="top")
    center_out = reframe_crop_for_anchor(crop, target_w=100, target_h=100, vertical_align="center")
    assert top_out.getpixel((50, 10))[0] > 200
    assert center_out.getpixel((50, 10))[0] < 50


def test_reframe_crop_bottom_align_preserves_lower_band() -> None:
    crop = Image.new("RGB", (100, 200))
    for y in range(200):
        # Top 50px = black, bottom 50px = red.
        color = (0, 0, 0) if y < 100 else (255, 0, 0)
        for x in range(100):
            crop.putpixel((x, y), color)

    out = reframe_crop_for_anchor(crop, target_w=100, target_h=100, vertical_align="bottom")
    assert out.getpixel((50, 10))[0] > 200


def test_reframe_crop_fit_creates_pillarbox_padding() -> None:
    # Narrow crop: fit preserves full height but cannot fill the target width.
    crop = Image.new("RGB", (288, 1280), (100, 100, 100))
    out_fit = reframe_crop_for_anchor(crop, target_w=720, target_h=1280, scale_mode="fit")
    # Left edge should be the pillarbox background (32,32,32).
    assert out_fit.getpixel((10, 10)) == (32, 32, 32)

    out_cover = reframe_crop_for_anchor(
        crop, target_w=720, target_h=1280, scale_mode="cover", vertical_align="bottom"
    )
    # Cover should fill the whole width (no pillarbox bars).
    assert out_cover.getpixel((10, 10)) != (32, 32, 32)


def test_conversation_scene_anchor_strategy_from_config() -> None:
    from pipeline.conversation_scene_anchor import (
        conversation_scene_anchor_strategy,
        conversation_uses_shared_master_bookend,
    )

    assert conversation_scene_anchor_strategy() == "shared_master_bookend"
    assert conversation_uses_shared_master_bookend() is True


def test_anchor_preview_skips_per_segment_when_conversation_run(
    tmp_path: Path,
) -> None:
    """build_scene_anchors_batch should not call per-segment i2i for conv segments."""
    repo = tmp_path
    did = "18030101"
    narr_dir = repo / "narrations"
    narr_dir.mkdir(parents=True)
    script = [
        {
            "segment_index": 1,
            "narration": "intro",
            "video_prompt": "river",
            "visual_mode": "b_roll",
        },
        {
            "segment_index": 2,
            "narration": "setup",
            "video_prompt": "camp wide",
            "visual_mode": "b_roll",
        },
        {
            "segment_index": 3,
            "narration": "line",
            "video_prompt": "camp",
            "visual_mode": "talking_head",
            "talking_head_subject": "lewis",
            "reference_character_id": "lewis",
        },
        {
            "segment_index": 4,
            "narration": "line",
            "video_prompt": "camp",
            "visual_mode": "talking_head",
            "talking_head_subject": "clark",
            "reference_character_id": "clark",
        },
    ]
    (narr_dir / f"narration{did}.json").write_text(
        json.dumps(
            {
                "narration_version": "2.0",
                "long_conversation_mode": True,
                "conversation_micro_arc": {
                    "shared_setting": "Camp.",
                    "speakers": ["lewis", "clark"],
                    "segment_indices": [3, 4],
                },
                "narration_script": script,
                "scene_spine": {"core_location": "River camp"},
            }
        ),
        encoding="utf-8",
    )
    portraits = repo / "character-portraits"
    portraits.mkdir()
    (portraits / "lewis.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (portraits / "clark.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (portraits / "lewis_clark.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    movie = repo / "movie-images" / did / "anchors"
    movie.mkdir(parents=True)
    (movie / "03_lewis.png").write_bytes(b"ok")
    (movie / "04_clark.png").write_bytes(b"ok")
    (movie / "_conv_03_lewis_clark_master.png").write_bytes(b"master")

    with (
        patch(
            "pipeline.conversation_scene_anchor.conversation_uses_shared_conversation_master",
            return_value=True,
        ),
        patch(
            "pipeline.conversation_scene_anchor.conversation_uses_shared_master_mask",
            return_value=False,
        ),
        patch("video_vendors.fal_scene_anchor_i2i.run_scene_anchor_i2i_to_disk") as mock_i2i,
    ):
        from pipeline.anchor_preview import build_scene_anchors_batch

        report = build_scene_anchors_batch(did, repo_root=repo, skip_existing=True)
        mock_i2i.assert_not_called()
    actions = {r.segment_index: r.action for r in report.anchor_build}
    assert actions.get(3) == "skipped_existing"
    assert actions.get(4) == "skipped_existing"
