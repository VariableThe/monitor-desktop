"""Unit tests for read-only camera telemetry probing."""

from __future__ import annotations

import unittest
from typing import Any

from monitor_desktop.backends import (
    CameraTelemetry,
    GPhotoBackend,
    SonyRemoteApiBackend,
    SonySdkServerBackend,
)


class FakeWidget:
    def __init__(self, name: str, value: Any = None, raises: bool = False) -> None:
        self._name = name
        self._value = value
        self._raises = raises

    def get_name(self) -> str:
        return self._name

    def get_value(self) -> Any:
        if self._raises:
            raise RuntimeError("widget read failed")
        return self._value


class FakeConfig:
    def __init__(self, widgets: dict[str, FakeWidget]) -> None:
        self._widgets = dict(widgets)

    def get_child_by_name(self, name: str) -> FakeWidget | None:
        return self._widgets.get(name)

    def count_children(self) -> int:
        return len(self._widgets)

    def get_child(self, index: int) -> FakeWidget:
        return list(self._widgets.values())[index]


class FakeCamera:
    def __init__(self, config: FakeConfig) -> None:
        self._config = config
        self.get_config_calls = 0
        self.get_single_config_calls: list[str] = []

    def get_config(self) -> FakeConfig:
        self.get_config_calls += 1
        return self._config

    def get_single_config(self, name: str) -> FakeWidget:
        self.get_single_config_calls.append(name)
        widget = self._config.get_child_by_name(name)
        if widget is None:
            raise RuntimeError(f"no such widget: {name}")
        return widget


class RaisingConfigCamera(FakeCamera):
    """Fails loudly on any full config fetch, like the ~500 ms cost on hardware."""

    def get_config(self) -> FakeConfig:
        self.get_config_calls += 1
        raise AssertionError("poll path must not fetch the full config")


def _backend_with(widgets: dict[str, FakeWidget]) -> GPhotoBackend:
    backend = GPhotoBackend()
    backend.camera = FakeCamera(FakeConfig(widgets))  # type: ignore[assignment]
    return backend


def _backend_with_raising_config(widgets: dict[str, FakeWidget]) -> tuple[GPhotoBackend, RaisingConfigCamera]:
    backend = GPhotoBackend()
    camera = RaisingConfigCamera(FakeConfig(widgets))
    backend.camera = camera  # type: ignore[assignment]
    return backend, camera


class CameraTelemetryTests(unittest.TestCase):
    def test_empty_config_reports_unavailable(self) -> None:
        telemetry = _backend_with({}).read_telemetry()

        self.assertIsInstance(telemetry, CameraTelemetry)
        self.assertFalse(telemetry.available)
        self.assertIsNone(telemetry.temperature_raw)
        self.assertIsNone(telemetry.overheat_raw)
        self.assertIsNone(telemetry.battery_raw)

    def test_battery_only_reports_battery(self) -> None:
        telemetry = _backend_with({"batterylevel": FakeWidget("batterylevel", "80")}).read_telemetry()

        self.assertTrue(telemetry.available)
        self.assertEqual(telemetry.battery_raw, "80")
        self.assertIsNone(telemetry.temperature_raw)
        self.assertIsNone(telemetry.overheat_raw)

    def test_temperature_widget_passes_through(self) -> None:
        telemetry = _backend_with(
            {
                "cameratemperature": FakeWidget("cameratemperature", "42"),
                "batterylevel": FakeWidget("batterylevel", "80"),
            }
        ).read_telemetry()

        self.assertEqual(telemetry.temperature_raw, "42")
        self.assertEqual(telemetry.battery_raw, "80")
        self.assertTrue(telemetry.available)

    def test_failing_widget_read_returns_none_without_raising(self) -> None:
        telemetry = _backend_with(
            {"batterylevel": FakeWidget("batterylevel", "80", raises=True)}
        ).read_telemetry()

        self.assertIsNone(telemetry.battery_raw)
        self.assertFalse(telemetry.available)

    def test_poll_path_leaves_widget_names_empty(self) -> None:
        telemetry = _backend_with(
            {
                "cameratemperature": FakeWidget("cameratemperature", "42"),
                "batterylevel": FakeWidget("batterylevel", "80"),
            }
        ).read_telemetry()

        self.assertEqual(telemetry.temperature_raw, "42")
        self.assertEqual(telemetry.battery_raw, "80")
        self.assertEqual(telemetry.widget_names, ())

    def test_telemetry_never_fetches_full_config(self) -> None:
        backend, camera = _backend_with_raising_config({"batterylevel": FakeWidget("batterylevel", "73%")})

        telemetry = backend.read_telemetry()

        self.assertEqual(telemetry.battery_raw, "73%")
        self.assertTrue(telemetry.available)
        self.assertEqual(camera.get_config_calls, 0)
        self.assertIn("batterylevel", camera.get_single_config_calls)

    def test_absent_candidates_stay_quiet_without_full_fetch(self) -> None:
        backend, camera = _backend_with_raising_config({})

        telemetry = backend.read_telemetry()

        self.assertFalse(telemetry.available)
        self.assertIsNone(telemetry.temperature_raw)
        self.assertIsNone(telemetry.overheat_raw)
        self.assertIsNone(telemetry.battery_raw)
        self.assertEqual(camera.get_config_calls, 0)
        # Every candidate is probed individually; none raises out of the poll.
        self.assertEqual(
            camera.get_single_config_calls,
            [
                *GPhotoBackend._temperature_widgets,
                *GPhotoBackend._overheat_widgets,
                *GPhotoBackend._battery_widgets,
            ],
        )

    def test_disconnected_backend_returns_empty_telemetry(self) -> None:
        telemetry = GPhotoBackend().read_telemetry()

        self.assertFalse(telemetry.available)
        self.assertEqual(telemetry, CameraTelemetry())

    def test_wifi_and_sdk_server_backends_return_empty_telemetry(self) -> None:
        wifi_telemetry = SonyRemoteApiBackend("").read_telemetry()
        sdk_telemetry = SonySdkServerBackend().read_telemetry()

        self.assertEqual(wifi_telemetry, CameraTelemetry())
        self.assertEqual(sdk_telemetry, CameraTelemetry())
        self.assertFalse(wifi_telemetry.available)
        self.assertFalse(sdk_telemetry.available)


if __name__ == "__main__":
    unittest.main()
