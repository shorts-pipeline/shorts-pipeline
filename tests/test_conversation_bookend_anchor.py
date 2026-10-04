"""Tests for shared_master_bookend conversation anchors."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pipeline.conversation_bookend_anchor import (
    bookend_segment_indices,
    conversation_segment_is_bookend,
    ensure_conversation_run_bookend_anchors,
    existing_bookend_anchor_for_segment,
    middle_segment_indices,
)
from pipeline.conversation_scene_anchor import (
    ConversationAnchorRun,
    conversation_scene_anchor_strategy,
    conversation_uses_shared_master_bookend,
)


def _run(
    *,
    segments: tuple[int, ...] = (3, 4, 5, 6),
) -> ConversationAnchorRun:
    return ConversationAnchorRun(
        first_segment_index=segments[0],
        segment_indices=segments,
        composite_id="lewis_clark",
        left_speaker_id="lewis",
        right_speaker_id="clark",
        shared_setting="campfire",
    )


def test_bookend_indices_four_turn_run() -> None:
    run = _run()
    assert bookend_segment_indices(run) == (3, 6)
    assert middle_segment_indices(run) == (4, 5)
    assert conversation_segment_is_bookend(run, 3) is True
    assert conversation_segment_is_bookend(run, 6) is True
    assert conversation_segment_is_bookend(run, 4) is False


def test_two_segment_run_both_bookends() -> None:
    run = _run(segments=(3, 4))
    assert bookend_segment_indices(run) == (3, 4)
    assert middle_segment_indices(run) == ()
    assert conversation_segment_is_bookend(run, 3) is True
    assert conversation_segment_is_bookend(run, 4) is True


@patch("pipeline.conversation_scene_anchor._conversation_scene_anchor_config")
def test_strategy_shared_master_bookend(mock_cfg) -> None:
    mock_cfg.return_value = {
        "enabled": True,
        "strategy": "shared_master_bookend",
    }
    assert conversation_scene_anchor_strategy() == "shared_master_bookend"
    assert conversation_uses_shared_master_bookend() is True


def test_ensure_bookend_writes_omni_masks_and_middle_crops(tmp_path: Path) -> None:
    repo = tmp_path
    did = "18030101"
    out = repo / "movie-images" / did
    run = _run()
    narr = {
        "narration_script": [
            {
                "segment_index": 3,
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
            },
            {
                "segment_index": 4,
                "visual_mode": "talking_head",
                "talking_head_subject": "clark",
            },
            {
                "segment_index": 5,
                "visual_mode": "talking_head",
                "talking_head_subject": "lewis",
            },
            {
                "segment_index": 6,
                "visual_mode": "talking_head",
                "talking_head_subject": "clark",
            },
        ]
    }
    wide = Image.new("RGB", (1440, 1280), (50, 48, 46))
    buf = io.BytesIO()
    wide.save(buf, format="PNG")
    master_bytes = buf.getvalue()

    with patch(
        "pipeline.conversation_scene_anchor._run_conversation_master_scene_anchor_i2i"
    ) as mock_i2i:
        master_path = out / "anchors" / "_conv_03_lewis_clark_master.png"
        master_path.parent.mkdir(parents=True, exist_ok=True)
        master_path.write_bytes(master_bytes)
        mock_i2i.return_value = master_path

        derived = ensure_conversation_run_bookend_anchors(
            repo_root=repo,
            output_dir=out,
            run=run,
            narr=narr,
            prompts=[],
            openings=[],
            core_overrides=[],
            aspect_ratio="9:16",
            skip_existing=False,
            force=True,
        )

    omni = out / "anchors" / "_conv_03_lewis_clark_omni.png"
    assert omni.is_file()
    assert derived[3] == omni
    assert derived[6] == omni
    assert (out / "anchors" / "03_lewis_mask.png").is_file()
    assert (out / "anchors" / "06_clark_mask.png").is_file()
    assert derived[4].name == "04_clark.png"
    assert derived[5].name == "05_lewis.png"
    assert (out / "anchors" / "04_clark_mask.png").exists() is False

    manifest = json.loads((out / "anchors" / "_conv_03_lewis_clark_manifest.json").read_text())
    assert manifest["anchor_mode"] == "bookend"
    assert manifest["bookend_segment_indices"] == [3, 6]
    assert manifest["middle_segment_indices"] == [4, 5]

    assert existing_bookend_anchor_for_segment(out, 4, "clark") == derived[4]
    assert existing_bookend_anchor_for_segment(out, 3, "lewis") == omni
