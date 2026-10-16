from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from monitor_desktop.idle import (
    DEFAULT_IDLE_TIMEOUT_MINUTES,
    DEFAULT_WARN_SECONDS,
    MAX_IDLE_TIMEOUT_MINUTES,
    MIN_IDLE_TIMEOUT_MINUTES,
    IdleTracker,
    StopDecision,
    should_stop,
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def decide(**overrides):
    params = {
        "enabled": True,
        "idle_seconds": 0.0,
        "timeout_seconds": 600.0,
        "warn_seconds": 60.0,
        "virtual_camera_active": False,
        "camera_recording": False,
        "monitor_recording": False,
        "pending_commands": 0,
        "resumable": True,
    }
    params.update(overrides)
    return should_stop(**params)


class IdleTrackerTests(unittest.TestCase):
    def test_starts_active_with_zero_idle(self) -> None:
        clock = FakeClock()
        tracker = IdleTracker(clock=clock)
        self.assertEqual(tracker.idle_seconds(), 0.0)

    def test_idle_grows_monotonically(self) -> None:
        clock = FakeClock(500.0)
        tracker = IdleTracker(clock=clock)
        clock.now = 560.0
        self.assertAlmostEqual(tracker.idle_seconds(), 60.0)
        clock.now = 590.0
        self.assertAlmostEqual(tracker.idle_seconds(), 90.0)

    def test_mark_activity_resets_idle(self) -> None:
        clock = FakeClock(200.0)
        tracker = IdleTracker(clock=clock)
        clock.now = 260.0
        self.assertAlmostEqual(tracker.idle_seconds(), 60.0)
        tracker.mark_activity()
        self.assertEqual(tracker.idle_seconds(), 0.0)

    def test_reset_marks_activity(self) -> None:
        clock = FakeClock(300.0)
        tracker = IdleTracker(clock=clock)
        clock.now = 400.0
        tracker.reset()
        self.assertEqual(tracker.idle_seconds(), 0.0)

    def test_idle_never_negative(self) -> None:
        clock = FakeClock(700.0)
        tracker = IdleTracker(clock=clock)
        tracker.mark_activity(now=800.0)
        self.assertEqual(tracker.idle_seconds(now=750.0), 0.0)
        self.assertEqual(IdleTracker(clock=FakeClock(100.0)).idle_seconds(now=50.0), 0.0)

    def test_explicit_now_marks(self) -> None:
        tracker = IdleTracker(clock=FakeClock(0.0))
        tracker.mark_activity(now=40.0)
        self.assertAlmostEqual(tracker.idle_seconds(now=55.0), 15.0)


class ShouldStopTests(unittest.TestCase):
    def test_disabled_never_stops(self) -> None:
        decision = decide(enabled=False, idle_seconds=10 * 3600.0)
        self.assertEqual(decision, StopDecision("none", "disabled"))

    def test_virtual_camera_supersedes_huge_idle(self) -> None:
        decision = decide(virtual_camera_active=True, idle_seconds=6000.0, timeout_seconds=600.0)
        self.assertEqual(decision, StopDecision("none", "virtual_camera_active"))

    def test_camera_recording_inhibits(self) -> None:
        decision = decide(camera_recording=True, idle_seconds=6000.0)
        self.assertEqual(decision, StopDecision("none", "camera_recording"))

    def test_monitor_recording_inhibits(self) -> None:
        decision = decide(monitor_recording=True, idle_seconds=6000.0)
        self.assertEqual(decision, StopDecision("none", "monitor_recording"))

    def test_pending_command_inhibits(self) -> None:
        decision = decide(pending_commands=1, idle_seconds=6000.0)
        self.assertEqual(decision, StopDecision("none", "command_in_flight"))

    def test_non_resumable_inhibits(self) -> None:
        decision = decide(resumable=False, idle_seconds=6000.0)
        self.assertEqual(decision, StopDecision("none", "not_resumable"))

    def test_inhibit_order_beats_idle(self) -> None:
        # Every gate beats any amount of idle time, in documented order.
        self.assertEqual(decide(enabled=False, virtual_camera_active=True).reason, "disabled")
        self.assertEqual(decide(virtual_camera_active=True, camera_recording=True).reason, "virtual_camera_active")
        self.assertEqual(decide(camera_recording=True, monitor_recording=True).reason, "camera_recording")
        self.assertEqual(decide(monitor_recording=True, pending_commands=2).reason, "monitor_recording")
        self.assertEqual(decide(pending_commands=2, resumable=False).reason, "command_in_flight")

    def test_warn_window_then_stop(self) -> None:
        self.assertEqual(
            decide(idle_seconds=100.0, timeout_seconds=600.0, warn_seconds=60.0),
            StopDecision("none", "active"),
        )
        self.assertEqual(
            decide(idle_seconds=550.0, timeout_seconds=600.0, warn_seconds=60.0),
            StopDecision("warn", "idle_warning"),
        )
        self.assertEqual(
            decide(idle_seconds=700.0, timeout_seconds=600.0, warn_seconds=60.0),
            StopDecision("stop", "idle_timeout"),
        )

    def test_exact_boundary_stops(self) -> None:
        decision = decide(idle_seconds=600.0, timeout_seconds=600.0, warn_seconds=60.0)
        self.assertEqual(decision, StopDecision("stop", "idle_timeout"))

    def test_zero_warn_seconds_skips_warning(self) -> None:
        self.assertEqual(
            decide(idle_seconds=599.0, timeout_seconds=600.0, warn_seconds=0.0),
            StopDecision("none", "active"),
        )
        self.assertEqual(
            decide(idle_seconds=600.0, timeout_seconds=600.0, warn_seconds=0.0),
            StopDecision("stop", "idle_timeout"),
        )

    def test_constants(self) -> None:
        self.assertEqual(DEFAULT_IDLE_TIMEOUT_MINUTES, 10)
        self.assertEqual(MIN_IDLE_TIMEOUT_MINUTES, 2)
        self.assertEqual(MAX_IDLE_TIMEOUT_MINUTES, 120)
        self.assertEqual(DEFAULT_WARN_SECONDS, 60)


if __name__ == "__main__":
    unittest.main()
