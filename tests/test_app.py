from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from monitor_desktop.app import CameraSettingControl, MonitorWindow, ScopeView, app_icon, load_custom_camera_presets, save_custom_camera_presets
from monitor_desktop.backends import CameraDevice


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

    def test_jetbrains_mono_is_registered_from_the_bundled_font(self) -> None:
        window = MonitorWindow()

        self.assertIn("JetBrains Mono", QFontDatabase.families())
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


if __name__ == "__main__":
    unittest.main()
