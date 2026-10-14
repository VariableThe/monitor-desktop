"""Preferences round-trip and fallback tests for the automatic virtual camera."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from monitor_desktop.app import load_virtual_camera_prefs, save_virtual_camera_prefs


class VirtualCameraPrefsTests(unittest.TestCase):
    def test_round_trip_preserves_mode_feed_and_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "virtual-camera.json"
            save_virtual_camera_prefs(path, {"mode": "auto", "feed": 1, "device_override": "/dev/video10"})
            self.assertEqual(
                load_virtual_camera_prefs(path),
                {"mode": "auto", "feed": 1, "device_override": "/dev/video10"},
            )

    def test_missing_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                load_virtual_camera_prefs(Path(directory) / "virtual-camera.json"),
                {"mode": "off", "feed": 0, "device_override": ""},
            )

    def test_corrupt_json_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "virtual-camera.json"
            path.write_text("{not valid json", encoding="utf-8")
            self.assertEqual(
                load_virtual_camera_prefs(path),
                {"mode": "off", "feed": 0, "device_override": ""},
            )

    def test_partial_and_invalid_values_are_clamped_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "virtual-camera.json"
            path.write_text('{"mode": "auto"}', encoding="utf-8")
            self.assertEqual(
                load_virtual_camera_prefs(path),
                {"mode": "auto", "feed": 0, "device_override": ""},
            )
            path.write_text('{"mode": "sometimes", "feed": 7, "device_override": 42}', encoding="utf-8")
            self.assertEqual(
                load_virtual_camera_prefs(path),
                {"mode": "off", "feed": 0, "device_override": ""},
            )
            path.write_text('{"mode": "auto", "feed": true, "device_override": "  /dev/video10  "}', encoding="utf-8")
            self.assertEqual(
                load_virtual_camera_prefs(path),
                {"mode": "auto", "feed": 0, "device_override": "/dev/video10"},
            )


if __name__ == "__main__":
    unittest.main()
