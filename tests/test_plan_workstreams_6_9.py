"""Tests for plan workstreams 6–9: prompt lineage, shot library, run report, FAL arg helper."""

from __future__ import annotations

import json
import unittest
import unittest.mock as mock
from pathlib import Path

from pipeline.narration_phase2 import merge_phase1_phase2
from pipeline.prompt_pack_metadata import (
    build_prompt_pack_lineage,
    fingerprint_prompt_pack,
    read_pack_manifest,
)
from pipeline.run_report import ClipReportRow, RunReportDocument, write_run_report
from pipeline.talking_head_shots import resolve_shot_snippet
from video_vendors.fal_avatar import _subscribe_args_for_talking_head


def _vs(primary: str) -> dict:
    return {
        "primary_visual": primary,
        "secondary_elements": "",
        "motion_style": "static",
        "pacing_note": "brief",
        "opening_frame": "",
    }


class MergeShotSnippetTests(unittest.TestCase):
    def test_talking_head_archetype_merged_into_prompt(self) -> None:
        root = Path(__file__).resolve().parent.parent
        phase1 = {
            "segments": [
                {"visual_mode": "b_roll", "narration": "a", "dialogue": []},
                {"visual_mode": "b_roll", "narration": "b", "dialogue": []},
                {
                    "visual_mode": "talking_head",
                    "talking_head_subject": "lewis",
                    "talking_head_archetype": "orders",
                    "talking_head_prompt": "Hold still before speaking.",
                    "narration": "c",
                    "dialogue": [{"speaker_id": "lewis", "text": "March."}],
                },
            ],
        }
        phase2 = {
            "scene_plan": [
                {"segment_index": 1, "visual_strategy": _vs("river")},
                {"segment_index": 2, "visual_strategy": _vs("camp")},
                {"segment_index": 3, "visual_strategy": _vs("lewis portrait")},
            ],
            "video_metadata": {},
            "map_insertions": [],
        }
        with mock.patch(
            "pipeline.narration_phase2.character_portrait_asset_exists",
            return_value=True,
        ):
            merged, _ = merge_phase1_phase2(phase1, phase2, dialogue_mode=True, repo_root=root)
        thp = merged["narration_script"][2].get("talking_head_prompt") or ""
        self.assertIn("Hold still", thp)
        self.assertIn("eye-line", thp.lower())


class PromptPackMetadataTests(unittest.TestCase):
    def test_read_manifest_default(self) -> None:
        root = Path(__file__).resolve().parent.parent
        m = read_pack_manifest(root, "nonexistent_pack_xyz")
        self.assertEqual(m["version"], "unversioned")

    def test_fingerprint_stable(self) -> None:
        root = Path(__file__).resolve().parent.parent
        fp = fingerprint_prompt_pack(root, "lewis_clark")
        self.assertEqual(len(fp), 64)
        self.assertEqual(fp, fingerprint_prompt_pack(root, "lewis_clark"))

    def test_lineage_shape(self) -> None:
        root = Path(__file__).resolve().parent.parent
        lin = build_prompt_pack_lineage(
            root, phase1_pack="lewis_clark", phase2_pack="lewis_clark_dialogue"
        )
        self.assertIn("generated_at_utc", lin)
        self.assertEqual(lin["phase1"]["pack_id"], "lewis_clark")
        self.assertEqual(lin["phase2"]["pack_id"], "lewis_clark_dialogue")

    def test_lineage_phase1_dialogue_when_requested(self) -> None:
        root = Path(__file__).resolve().parent.parent
        lin = build_prompt_pack_lineage(
            root,
            phase1_pack="lewis_clark_dialogue",
            phase2_pack="lewis_clark_dialogue",
            phase1_dialogue_pack="lewis_clark_dialogue",
        )
        self.assertIn("phase1_dialogue", lin)
        self.assertEqual(lin["phase1_dialogue"]["pack_id"], "lewis_clark_dialogue")
        self.assertEqual(len(lin["phase1_dialogue"]["fingerprint_sha256"]), 64)


class TalkingHeadShotsTests(unittest.TestCase):
    def tearDown(self) -> None:
        from pipeline import talking_head_shots as ths

        ths._load_raw.cache_clear()

    def test_archetype_orders(self) -> None:
        root = Path(__file__).resolve().parent.parent
        seg = {"talking_head_archetype": "orders", "talking_head_prompt": "x"}
        snip = resolve_shot_snippet(root, seg)
        self.assertIsNotNone(snip)
        self.assertIn("eye-line", snip.lower())


class RunReportTests(unittest.TestCase):
    def test_write_and_roundtrip(self) -> None:
        tmp = Path(__file__).resolve().parent / "_tmp_run_report_test"
        if tmp.exists():
            for c in tmp.glob("*"):
                c.unlink()
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            doc = RunReportDocument(date_id="18040101", vendor="fal")
            doc.b_roll_rows.append(
                ClipReportRow(
                    segment=1, kind="b_roll", action="generated", detail="01.mp4", model="fal_wan"
                )
            )
            p = write_run_report(tmp, doc)
            self.assertTrue(p.is_file())
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["date_id"], "18040101")
            self.assertEqual(len(data["b_roll"]), 1)
        finally:
            if tmp.exists():
                for c in tmp.glob("*"):
                    c.unlink()
                tmp.rmdir()


class FalSubscribeArgsTests(unittest.TestCase):
    def test_sadtalker_keys(self) -> None:
        d = _subscribe_args_for_talking_head(
            "fal-ai/sadtalker", image_url="https://i", audio_url="https://a"
        )
        self.assertIn("source_image_url", d)
        self.assertIn("still_mode", d)
        self.assertIs(d["still_mode"], True)

    def test_sadtalker_still_mode_false(self) -> None:
        d = _subscribe_args_for_talking_head(
            "fal-ai/sadtalker",
            image_url="https://i",
            audio_url="https://a",
            sadtalker_still_mode=False,
        )
        self.assertIs(d["still_mode"], False)

    def test_omnihuman_keys(self) -> None:
        d = _subscribe_args_for_talking_head(
            "fal-ai/bytedance/omnihuman", image_url="https://i", audio_url="https://a"
        )
        self.assertIn("image_url", d)
        self.assertIn("audio_url", d)

    def test_omnihuman_v15_mask_and_prompt(self) -> None:
        d = _subscribe_args_for_talking_head(
            "fal-ai/bytedance/omnihuman/v1.5",
            image_url="https://i",
            audio_url="https://a",
            prompt="subtle nod",
            mask_url="https://mask",
        )
        self.assertEqual(d["mask_url"], "https://mask")
        self.assertEqual(d["prompt"], "subtle nod")

    def test_heygen_avatar4_keys(self) -> None:
        d = _subscribe_args_for_talking_head(
            "fal-ai/heygen/avatar4/image-to-video",
            image_url="https://i",
            audio_url="https://a",
            aspect_ratio="9:16",
        )
        self.assertEqual(d["image_url"], "https://i")
        self.assertEqual(d["audio_url"], "https://a")
        self.assertEqual(d["talking_style"], "stable")
        self.assertEqual(d["aspect_ratio"], "9:16")
        self.assertEqual(d["resolution"], "720p")
        self.assertNotIn("prompt", d)

    def test_heygen_avatar4_landscape_aspect(self) -> None:
        d = _subscribe_args_for_talking_head(
            "fal-ai/heygen/avatar4/image-to-video",
            image_url="https://i",
            audio_url="https://a",
            aspect_ratio="16:9",
        )
        self.assertEqual(d["aspect_ratio"], "16:9")


if __name__ == "__main__":
    unittest.main()
