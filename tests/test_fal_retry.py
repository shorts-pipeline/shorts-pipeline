"""Tests for FAL subscribe / HTTP retry helpers."""

from __future__ import annotations

import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

from video_vendors.fal_retry import (
    fal_subscribe_with_retries,
    http_stream_to_file_with_retries,
    is_transient_fal_subscribe_error,
    is_transient_http_error,
)


class FalRetryTests(unittest.TestCase):
    def test_transient_rate_limit(self) -> None:
        self.assertTrue(
            is_transient_fal_subscribe_error(RuntimeError("HTTP 429 Too Many Requests"))
        )

    def test_transient_503(self) -> None:
        self.assertTrue(is_transient_fal_subscribe_error(Exception("503 Service Unavailable")))

    def test_not_transient_content_policy(self) -> None:
        self.assertFalse(
            is_transient_fal_subscribe_error(
                RuntimeError("content_policy_violation flagged by checker")
            )
        )

    def test_not_transient_face_detection_422(self) -> None:
        self.assertFalse(
            is_transient_fal_subscribe_error(
                RuntimeError("422 validation failed: face_detection_error")
            )
        )

    @mock.patch("video_vendors.fal_retry.time.sleep", autospec=True)
    def test_subscribe_retries_then_ok(self, _sleep: mock.MagicMock) -> None:
        fn = mock.Mock(side_effect=[RuntimeError("503 unavailable"), {"ok": True}])
        out = fal_subscribe_with_retries("unit", fn, max_attempts=4, base_delay=0.01)
        self.assertEqual(out, {"ok": True})
        self.assertEqual(fn.call_count, 2)

    @mock.patch("video_vendors.fal_retry.time.sleep", autospec=True)
    def test_subscribe_exhausts_raises(self, _sleep: mock.MagicMock) -> None:
        fn = mock.Mock(side_effect=RuntimeError("503 still down"))
        with self.assertRaises(RuntimeError):
            fal_subscribe_with_retries("unit", fn, max_attempts=2, base_delay=0.01)
        self.assertEqual(fn.call_count, 2)

    def test_http_transient_timeout_string(self) -> None:
        self.assertTrue(is_transient_http_error(Exception("Read timed out")))

    @mock.patch("video_vendors.fal_retry.requests.get")
    @mock.patch("video_vendors.fal_retry.time.sleep", autospec=True)
    def test_stream_download_retries(self, _sleep: mock.MagicMock, m_get: mock.MagicMock) -> None:
        bad = mock.Mock()
        bad.raise_for_status.side_effect = OSError("connection reset by peer")
        good = mock.Mock()
        good.raise_for_status.return_value = None
        good.iter_content.return_value = [b"ab", b"c"]
        m_get.side_effect = [bad, good]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "t.mp4"
            http_stream_to_file_with_retries(
                "http://example.invalid/x",
                dest,
                label="test-dl",
                max_attempts=3,
                base_delay=0.01,
            )
            self.assertEqual(dest.read_bytes(), b"abc")


if __name__ == "__main__":
    unittest.main()
