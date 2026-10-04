"""Tests for prompt_packs loading (Phase 2 template, shared fragments)."""

from __future__ import annotations

import unittest
from pathlib import Path

from pipeline.narration_phase2 import build_phase2_system_prompt
from pipeline.prompt_pack_text import load_phase2_system_template, load_shared_prompt_text

_REPO = Path(__file__).resolve().parent.parent


class TestPromptPacks(unittest.TestCase):
    def test_phase2_template_loads_and_validates(self) -> None:
        text = load_phase2_system_template(_REPO, "lewis_clark")
        self.assertIn("__ANCHOR_HINTS_BLOCK__", text)
        self.assertIn("{focus_tone_relax}", text)
        self.assertIn("Hunting / game", text)
        self.assertIn("active hunt", text)
        self.assertIn("venison steak", text)
        self.assertIn("episode_visual_world", text)
        self.assertIn("visual_blocks", text)
        self.assertIn("SILENT `video_prompt`", text)

    def test_phase1_ambient_sound_in_narration(self) -> None:
        p1 = (_REPO / "prompt_packs" / "lewis_clark" / "phase1_system.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("AMBIENT SOUND IN NARRATION", p1)

    def test_long_conversation_phase2_template_loads(self) -> None:
        text = load_phase2_system_template(_REPO, "lewis_clark_long_conversation")
        self.assertIn("PHASE 2 RULES", text)
        self.assertIn("__RUNTIME_MAP_POLICY_BLOCK__", text)

    def test_phase2_fallback_pack(self) -> None:
        text = load_phase2_system_template(_REPO, "nonexistent_pack_xyz")
        self.assertIn("PHASE 2 RULES", text)

    def test_build_phase2_system_prompt_substitutions(self) -> None:
        s = build_phase2_system_prompt(
            repo_root=_REPO,
            prompt_pack="lewis_clark",
            character_anchor_hints=None,
        )
        self.assertNotIn("__ANCHOR_HINTS_BLOCK__", s)
        self.assertNotIn("__AMBIENT_TAG_ENUM__", s)
        self.assertNotIn("__RUNTIME_MAP_POLICY_BLOCK__", s)
        self.assertIn("ambient_tag", s)
        self.assertIn("chirping_birds", s)

    def test_build_phase2_system_prompt_anchor_boat_guardrails(self) -> None:
        s = build_phase2_system_prompt(
            repo_root=_REPO,
            prompt_pack="lewis_clark",
            character_anchor_hints=["clark", None, None],
        )
        self.assertIn("Portrait anchor + boats", s)
        self.assertIn("blade-at-waterline-beside-hull", s)
        self.assertIn("Keelboat / barge", load_phase2_system_template(_REPO, "lewis_clark"))

    def test_shared_video_fragments(self) -> None:
        a = load_shared_prompt_text(_REPO, "video_safety_suffix.txt")
        b = load_shared_prompt_text(_REPO, "video_no_on_screen_text_prefix.txt")
        self.assertIn("Family-friendly", a)
        self.assertIn("readable text", b.lower())


if __name__ == "__main__":
    unittest.main()
