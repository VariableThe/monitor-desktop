"""Idle-stop decision logic for viewfindrr.

Streaming pause is opt-in and off by default. This module holds the pure,
UI-free part: an activity tracker with an injectable clock and a total
function mapping idleness plus inhibition gates to a stop decision.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

DEFAULT_IDLE_TIMEOUT_MINUTES = 10
MIN_IDLE_TIMEOUT_MINUTES = 2
MAX_IDLE_TIMEOUT_MINUTES = 120
DEFAULT_WARN_SECONDS = 60


class IdleTracker:
    """Track user activity as idle seconds since the last mark."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last_activity = clock()

    def mark_activity(self, now: float | None = None) -> None:
        self._last_activity = now if now is not None else self._clock()

    def idle_seconds(self, now: float | None = None) -> float:
        current = now if now is not None else self._clock()
        return max(0.0, current - self._last_activity)

    def reset(self, now: float | None = None) -> None:
        self.mark_activity(now)


@dataclass(frozen=True)
class StopDecision:
    action: str
    reason: str


def should_stop(
    *,
    enabled: bool,
    idle_seconds: float,
    timeout_seconds: float,
    warn_seconds: float,
    virtual_camera_active: bool,
    camera_recording: bool,
    monitor_recording: bool,
    pending_commands: int,
    resumable: bool,
) -> StopDecision:
    """Decide whether an idle stream should warn, stop, or keep running."""
    if not enabled:
        return StopDecision("none", "disabled")
    if virtual_camera_active:
        return StopDecision("none", "virtual_camera_active")
    if camera_recording:
        return StopDecision("none", "camera_recording")
    if monitor_recording:
        return StopDecision("none", "monitor_recording")
    if pending_commands > 0:
        return StopDecision("none", "command_in_flight")
    if not resumable:
        return StopDecision("none", "not_resumable")
    remaining = timeout_seconds - idle_seconds
    if remaining <= 0:
        return StopDecision("stop", "idle_timeout")
    if remaining <= warn_seconds:
        return StopDecision("warn", "idle_warning")
    return StopDecision("none", "active")
