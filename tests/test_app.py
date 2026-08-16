from __future__ import annotations

import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from monitor_desktop.app import MonitorWindow, ScopeView
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


if __name__ == "__main__":
    unittest.main()
