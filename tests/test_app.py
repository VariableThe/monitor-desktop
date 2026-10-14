from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from monitor_desktop.app import (
    CameraSettingControl,
    MonitorWindow,
    ScopeView,
    app_icon,
    load_application_fonts,
    load_custom_camera_presets,
    load_virtual_camera_prefs,
    save_custom_camera_presets,
    save_virtual_camera_prefs,
)
from monitor_desktop.backends import CameraDevice, GPhotoLiveCapture
from monitor_desktop.virtual_camera import VirtualCameraError, VirtualCameraOutput


class ScopeViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_scope_frames_do_not_change_the_fixed_layout_height(self) -> None:
        view = ScopeView()
        view.resize(340, ScopeView.fixed_height)
        view.show()
        frame = np.zeros((240, 640, 3), dtype=np.uint8)

        for _ in range(20):
            view.present(frame)
            self.app.processEvents()

        self.assertEqual(view.height(), ScopeView.fixed_height)
        self.assertEqual(view.minimumHeight(), ScopeView.fixed_height)
        self.assertEqual(view.maximumHeight(), ScopeView.fixed_height)
        view.close()

    def test_discovery_selects_the_first_camera_result(self) -> None:
        window = MonitorWindow()
        window.backend_select.setCurrentIndex(1)
        window._discover_selected_backend = lambda: (  # type: ignore[method-assign]
            object(),
            [
                CameraDevice("usb:001,001", "Sony ZV-E10", "gphoto2 USB"),
                CameraDevice("usb:001,002", "Sony A7", "gphoto2 USB"),
            ],
        )
        window.discover_camera()

        self.assertEqual(window.camera_devices.currentIndex(), 0)
        self.assertEqual(window.camera_devices.currentText(), "Sony ZV-E10")
        window.close()

    def test_preview_tools_and_advanced_assists_stay_in_sync(self) -> None:
        window = MonitorWindow()

        self.assertEqual(window.mode_stack.currentIndex(), 0)
        self.assertTrue(window.preview_drawer.isHidden())
        window.preview_tools_button.setChecked(True)
        self.app.processEvents()
        self.assertFalse(window.preview_drawer.isHidden())

        window.preview_assist_buttons["peaking"].setChecked(True)
        self.assertTrue(window.assist_buttons["peaking"].isChecked())
        self.assertTrue(window.settings.peaking)
        window.set_mode("advanced", announce=False)
        self.assertEqual(window.mode_stack.currentIndex(), 1)
        window.close()

    def test_virtual_camera_uses_an_incoming_stream_without_a_camera_connection(self) -> None:
        class FakeVirtualCamera:
            def __init__(self) -> None:
                self.active = False
                self.device = "Test Virtual Camera"
                self.sent: list[np.ndarray] = []

            def start(self, frame: np.ndarray, device: str | None) -> str:
                self.active = True
                self.sent.append(frame.copy())
                return self.device

            def send(self, frame: np.ndarray) -> None:
                self.sent.append(frame.copy())

            def stop(self) -> None:
                self.active = False

        # NOTE (feat/auto-virtual-camera): isolated prefs paths so the new
        # virtual-camera persistence cannot leak widget state between runs.
        # The Active-status assertion below is unchanged.
        with tempfile.TemporaryDirectory() as directory:
            window = MonitorWindow(Path(directory) / "camera-presets.json", Path(directory) / "virtual-camera.json")
            virtual_camera = FakeVirtualCamera()
            window.virtual_camera = virtual_camera  # type: ignore[assignment]
            incoming = np.full((24, 32, 3), 42, dtype=np.uint8)
            processed = np.full((24, 32, 3), 99, dtype=np.uint8)
            window.latest_source_frame = incoming

            window.toggle_virtual_camera()
            window._publish_virtual_camera(incoming, processed)

            self.assertTrue(virtual_camera.active)
            self.assertEqual(window.virtual_camera_status.text(), "Active: Test Virtual Camera. Select it as the camera in your call or meeting app.")
            self.assertTrue(np.array_equal(virtual_camera.sent[-1], incoming))

            window.virtual_camera_feed.setCurrentIndex(1)
            window._publish_virtual_camera(incoming, processed)
            self.assertTrue(np.array_equal(virtual_camera.sent[-1], processed))
            window.close()

    def test_monitor_preset_applies_look_and_assists(self) -> None:
        window = MonitorWindow()

        window.apply_monitor_preset("Director's View")

        self.assertTrue(window.settings.zebra)
        self.assertTrue(window.settings.peaking)
        self.assertTrue(window.settings.guide)
        self.assertEqual(window.current_look, "Warm Film")
        self.assertIsNotNone(window.current_lut)
        self.assertEqual(window.preview_preset_select.currentText(), "Director's View")
        window.close()

    def test_camera_setting_control_scrubs_and_accepts_typed_values(self) -> None:
        applied: list[tuple[str, str]] = []
        invalid: list[str] = []
        control = CameraSettingControl("iso", ["100", "200", "400"], lambda name, value: applied.append((name, value)), invalid.append)

        control.slider.setValue(2)
        control.apply_current()
        self.assertEqual(control.currentText(), "400")
        self.assertEqual(applied, [("iso", "400")])

        control.value_input.setText("200")
        control.apply_current()
        self.assertEqual(control.slider.value(), 1)
        self.assertEqual(applied[-1], ("iso", "200"))

        control.value_input.setText("333")
        control.apply_current()
        self.assertEqual(invalid, ["iso"])

    def test_custom_camera_presets_round_trip_only_supported_setting_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "camera-presets.json"
            save_custom_camera_presets(
                path,
                {
                    "Desk": {"iso": "800", "shutter": "1/50", "unsupported": "ignored"},
                    "": {"iso": "100"},
                },
            )

            self.assertEqual(load_custom_camera_presets(path), {"Desk": {"iso": "800", "shutter": "1/50"}})

    def test_saving_custom_camera_preset_persists_current_camera_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "camera-presets.json"
            window = MonitorWindow(path)
            window.active_backend = object()  # type: ignore[assignment]
            window._set_camera_controls_enabled(True)
            window.camera_setting_boxes["iso"].setCurrentText("800")
            window.camera_setting_boxes["shutter"].setCurrentText("1/50")

            with patch("monitor_desktop.app.QInputDialog.getText", return_value=("Desk", True)):
                window.save_current_camera_preset()

            self.assertEqual(window.camera_preset_select.currentText(), "Custom: Desk")
            self.assertEqual(load_custom_camera_presets(path)["Desk"]["iso"], "800")
            window.close()

            reloaded = MonitorWindow(path)
            self.assertIn("Custom: Desk", [reloaded.camera_preset_select.itemText(index) for index in range(reloaded.camera_preset_select.count())])
            reloaded.close()

    def test_custom_camera_preset_applies_only_matching_values(self) -> None:
        class FakeBackend:
            name = "Test camera"

            def __init__(self) -> None:
                self.applied: list[tuple[str, str]] = []

            def set_property(self, name: str, value: str) -> str:
                self.applied.append((name, value))
                return f"Set {name}"

        with tempfile.TemporaryDirectory() as directory:
            window = MonitorWindow(Path(directory) / "camera-presets.json")
            backend = FakeBackend()
            window.active_backend = backend  # type: ignore[assignment]
            window._set_camera_controls_enabled(True)
            window.custom_camera_presets = {"Desk": {"iso": "800", "shutter": "1/50", "aperture": "99"}}
            window._refresh_camera_preset_select("Custom: Desk")

            window.apply_camera_preset()

            self.assertEqual(backend.applied, [("iso", "800"), ("shutter", "1/50")])
            self.assertIn("Skipped: aperture", window.status_label.text())
            window.close()

    def test_auto_connect_uses_a_single_discovered_usb_camera(self) -> None:
        class FakeCapture:
            def release(self) -> None:
                pass

        class FakeGPhotoBackend:
            name = "gphoto2 USB"

            @staticmethod
            def installed() -> bool:
                return True

            def discover(self) -> list[CameraDevice]:
                return [CameraDevice("usb:001,001", "Sony ZV-E10", self.name)]

            def connect(self, device: CameraDevice) -> None:
                self.connected = device

            def start_live_view(self) -> FakeCapture:
                return FakeCapture()

            def available_values(self, name: str) -> list[str]:
                return []

            def disconnect(self) -> None:
                pass

        window = MonitorWindow()
        with patch("monitor_desktop.app.GPhotoBackend", FakeGPhotoBackend):
            window.auto_connect_usb_camera()

        self.assertEqual(window.camera_devices.currentText(), "Sony ZV-E10")
        self.assertEqual(window.preview_camera_label.text(), "Sony ZV-E10 connected")
        self.assertEqual(window.connection_label.text(), "CAMERA LIVE VIEW")
        self.assertEqual(window.mode_stack.currentIndex(), 0)
        self.assertIn("Auto-started camera live view", window.status_label.text())
        window.close()

    def test_zoom_buttons_dispatch_start_and_stop_actions(self) -> None:
        class FakeBackend:
            def __init__(self) -> None:
                self.actions: list[str] = []

            def action(self, action: str) -> str:
                self.actions.append(action)
                return f"{action} ok"

        window = MonitorWindow()
        backend = FakeBackend()
        window.active_backend = backend  # type: ignore[assignment]
        window._set_camera_controls_enabled(True)

        window.zoom_in_button.pressed.emit()
        window.zoom_in_button.released.emit()
        window.preview_zoom_out_button.pressed.emit()
        window.preview_zoom_out_button.released.emit()

        self.assertEqual(backend.actions, ["zoom_in", "zoom_stop", "zoom_out", "zoom_stop"])
        window.close()

    def test_zoom_buttons_disable_when_backend_reports_no_zoom_support(self) -> None:
        class FakeBackend:
            def supports_action(self, action: str) -> bool:
                return not action.startswith("zoom_")

        window = MonitorWindow()
        window.active_backend = FakeBackend()  # type: ignore[assignment]

        window._set_camera_controls_enabled(True)

        self.assertFalse(window.zoom_in_button.isEnabled())
        self.assertFalse(window.preview_zoom_out_button.isEnabled())
        self.assertIn("not available", window.zoom_in_button.toolTip())
        window.close()

    def test_jetbrains_mono_is_registered_from_the_bundled_font(self) -> None:
        window = MonitorWindow()

        # Assert on the families the loader actually registered, not on
        # QFontDatabase.families(): when the host OS already ships JetBrains
        # Mono, Qt qualifies the duplicate with the foundry (e.g.
        # "JetBrains Mono [JB  ]") and drops the plain name from families(),
        # even though registration succeeded and the font resolves.
        self.assertIn("JetBrains Mono", load_application_fonts())
        self.assertEqual(window.font().family(), "JetBrains Mono")
        window.close()

    def test_vector_icons_and_settings_update_control_are_available(self) -> None:
        window = MonitorWindow()

        self.assertFalse(app_icon("gear").isNull())
        self.assertFalse(app_icon("folder-open").isNull())
        window.show_settings()

        assert window.settings_dialog is not None
        self.assertEqual(window.settings_dialog.update_button.text(), "Update app")
        self.assertFalse(window.settings_button.icon().isNull())
        window.settings_dialog.close()
        window.close()

    def test_window_carries_the_product_name_and_bundled_icon(self) -> None:
        window = MonitorWindow()

        self.assertEqual(window.windowTitle(), "viewfindrr")
        self.assertFalse(window.windowIcon().isNull())
        self.assertIn("viewfindrr", window.virtual_camera_device.placeholderText())
        window.close()


class FakeVirtualCameraOutput:
    """Fake virtual-camera backend recording start device and sends."""

    def __init__(self, device: str = "Test Virtual Camera") -> None:
        self.active = False
        self.device = device
        self.start_calls = 0
        self.start_device: str | None = None
        self.sent: list[np.ndarray] = []

    def start(self, frame: np.ndarray, device: str | None) -> str:
        self.start_calls += 1
        self.start_device = device
        self.active = True
        self.sent.append(frame.copy())
        return self.device

    def send(self, frame: np.ndarray) -> None:
        self.sent.append(frame.copy())

    def stop(self) -> None:
        self.active = False


class FakeCapture:
    """Minimal capture stub delivering one fixed frame per read."""

    def __init__(self, frame: np.ndarray) -> None:
        self._frame = frame
        self.released = False

    def read(self) -> tuple[bool, np.ndarray | None]:
        return True, self._frame.copy()

    def release(self) -> None:
        self.released = True


class VirtualCameraAutoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, directory: str) -> MonitorWindow:
        return MonitorWindow(Path(directory) / "camera-presets.json", Path(directory) / "virtual-camera.json")

    def test_auto_mode_armed_then_started_notifies_and_refreshes_controls(self) -> None:
        frame = np.full((24, 32, 3), 42, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            fake = FakeVirtualCameraOutput()
            window.virtual_camera = fake  # type: ignore[assignment]
            with patch("monitor_desktop.app._resolve_loopback_device", return_value="/dev/video10"):
                window.virtual_camera_mode.setCurrentIndex(1)  # Auto
                window._set_capture(FakeCapture(frame), "Video connected")
                self.assertTrue(window._vc_auto_armed)
                window._tick()
                self.assertTrue(fake.active)
                self.assertEqual(fake.start_calls, 1)
                self.assertEqual(fake.start_device, "/dev/video10")
                self.assertIn("Virtual camera started:", window.status_label.text())
                self.assertIn("Test Virtual Camera", window.status_label.text())
                self.assertTrue(window.virtual_camera_status.text().startswith("Active:"))
                # No tight retry loop: a second tick must not reopen the camera.
                window._tick()
                self.assertEqual(fake.start_calls, 1)
            window.close()

    def test_mode_off_never_auto_starts(self) -> None:
        frame = np.full((24, 32, 3), 42, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            fake = FakeVirtualCameraOutput()
            window.virtual_camera = fake  # type: ignore[assignment]
            self.assertEqual(window.virtual_camera_mode.currentIndex(), 0)
            with patch("monitor_desktop.app._resolve_loopback_device", return_value="/dev/video10"):
                window._set_capture(FakeCapture(frame), "Video connected")
                window._tick()
                self.assertFalse(fake.active)
                self.assertEqual(fake.start_calls, 0)
            window.close()

    def test_disconnecting_source_stops_active_virtual_camera(self) -> None:
        frame = np.full((24, 32, 3), 42, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            fake = FakeVirtualCameraOutput()
            window.virtual_camera = fake  # type: ignore[assignment]
            window.capture = FakeCapture(frame)  # type: ignore[assignment]
            fake.start(frame, "/dev/video10")
            self.assertTrue(fake.active)
            window.disconnect_source()
            self.assertFalse(fake.active)
            self.assertEqual(window.connection_label.text(), "NO SOURCE")
            window.close()

    def test_manual_stop_while_auto_disarms_until_next_activation(self) -> None:
        frame = np.full((24, 32, 3), 42, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            fake = FakeVirtualCameraOutput()
            window.virtual_camera = fake  # type: ignore[assignment]
            with patch("monitor_desktop.app._resolve_loopback_device", return_value="/dev/video10"):
                window.virtual_camera_mode.setCurrentIndex(1)  # Auto
                window._set_capture(FakeCapture(frame), "Video connected")
                window._tick()
                self.assertTrue(fake.active)
                window.toggle_virtual_camera()  # manual Stop
                self.assertFalse(fake.active)
                self.assertFalse(window._vc_auto_armed)
                # Same source keeps delivering frames but must not restart.
                window.capture = FakeCapture(frame)  # type: ignore[assignment]
                window._tick()
                self.assertFalse(fake.active)
                # Next source activation re-arms and restarts.
                window._set_capture(FakeCapture(frame), "Video connected")
                window._tick()
                self.assertTrue(fake.active)
            window.close()

    def test_dead_camera_live_view_stops_virtual_camera_with_single_notice(self) -> None:
        class DeadCapture(GPhotoLiveCapture):
            def __init__(self) -> None:
                pass

            def isOpened(self) -> bool:  # noqa: N802 - match OpenCV's API
                return False

            def release(self) -> None:
                pass

        frame = np.full((24, 32, 3), 42, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            fake = FakeVirtualCameraOutput()
            window.virtual_camera = fake  # type: ignore[assignment]
            fake.start(frame, "/dev/video10")
            window.capture = DeadCapture()  # type: ignore[assignment]
            window._tick()
            self.assertFalse(fake.active)
            self.assertIsNone(window.capture)
            self.assertEqual(window.status_label.text(), "Camera live view ended.")
            # A further tick must not repeat the notice or touch the camera.
            window._notify("untouched")
            window._tick()
            self.assertEqual(window.status_label.text(), "untouched")
            window.close()

    def test_resolution_change_does_not_reopen_virtual_camera(self) -> None:
        created: list[dict] = []

        class FactoryCamera:
            device = "Test Virtual Camera"

            def __init__(self) -> None:
                self.frames: list[np.ndarray] = []
                self.closed = False

            def send(self, frame: np.ndarray) -> None:
                self.frames.append(frame.copy())

            def close(self) -> None:
                self.closed = True

        camera = FactoryCamera()

        def factory(**kwargs: object) -> FactoryCamera:
            created.append(dict(kwargs))
            return camera

        output = VirtualCameraOutput(factory, fps=30)
        output.start(np.zeros((180, 320, 3), dtype=np.uint8), "/dev/video10")
        output.send(np.zeros((360, 640, 3), dtype=np.uint8))

        self.assertEqual(len(created), 1)
        self.assertEqual(len(camera.frames), 2)
        self.assertEqual([frame.shape for frame in camera.frames], [(180, 320, 3), (180, 320, 3)])
        output.stop()

    def test_virtual_camera_prefs_persist_on_widget_change_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "virtual-camera.json"
            window = self._window(directory)
            window.virtual_camera_mode.setCurrentIndex(1)
            window.virtual_camera_feed.setCurrentIndex(1)
            window.virtual_camera_device.setText("/dev/video10")
            saved = load_virtual_camera_prefs(path)
            self.assertEqual(saved, {"mode": "auto", "feed": 1, "device_override": "/dev/video10"})
            window.close()

            reloaded = self._window(directory)
            self.assertEqual(reloaded.virtual_camera_mode.currentIndex(), 1)
            self.assertEqual(reloaded.virtual_camera_feed.currentIndex(), 1)
            self.assertEqual(reloaded.virtual_camera_device.text(), "/dev/video10")
            reloaded.close()

    def test_unresolved_device_shows_remediation_once_per_arming(self) -> None:
        frame = np.full((24, 32, 3), 42, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)
            fake = FakeVirtualCameraOutput()
            window.virtual_camera = fake  # type: ignore[assignment]
            with patch("monitor_desktop.app._resolve_loopback_device", return_value=None):
                window.virtual_camera_mode.setCurrentIndex(1)  # Auto
                window._set_virtual_camera_controls()
                self.assertIn("Virtual camera driver not found", window.virtual_camera_status.text())
                self.assertIn("setup-v4l2loopback", window.virtual_camera_status.text())
                window._set_capture(FakeCapture(frame), "Video connected")
                window._tick()
                self.assertFalse(fake.active)
                self.assertFalse(window._vc_auto_armed)
                self.assertIn("Virtual camera driver not found", window.status_label.text())
                # Disarmed: a second tick must not notify again.
                window._notify("untouched")
                window._tick()
                self.assertEqual(window.status_label.text(), "untouched")
            window.close()

    def test_busy_device_shows_busy_message(self) -> None:
        frame = np.full((24, 32, 3), 42, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(directory)

            class BusyCamera(FakeVirtualCameraOutput):
                def start(self, frame: np.ndarray, device: str | None) -> str:
                    raise VirtualCameraError("Could not open the virtual camera '/dev/video10': busy")

            window.virtual_camera = BusyCamera()  # type: ignore[assignment]
            with patch("monitor_desktop.app._resolve_loopback_device", return_value="/dev/video10"):
                window.virtual_camera_mode.setCurrentIndex(1)  # Auto
                window._set_capture(FakeCapture(frame), "Video connected")
                window._tick()
                self.assertIn("Virtual camera is busy", window.status_label.text())
                self.assertFalse(window._vc_auto_armed)
            window.close()


if __name__ == "__main__":
    unittest.main()
