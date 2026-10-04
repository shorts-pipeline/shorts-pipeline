"""Tests for Phase 2 runtime mid-episode map policy (dry streak + travel heuristic)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from pipeline.map_display_policy import (
    compute_phase2_runtime_map_policy_block,
    consecutive_prior_episodes_without_mid_map,
    phase1_implies_travel_or_relocation,
)


def _v2_no_map() -> dict:
    return {"narration_version": "2.0", "map_insertions": []}


def _v2_with_overlay(seg: int = 3) -> dict:
    return {
        "narration_version": "2.0",
        "map_insertions": [
            {
                "placement": "after_segment_index",
                "segment_index": seg,
                "visual_style": "parchment_overlay",
                "duration_seconds": 4,
            }
        ],
    }


class TestMapDisplayPolicy(unittest.TestCase):
    def test_consecutive_prior_without_map_counts_newest_backward(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for did in ("18040510", "18040511", "18040512"):
                (d / f"narration{did}.json").write_text(json.dumps(_v2_no_map()), encoding="utf-8")
            self.assertEqual(consecutive_prior_episodes_without_mid_map(d, "18040513"), 3)

    def test_consecutive_stops_when_mid_map_found(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            # Newest prior must lack map; older episode breaks streak when it has overlay.
            (d / "narration18040512.json").write_text(json.dumps(_v2_no_map()), encoding="utf-8")
            (d / "narration18040511.json").write_text(
                json.dumps(_v2_with_overlay()), encoding="utf-8"
            )
            self.assertEqual(consecutive_prior_episodes_without_mid_map(d, "18040513"), 1)

    def test_phase1_navigation_lens_is_travel(self) -> None:
        p1 = {
            "episode_metadata": {"primary_lens": "navigation"},
            "segments": [{"narration": "Routine camp chores."}],
        }
        self.assertTrue(phase1_implies_travel_or_relocation(p1))

    def test_phase1_narrative_energy_zero_is_travel(self) -> None:
        p1 = {
            "episode_metadata": {"primary_lens": "environmental", "narrative_energy": 0},
            "segments": [{"narration": "Quiet evening."}],
        }
        self.assertTrue(phase1_implies_travel_or_relocation(p1))

    def test_runtime_block_when_threshold_met_and_travel(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for did in ("18040510", "18040511"):
                (d / f"narration{did}.json").write_text(json.dumps(_v2_no_map()), encoding="utf-8")
            p1 = {
                "episode_metadata": {"primary_lens": "navigation"},
                "segments": [{"narration": "They marched upstream."}],
            }
            os.environ["LEWISCLARK_MAP_DRY_SPELL_THRESHOLD"] = "2"
            try:
                block, log = compute_phase2_runtime_map_policy_block(p1, "18040512", d)
                self.assertIn("RUNTIME MAP POLICY NUDGE", block)
                self.assertIn("prior_without_mid_map=2", log)
            finally:
                os.environ.pop("LEWISCLARK_MAP_DRY_SPELL_THRESHOLD", None)

    def test_runtime_block_skipped_when_not_travel_heuristic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for did in ("18040510", "18040511", "18040512", "18040513"):
                (d / f"narration{did}.json").write_text(json.dumps(_v2_no_map()), encoding="utf-8")
            p1 = {
                "episode_metadata": {
                    "primary_lens": "cultural",
                    "narrative_energy": 4,
                },
                "segments": [{"narration": "The council exchanged gifts at the lodge."}],
            }
            os.environ["LEWISCLARK_MAP_DRY_SPELL_THRESHOLD"] = "2"
            try:
                block, log = compute_phase2_runtime_map_policy_block(p1, "18040514", d)
                self.assertEqual(block, "")
                self.assertEqual(log, "")
            finally:
                os.environ.pop("LEWISCLARK_MAP_DRY_SPELL_THRESHOLD", None)


if __name__ == "__main__":
    unittest.main()
