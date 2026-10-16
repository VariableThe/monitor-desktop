"""Background camera-telemetry reads for the monitor window.

The 60 s telemetry timer used to call ``backend.read_telemetry()`` directly
on the GUI thread. While live view streams, that read waits on the camera
lock the capture thread holds (up to ~377 ms cold on a Sony ZV-E10), which
stalls frames. This module moves the read to a single worker thread while
all Qt widget updates stay on the GUI thread.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from .backends import CameraTelemetry

_DRAIN_INTERVAL_MS = 25


class TelemetryPoller(QObject):
    """Run at most one ``read_telemetry`` call off the GUI thread."""

    readingReady = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="telemetry")
        self._future: Future[Any] | None = None
        self._shutdown = False
        self._drain = QTimer(self)
        self._drain.setInterval(_DRAIN_INTERVAL_MS)
        self._drain.timeout.connect(self._drain_finished)

    @property
    def busy(self) -> bool:
        """True while a background read is in flight."""
        with self._lock:
            return self._future is not None and not self._future.done()

    def request(self, backend: Any | None) -> bool:
        """Start one background read unless there is nothing to do."""
        if backend is None:
            return False
        read = getattr(backend, "read_telemetry", None)
        if read is None:
            return False
        with self._lock:
            if self._shutdown:
                return False
            if self._future is not None and not self._future.done():
                return False
            self._future = self._executor.submit(self._read, read)
        if not self._drain.isActive():
            self._drain.start(_DRAIN_INTERVAL_MS)
        return True

    def shutdown(self, timeout_ms: int = 1000) -> None:
        """Stop accepting work and drop any late result without hanging."""
        with self._lock:
            self._shutdown = True
            future = self._future
        self._drain.stop()
        if future is not None and not future.done():
            try:
                future.result(timeout=max(0, timeout_ms) / 1000.0)
            except Exception:
                pass
        with self._lock:
            self._future = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _read(read: Any) -> CameraTelemetry:
        try:
            telemetry = read()
        except Exception:
            return CameraTelemetry()
        if telemetry is None or not isinstance(telemetry, CameraTelemetry):
            return CameraTelemetry()
        return telemetry

    def _drain_finished(self) -> None:
        with self._lock:
            future = self._future
            shutdown = self._shutdown
        if future is None or not future.done():
            return
        with self._lock:
            self._future = None
        if shutdown:
            self._drain.stop()
            return
        try:
            telemetry = future.result()
        except Exception:
            telemetry = CameraTelemetry()
        if telemetry is None or not isinstance(telemetry, CameraTelemetry):
            telemetry = CameraTelemetry()
        self._drain.stop()
        self.readingReady.emit(telemetry)
