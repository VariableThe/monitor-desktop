"""Unit tests for the background telemetry poller."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from monitor_desktop.app import MonitorWindow
from monitor_desktop.backends import CameraTelemetry
from monitor_desktop.telemetry_poller import TelemetryPoller


class FakeBackend:
    """Fake camera backend with a controllable read."""

    def __init__(
        self,
        telemetry: CameraTelemetry | None = None,
        gate: threading.Event | None = None,
        raise_on_read: bool = False,
    ) -> None:
        self.telemetry = telemetry or CameraTelemetry()
        self._gate = gate
        self._raise_on_read = raise_on_read
        self.read_count = 0
        self.read_idents: list[int] = []
        self.read_threads: list[QThread] = []

    def read_telemetry(self) -> CameraTelemetry:
        self.read_count += 1
        self.read_idents.append(threading.get_ident())
        qthread = QThread.currentThread()
        if qthread is not None:
            self.read_threads.append(qthread)
        if self._gate is not None:
            self._gate.wait(timeout=10.0)
        if self._raise_on_read:
            raise RuntimeError("transport busy")
        return self.telemetry


class FakeCapture:
    def __init__(self, frame: np.ndarray) -> None:
        self._frame = frame
        self.released = False

    def read(self) -> tuple[bool, np.ndarray | None]:
        return True, self._frame.copy()

    def release(self) -> None:
        self.released = True


def _pump_until(predicate: object, timeout: float = 5.0) -> bool:
    # NOTE: wait via the real event loop (QTest.qWait) instead of a hot
    # processEvents() loop. A hot processEvents() loop while the background
    # telemetry worker delivers a result aborts CPython 3.11 (PySide6 6.12
    # refcount corruption, exit 134); the application runs a real event
    # loop, so this matches production.
    app = QApplication.instance()
    assert app is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        QTest.qWait(5)
    return bool(predicate())  # type: ignore[operator]


class TelemetryPollerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.processEvents()

    def _poller(self) -> TelemetryPoller:
        poller = TelemetryPoller()
        self.addCleanup(poller.shutdown)
        return poller

    def test_read_runs_off_gui_thread(self) -> None:
        poller = self._poller()
        received: list[CameraTelemetry] = []
        poller.readingReady.connect(received.append)
        backend = FakeBackend(CameraTelemetry(battery_raw="80"))
        gui_ident = threading.get_ident()
        gui_thread = QThread.currentThread()
        self.assertTrue(poller.request(backend))
        self.assertTrue(_pump_until(lambda: len(received) == 1))
        self.assertEqual(backend.read_count, 1)
        self.assertEqual(len(backend.read_idents), 1)
        self.assertNotEqual(backend.read_idents[0], gui_ident)
        self.assertIsNotNone(gui_thread)
        for worker in backend.read_threads:
            self.assertIsNot(worker, gui_thread)
        self.assertEqual(received[0].battery_raw, "80")
        self.assertFalse(poller.busy)

    def test_single_flight_coalesces_overlapping_requests(self) -> None:
        poller = self._poller()
        received: list[CameraTelemetry] = []
        poller.readingReady.connect(received.append)
        gate = threading.Event()
        backend = FakeBackend(CameraTelemetry(battery_raw="80"), gate=gate)
        self.assertTrue(poller.request(backend))
        self.assertTrue(_pump_until(lambda: poller.busy))
        self.assertTrue(poller.busy)
        self.assertFalse(poller.request(backend))
        self.assertEqual(backend.read_count, 1)
        gate.set()
        self.assertTrue(_pump_until(lambda: len(received) == 1))
        self.assertEqual(backend.read_count, 1)
        self.assertTrue(_pump_until(lambda: not poller.busy))
        self.assertTrue(poller.request(backend))
        self.assertTrue(_pump_until(lambda: len(received) == 2))

    def test_request_none_emits_nothing(self) -> None:
        poller = self._poller()
        received: list[CameraTelemetry] = []
        poller.readingReady.connect(received.append)
        self.assertFalse(poller.request(None))
        self.assertFalse(poller.busy)
        self.app.processEvents()
        self.assertEqual(received, [])

    def test_raising_read_delivers_empty_telemetry(self) -> None:
        poller = self._poller()
        received: list[CameraTelemetry] = []
        poller.readingReady.connect(received.append)
        self.assertTrue(poller.request(FakeBackend(raise_on_read=True)))
        self.assertTrue(_pump_until(lambda: len(received) == 1))
        delivered = received[0]
        self.assertIsInstance(delivered, CameraTelemetry)
        self.assertIsNone(delivered.temperature_raw)
        self.assertIsNone(delivered.overheat_raw)
        self.assertIsNone(delivered.battery_raw)

    def test_shutdown_while_busy_returns_bounded_and_delivers_nothing(self) -> None:
        poller = TelemetryPoller()
        received: list[CameraTelemetry] = []
        poller.readingReady.connect(received.append)
        gate = threading.Event()
        backend = FakeBackend(CameraTelemetry(battery_raw="80"), gate=gate)
        self.assertTrue(poller.request(backend))
        self.assertTrue(_pump_until(lambda: poller.busy))
        started = time.monotonic()
        poller.shutdown(timeout_ms=100)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0)
        self.assertFalse(poller.request(backend))
        gate.set()
        # NOTE: real event-loop wait (see _pump_until); must deliver nothing.
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertEqual(received, [])

    def test_window_updates_label_from_delivered_reading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            window = MonitorWindow(base / "camera-presets.json", base / "virtual-camera.json", base / "idle-stop.json")
            try:
                window._set_capture(FakeCapture(np.full((24, 32, 3), 42, dtype=np.uint8)), "Video connected")
                window.active_backend = FakeBackend(CameraTelemetry(battery_raw="80"))  # type: ignore[assignment]
                window._poll_telemetry()
                self.assertTrue(_pump_until(lambda: window._thermal_reading is not None))
                self.assertEqual(window._thermal_reading.battery_percent, 80)
                for label in (window.preview_camera_status, window.camera_connection_status):
                    self.assertIn("80%", label.text())
                    self.assertIn("Batt", label.text())
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
