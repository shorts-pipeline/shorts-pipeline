"""SadTalking preprocess fallback detection."""

from __future__ import annotations

import unittest
from pathlib import Path

from video_vendors.fal_avatar import (
    _model_supports_mask_url,
    _parse_cropdetect_spec,
    _probe_letterbox_crop,
    _sadtalker_preprocess_fallback_worthy,
    _subscribe_args_for_talking_head,
)


class SadtalkerPreprocessFallbackTests(unittest.TestCase):
    def test_face_detection_error(self) -> None:
        self.assertTrue(
            _sadtalker_preprocess_fallback_worthy(RuntimeError("422: face_detection_error"))
        )

    def test_no_face_message(self) -> None:
        self.assertTrue(
            _sadtalker_preprocess_fallback_worthy(Exception("No face detected in image"))
        )

    def test_content_policy_not_fallback(self) -> None:
        self.assertFalse(
            _sadtalker_preprocess_fallback_worthy(
                RuntimeError("content_policy_violation face_detection")
            )
        )

    def test_unrelated_422(self) -> None:
        self.assertFalse(_sadtalker_preprocess_fallback_worthy(RuntimeError("422 invalid JSON")))


class LetterboxCropTests(unittest.TestCase):
    def test_parse_cropdetect_spec(self) -> None:
        self.assertEqual(_parse_cropdetect_spec("720:1200:0:40"), (720, 1200, 0, 40))
        self.assertIsNone(_parse_cropdetect_spec("bad"))

    def test_probe_kling_spike_clip(self) -> None:
        clip = Path("movie-images/18040614/04_kling.mp4")
        if not clip.is_file():
            self.skipTest("spike clip not present")
        crop = _probe_letterbox_crop(clip, frame_width=720, frame_height=1280)
        self.assertEqual(crop, "720:1200:0:40")


class MaskUrlModelSupportTests(unittest.TestCase):
    def test_omnihuman_v1_5_supports_mask(self) -> None:
        self.assertTrue(_model_supports_mask_url("fal-ai/bytedance/omnihuman/v1.5"))

    def test_omnihuman_v1_0_does_not_support_mask(self) -> None:
        self.assertFalse(_model_supports_mask_url("fal-ai/bytedance/omnihuman/v1.0"))

    def test_heygen_avatar_does_not_support_mask(self) -> None:
        self.assertFalse(_model_supports_mask_url("fal-ai/heygen/avatar4/image-to-video"))

    def test_sadtalker_does_not_support_mask(self) -> None:
        self.assertFalse(_model_supports_mask_url("fal-ai/sadtalker"))

    def test_heygen_args_drop_mask_url(self) -> None:
        args = _subscribe_args_for_talking_head(
            "fal-ai/heygen/avatar4/image-to-video",
            image_url="https://example.com/img.png",
            audio_url="https://example.com/audio.mp3",
            mask_url="https://example.com/mask.png",
        )
        self.assertNotIn("mask_url", args)

    def test_omnihuman_v1_5_args_forward_mask_url(self) -> None:
        args = _subscribe_args_for_talking_head(
            "fal-ai/bytedance/omnihuman/v1.5",
            image_url="https://example.com/img.png",
            audio_url="https://example.com/audio.mp3",
            mask_url="https://example.com/mask.png",
        )
        self.assertEqual(args.get("mask_url"), "https://example.com/mask.png")


if __name__ == "__main__":
    unittest.main()
