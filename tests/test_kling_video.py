"""Kling (FAL) video argument builders."""

from __future__ import annotations

import unittest

from video_vendors.kling import (
    KLING_AVATAR_PRO_MODEL,
    kling_avatar_arguments,
    kling_broll_duration_limits,
    kling_duration_seconds,
    kling_i2v_arguments,
    kling_t2v_arguments,
    resolve_broll_engine,
    resolve_broll_route_engines,
)


class KlingVideoTests(unittest.TestCase):
    def test_resolve_broll_engine(self) -> None:
        self.assertEqual(resolve_broll_engine(None), "wan")
        self.assertEqual(resolve_broll_engine("kling"), "kling-v3-standard")
        self.assertEqual(resolve_broll_engine("kling-v3-standard"), "kling-v3-standard")

    def test_resolve_broll_route_engines_hybrid(self) -> None:
        i2v, t2v = resolve_broll_route_engines(
            {
                "fal_broll_engine": "kling-v3-standard",
                "fal_broll_i2v_engine": "kling-v3-standard",
                "fal_broll_t2v_engine": "wan",
            }
        )
        self.assertEqual(i2v, "kling-v3-standard")
        self.assertEqual(t2v, "wan")

    def test_resolve_broll_route_engines_legacy_single_key(self) -> None:
        i2v, t2v = resolve_broll_route_engines({"fal_broll_engine": "kling-v3-standard"})
        self.assertEqual(i2v, "kling-v3-standard")
        self.assertEqual(t2v, "kling-v3-standard")

    def test_resolve_broll_route_engines_cli_override(self) -> None:
        i2v, t2v = resolve_broll_route_engines(
            {"fal_broll_i2v_engine": "wan", "fal_broll_t2v_engine": "wan"},
            fal_broll_i2v_engine="kling-v3-standard",
        )
        self.assertEqual(i2v, "kling-v3-standard")
        self.assertEqual(t2v, "wan")

    def test_kling_duration_bounds(self) -> None:
        self.assertEqual(kling_duration_seconds(1.0), "3")
        self.assertEqual(kling_duration_seconds(12.432), "12")
        self.assertEqual(kling_duration_seconds(12.6), "13")
        self.assertEqual(kling_duration_seconds(99.0), "15")

    def test_kling_broll_duration_limits_from_config(self) -> None:
        lo, hi = kling_broll_duration_limits(
            {"fal_kling_broll_min_seconds": 4, "fal_kling_broll_max_seconds": 14}
        )
        self.assertEqual((lo, hi), (4.0, 14.0))
        self.assertEqual(
            kling_duration_seconds(8.4, min_seconds=lo, max_seconds=hi),
            "8",
        )

    def test_kling_i2v_arguments(self) -> None:
        endpoint, args = kling_i2v_arguments(
            prompt="River camp at dusk.",
            start_image_url="https://example.com/a.png",
            duration_seconds=12.2,
            aspect_ratio="9:16",
            negative_prompt="text on screen",
        )
        self.assertIn("kling-video/v3/standard/image-to-video", endpoint)
        self.assertEqual(args["start_image_url"], "https://example.com/a.png")
        self.assertEqual(args["duration"], "12")
        self.assertFalse(args["generate_audio"])
        self.assertEqual(args["aspect_ratio"], "9:16")

    def test_kling_t2v_arguments(self) -> None:
        _endpoint, args = kling_t2v_arguments(
            prompt="Fog on the Missouri.",
            duration_seconds=6.0,
            aspect_ratio="16:9",
        )
        self.assertFalse(args["generate_audio"])
        self.assertEqual(args["aspect_ratio"], "16:9")

    def test_kling_avatar_arguments(self) -> None:
        args = kling_avatar_arguments(
            image_url="https://example.com/lewis.png",
            audio_url="https://example.com/04.mp3",
            prompt="Thoughtful delivery.",
        )
        self.assertEqual(args["image_url"], "https://example.com/lewis.png")
        self.assertEqual(args["audio_url"], "https://example.com/04.mp3")
        self.assertEqual(args["prompt"], "Thoughtful delivery.")
        self.assertEqual(KLING_AVATAR_PRO_MODEL, "fal-ai/kling-video/ai-avatar/v2/pro")


if __name__ == "__main__":
    unittest.main()
