"""Calendar-phase river-travel cues for expedition videogen prompts."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from pipeline.journey_river_context import (
    expedition_route_hint_for_date_id,
    expedition_route_hint_for_journal_date,
    parse_journal_date_id,
    resolved_expedition_route_hint,
)
from pipeline.narration_phase2 import build_phase2_user_prompt, merge_phase1_phase2
from video_vendors import build_prompts, opening_period_line_for_narration_segment


class TestJourneyRiverContext(unittest.TestCase):
    def test_parse_journal_date_id(self) -> None:
        self.assertEqual(parse_journal_date_id("18040525"), date(1804, 5, 25))
        self.assertIsNone(parse_journal_date_id("1804"))
        self.assertIsNone(parse_journal_date_id(""))

    def test_phases_missouri_upstream_and_return_downstream_tone(self) -> None:
        h_may1804 = expedition_route_hint_for_journal_date(date(1804, 5, 25))
        self.assertIn("upstream", h_may1804.lower())
        self.assertIn("against", h_may1804.lower())
        self.assertIn("cutting", h_may1804.lower())
        self.assertIn("slowly", h_may1804.lower())

        h_prep = expedition_route_hint_for_journal_date(date(1804, 5, 10))
        self.assertIn("staging", h_prep.lower())
        self.assertIn("cutting", h_prep.lower())

        h_sep1805 = expedition_route_hint_for_journal_date(date(1805, 9, 9))
        self.assertIn("divide", h_sep1805.lower())

        h_coast = expedition_route_hint_for_journal_date(date(1805, 12, 7))
        self.assertIn("pacific", h_coast.lower())

        h_return = expedition_route_hint_for_journal_date(date(1806, 9, 20))
        self.assertIn("downstream", h_return.lower())
        self.assertIn("missouri main stem", h_return.lower())

    def test_suppress_via_episode_metadata(self) -> None:
        meta = {"suppress_expedition_route_hint": True}
        self.assertEqual(resolved_expedition_route_hint("18040525", meta), "")
        bare = expedition_route_hint_for_date_id("18040525")
        self.assertTrue(bare)
        self.assertEqual(
            resolved_expedition_route_hint("18040525", None),
            bare,
        )


def test_build_prompts_omits_calendar_route_hint(tmp_path: Path) -> None:
    """Route attitude is Phase 2 only; vendors must not inject journey_river_context on every clip."""
    narration = {
        "scene_spine": {"core_location": "Camp Dubois", "visual_mood": "grounded"},
        "narration_script": [
            {
                "segment_index": 1,
                "stage_direction": "static",
                "video_prompt": "Rats in underbrush; no boats.",
            },
        ],
    }
    narrations_dir = tmp_path / "narrations"
    narrations_dir.mkdir(parents=True, exist_ok=True)
    (narrations_dir / "narration18040525.json").write_text(json.dumps(narration), encoding="utf-8")
    prompts = build_prompts("18040525", narrations_dir=narrations_dir)
    assert len(prompts) == 1
    low = prompts[0].lower()
    assert "outbound missouri-system travel" not in low
    assert "cordelle" not in low
    assert "rowers stay" not in low
    assert "rats in underbrush" in low


def test_opening_period_line_matches_build_prompts_opening_shard(tmp_path: Path) -> None:
    narration = {
        "date_id": "18040525",
        "scene_spine": {"core_location": "La Charrette", "visual_mood": "grounded"},
        "narration_script": [
            {"segment_index": 1, "stage_direction": "static", "video_prompt": "River."},
        ],
    }
    narrations_dir = tmp_path / "narrations"
    narrations_dir.mkdir(parents=True, exist_ok=True)
    (narrations_dir / "narration18040525.json").write_text(json.dumps(narration), encoding="utf-8")
    pr = build_prompts("18040525", narrations_dir=narrations_dir)[0]
    world = opening_period_line_for_narration_segment(narration, 1)
    idx = pr.find(world.strip())
    assert idx >= 0, f"opening not found in vendor prompt ({world[:80]}…)"


def test_build_phase2_user_route_attitude_lewis_pack_only() -> None:
    phase1 = {
        "segments": [{"narration": "We ascended the Missouri."}],
        "episode_metadata": {"location_summary": "Missouri River"},
    }
    u_lc = build_phase2_user_prompt(phase1, "18040525", prompt_pack="lewis_clark")
    assert "ROUTE ATTITUDE" in u_lc
    assert "18040525" in u_lc

    u_gen = build_phase2_user_prompt(phase1, "18040525", prompt_pack="generic_documentary")
    assert "ROUTE ATTITUDE" not in u_gen


def test_merge_includes_date_id() -> None:
    phase1 = {
        "title": "T",
        "segments": [{"narration": "One beat only here."}],
        "episode_metadata": {"location_summary": "River"},
    }
    from tests.phase2_fixtures import episode_visual_world_for_segments

    phase2 = {
        "episode_visual_world": episode_visual_world_for_segments(1),
        "scene_plan": [
            {
                "segment_index": 1,
                "visual_strategy": {
                    "primary_visual": "A",
                    "secondary_elements": "B",
                    "motion_style": "static",
                    "pacing_note": "brief",
                },
            }
        ],
        "open_questions": [],
        "editor_notes": "",
    }
    merged, _counts = merge_phase1_phase2(phase1, phase2, date_id="18040525")
    assert merged.get("date_id") == "18040525"


if __name__ == "__main__":
    unittest.main()
