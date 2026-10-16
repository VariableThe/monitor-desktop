from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from monitor_desktop.app import load_idle_stop_prefs, save_idle_stop_prefs
from monitor_desktop.idle import DEFAULT_IDLE_TIMEOUT_MINUTES, DEFAULT_WARN_SECONDS


class IdlePrefsTests(unittest.TestCase):
    def _path(self, directory: str) -> Path:
        return Path(directory) / "idle-stop.json"

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._path(directory)
            save_idle_stop_prefs(path, {"enabled": True, "timeout_minutes": 25, "warn_seconds": 30})
            self.assertEqual(
                load_idle_stop_prefs(path),
                {"enabled": True, "timeout_minutes": 25, "warn_seconds": 30},
            )

    def test_missing_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                load_idle_stop_prefs(self._path(directory)),
                {"enabled": False, "timeout_minutes": DEFAULT_IDLE_TIMEOUT_MINUTES, "warn_seconds": DEFAULT_WARN_SECONDS},
            )

    def test_corrupt_json_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._path(directory)
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(
                load_idle_stop_prefs(path),
                {"enabled": False, "timeout_minutes": DEFAULT_IDLE_TIMEOUT_MINUTES, "warn_seconds": DEFAULT_WARN_SECONDS},
            )
            path.write_text("[1, 2, 3]", encoding="utf-8")
            self.assertEqual(
                load_idle_stop_prefs(path),
                {"enabled": False, "timeout_minutes": DEFAULT_IDLE_TIMEOUT_MINUTES, "warn_seconds": DEFAULT_WARN_SECONDS},
            )

    def test_partial_values_keep_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._path(directory)
            path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
            self.assertEqual(
                load_idle_stop_prefs(path),
                {"enabled": True, "timeout_minutes": DEFAULT_IDLE_TIMEOUT_MINUTES, "warn_seconds": DEFAULT_WARN_SECONDS},
            )

    def test_string_true_does_not_enable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._path(directory)
            path.write_text(json.dumps({"enabled": "true", "timeout_minutes": 10, "warn_seconds": 60}), encoding="utf-8")
            self.assertFalse(load_idle_stop_prefs(path)["enabled"])
            path.write_text(json.dumps({"enabled": 1, "timeout_minutes": 10, "warn_seconds": 60}), encoding="utf-8")
            self.assertFalse(load_idle_stop_prefs(path)["enabled"])

    def test_bool_timeout_falls_back_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._path(directory)
            path.write_text(json.dumps({"enabled": True, "timeout_minutes": True, "warn_seconds": False}), encoding="utf-8")
            loaded = load_idle_stop_prefs(path)
            self.assertEqual(loaded["timeout_minutes"], DEFAULT_IDLE_TIMEOUT_MINUTES)
            self.assertEqual(loaded["warn_seconds"], DEFAULT_WARN_SECONDS)

    def test_clamping_both_directions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._path(directory)
            path.write_text(json.dumps({"enabled": True, "timeout_minutes": 1, "warn_seconds": -5}), encoding="utf-8")
            loaded = load_idle_stop_prefs(path)
            self.assertEqual(loaded["timeout_minutes"], DEFAULT_IDLE_TIMEOUT_MINUTES)
            self.assertEqual(loaded["warn_seconds"], DEFAULT_WARN_SECONDS)
            path.write_text(json.dumps({"enabled": True, "timeout_minutes": 500, "warn_seconds": 999}), encoding="utf-8")
            loaded = load_idle_stop_prefs(path)
            self.assertEqual(loaded["timeout_minutes"], DEFAULT_IDLE_TIMEOUT_MINUTES)
            self.assertEqual(loaded["warn_seconds"], DEFAULT_WARN_SECONDS)

    def test_unknown_keys_ignored_and_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._path(directory)
            path.write_text(
                json.dumps({"enabled": True, "timeout_minutes": 15, "warn_seconds": 20, "mystery": "kept-out"}),
                encoding="utf-8",
            )
            self.assertEqual(
                load_idle_stop_prefs(path),
                {"enabled": True, "timeout_minutes": 15, "warn_seconds": 20},
            )
            save_idle_stop_prefs(path, load_idle_stop_prefs(path))
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("mystery", stored)
            self.assertEqual(stored["version"], 1)

    def test_no_tmp_left_behind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._path(directory)
            save_idle_stop_prefs(path, {"enabled": False, "timeout_minutes": 10, "warn_seconds": 60})
            leftovers = list(Path(directory).glob("*.tmp"))
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
