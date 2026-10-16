from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from monitor_desktop.app import MonitorWindow
from monitor_desktop.backends import CameraTelemetry
from monitor_desktop.idle import IdleTracker


class FakeCapture:
    """Minimal capture stub delivering one fixed frame per read."""

    def __init__(self, frame: np.ndarray) -> None:
        self._frame = frame
        self.released = False
        self.release_count = 0

    def read(self) -> tuple[bool, np.ndarray | None]:
        return True, self._frame.copy()

    def release(self) -> None:
        self.released = True
        self.release_count += 1


class FakeVirtualCameraOutput:
    def __init__(self) -> None:
        self.active = False
        self.device = "Test Virtual Camera"

    def start(self, frame: np.ndarray, device: str | None) -> str:
        self.active = True
        return self.device

    def send(self, frame: np.ndarray) -> None:
        pass

    def stop(self) -> None:
        self.active = False


class FakeWriter:
    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True

    def write(self, frame: np.ndarray) -> None:
        pass


class FakeGPhotoBackend:
    name = "gphoto2 USB"

    def __init__(self, telemetry: CameraTelemetry | None = None, raise_on_read: bool = False) -> None:
        self.telemetry = telemetry or CameraTelemetry()
        self.raise_on_read = raise_on_read

    def read_telemetry(self) -> CameraTelemetry:
        if self.raise_on_read:
            raise RuntimeError("transport busy")
        return self.telemetry

    def disconnect(self) -> None:
        pass


class IdleStopWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, directory: str) -> MonitorWindow:
        base = Path(directory)
        return MonitorWindow(base / "camera-presets.json", base / "virtual-camera.json", base / "idle-stop.json")

    def _frame(self) -> np.ndarray:
        return np.full((24, 32, 3), 42, dtype=np.uint8)

    def _idle_window(self, directory: str) -> tuple[MonitorWindow, list[float]]:
        window = self._window(directory)
        now = [time.monotonic()]
        window.idle_tracker = IdleTracker(clock=lambda: now[0])
        window.idle_prefs = {"enabled": True, "timeout_minutes": 2, "warn_seconds": 60}
        window._set_capture(FakeCapture(self._frame()), "Video connected")
        return window, now

    def _notices(self, window: MonitorWindow) -> list[str]:
        notices: list[str] = []
        original = window._notify

        def record(message: str, error: bool = False) -> None:
            notices.append(message)
            original(message, error=error)

        window._notify = record  # type: ignore[method-assign]
        return notices

    def test_disabled_by_default_never_releases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            self.assertFalse(window.idle_prefs["enabled"])
            now = [time.monotonic()]
            window.idle_tracker = IdleTracker(clock=lambda: now[0])
            capture = FakeCapture(self._frame())
            window._set_capture(capture, "Video connected")
            now[0] += 10 * 600.0
            window._check_idle()
            self.assertIs(window.capture, capture)
            self.assertFalse(capture.released)
            self.assertFalse(window._idle_paused)
            window.close()

    def test_enabled_idle_pauses_stream_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window, now = self._idle_window(directory)
            notices = self._notices(window)
            capture = window.capture
            assert isinstance(capture, FakeCapture)
            now[0] += 200.0
            window._check_idle()
            self.assertIsNone(window.capture)
            self.assertTrue(capture.released)
            self.assertEqual(capture.release_count, 1)
            self.assertTrue(window._idle_paused)
            self.assertIsNotNone(window.preview_surface.pixmap())
            self.assertTrue(any("paused" in notice.casefold() for notice in notices))
            paused_notices = len(notices)
            window._check_idle()
            self.assertEqual(capture.release_count, 1)
            self.assertEqual(len(notices), paused_notices)
            window.close()

    def test_virtual_camera_supersedes_idle_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window, now = self._idle_window(directory)
            fake = FakeVirtualCameraOutput()
            fake.active = True
            window.virtual_camera = fake  # type: ignore[assignment]
            capture = window.capture
            now[0] += 10 * 120.0
            window._check_idle()
            self.assertIs(window.capture, capture)
            self.assertFalse(window._idle_paused)
            window.close()

    def test_camera_recording_inhibits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window, now = self._idle_window(directory)
            window.camera_recording = True
            capture = window.capture
            now[0] += 600.0
            window._check_idle()
            self.assertIs(window.capture, capture)
            self.assertFalse(window._idle_paused)
            window.close()

    def test_monitor_recording_inhibits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window, now = self._idle_window(directory)
            writer = FakeWriter()
            window.writer = writer  # type: ignore[assignment]
            capture = window.capture
            now[0] += 600.0
            window._check_idle()
            self.assertIs(window.capture, capture)
            self.assertFalse(writer.released)
            self.assertFalse(window._idle_paused)
            window.writer = None  # type: ignore[assignment]
            window.close()

    def test_pending_command_inhibits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window, now = self._idle_window(directory)
            window._pending_camera_commands = 1
            capture = window.capture
            now[0] += 600.0
            window._check_idle()
            self.assertIs(window.capture, capture)
            self.assertFalse(window._idle_paused)
            window.close()

    def test_warn_precedes_stop_with_single_notice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window, now = self._idle_window(directory)
            notices = self._notices(window)
            now[0] += 70.0
            window._check_idle()
            self.assertTrue(window._idle_warned)
            self.assertIsNotNone(window.capture)
            self.assertEqual(len(notices), 1)
            now[0] += 20.0
            window._check_idle()
            self.assertTrue(window._idle_warned)
            self.assertIsNotNone(window.capture)
            self.assertEqual(len(notices), 1)
            now[0] += 60.0
            window._check_idle()
            self.assertIsNone(window.capture)
            self.assertTrue(window._idle_paused)
            window.close()

    def test_activity_after_pause_resumes_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window, now = self._idle_window(directory)
            notices = self._notices(window)
            window._idle_resume = {"kind": "source", "value": "0", "index": 0}

            def fake_connect_source() -> None:
                window._set_capture(FakeCapture(self._frame()), "Video connected")

            window.connect_source = fake_connect_source  # type: ignore[method-assign]
            now[0] += 200.0
            window._check_idle()
            self.assertTrue(window._idle_paused)
            self.assertIsNone(window.capture)
            window._record_activity()
            self.assertFalse(window._idle_paused)
            self.assertIsNotNone(window.capture)
            self.assertTrue(any("resumed" in notice.casefold() for notice in notices))
            self.assertNotEqual(window.connection_label.text(), "NO SOURCE")
            window.close()

    def test_manual_disconnect_while_paused_does_not_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window, now = self._idle_window(directory)
            now[0] += 200.0
            window._check_idle()
            self.assertTrue(window._idle_paused)
            window.disconnect_source()
            self.assertIsNone(window.capture)
            window._record_activity()
            self.assertIsNone(window.capture)
            self.assertFalse(window._idle_paused)
            window.close()

    def test_thermal_poll_updates_label_from_battery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            window._set_capture(FakeCapture(self._frame()), "Video connected")
            window.active_backend = FakeGPhotoBackend(CameraTelemetry(battery_raw="80"))  # type: ignore[assignment]
            window._poll_telemetry()
            self.assertIsNotNone(window._thermal_reading)
            self.assertEqual(window._thermal_reading.battery_percent, 80)
            for label in (window.preview_camera_status, window.camera_connection_status):
                self.assertIn("80%", label.text())
                self.assertIn("Batt", label.text())
                self.assertIn("80%", label.toolTip())
            window.close()

    def test_thermal_poll_without_battery_shows_no_battery_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            window._set_capture(FakeCapture(self._frame()), "Video connected")
            window.active_backend = FakeGPhotoBackend(CameraTelemetry())  # type: ignore[assignment]
            window._poll_telemetry()
            self.assertIsNotNone(window._thermal_reading)
            self.assertIsNone(window._thermal_reading.battery_percent)
            for label in (window.preview_camera_status, window.camera_connection_status):
                self.assertNotIn("Batt", label.text())
                self.assertNotIn("%", label.text())
            window.close()

    def test_thermal_poll_with_no_stream_leaves_labels_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            window.active_backend = FakeGPhotoBackend(CameraTelemetry(battery_raw="80"))  # type: ignore[assignment]
            self.assertIsNone(window.capture)
            window.preview_camera_status.setText("Connected via gphoto2 USB")
            window.camera_connection_status.setText("Connected via gphoto2 USB")
            window._thermal_reading = None
            window._poll_telemetry()
            self.assertEqual(window.preview_camera_status.text(), "Connected via gphoto2 USB")
            self.assertEqual(window.camera_connection_status.text(), "Connected via gphoto2 USB")
            self.assertIsNone(window._thermal_reading)
            window.close()

    def test_child_widget_click_resets_idle_through_app_filter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window, now = self._idle_window(directory)
            window.show()
            self.app.processEvents()
            now[0] += 200.0
            self.assertGreater(window.idle_tracker.idle_seconds(), 100.0)
            QTest.mouseClick(window.preview_tools_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertLess(window.idle_tracker.idle_seconds(), 5.0)
            window._check_idle()
            self.assertIsNotNone(window.capture)
            self.assertFalse(window._idle_paused)
            window.close()

    def test_failed_resume_does_not_resurrect_released_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window, now = self._idle_window(directory)
            capture = window.capture
            assert isinstance(capture, FakeCapture)
            now[0] += 200.0
            window._check_idle()
            self.assertTrue(window._idle_paused)
            self.assertIsNone(window.capture)
            self.assertTrue(capture.released)
            released_obj = capture
            window.active_backend = None
            seen: list[tuple[str, bool]] = []
            original = window._notify

            def record(message: str, error: bool = False) -> None:
                seen.append((message, error))
                original(message, error=error)

            window._notify = record  # type: ignore[method-assign]
            window._record_activity()
            self.assertIsNone(window.capture)
            self.assertIsNot(window.capture, released_obj)
            self.assertFalse(window._idle_paused)
            self.assertTrue(any("reconnect" in message.casefold() for message, _ in seen))
            self.assertTrue(any(error for _, error in seen))
            window.close()

    def test_thermal_edge_notifies_once_per_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            window._set_capture(FakeCapture(self._frame()), "Video connected")
            window.active_backend = FakeGPhotoBackend(CameraTelemetry(battery_raw="80"))  # type: ignore[assignment]
            window._stream_started_at = time.monotonic() - 25 * 60.0
            notices = self._notices(window)
            window._poll_telemetry()
            self.assertEqual(window._thermal_reading.state, "caution")
            caution_notices = len(notices)
            self.assertEqual(caution_notices, 1)
            window._poll_telemetry()
            window._poll_telemetry()
            self.assertEqual(len(notices), caution_notices)
            window._stream_started_at = time.monotonic()
            window._poll_telemetry()
            self.assertEqual(len(notices), caution_notices + 1)
            self.assertIn("normal", notices[-1].casefold())
            window._poll_telemetry()
            self.assertEqual(len(notices), caution_notices + 1)
            window.close()

    def test_thermal_read_failure_leaves_streaming_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            window.active_backend = FakeGPhotoBackend(raise_on_read=True)  # type: ignore[assignment]
            capture = FakeCapture(self._frame())
            window._set_capture(capture, "Video connected")
            window._poll_telemetry()
            self.assertIs(window.capture, capture)
            self.assertFalse(capture.released)
            window.close()


if __name__ == "__main__":
    unittest.main()
