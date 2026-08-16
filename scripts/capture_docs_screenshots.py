"""Capture documentation screenshots without opening a camera connection."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from monitor_desktop.app import MonitorWindow


def save_window(window: MonitorWindow, destination: Path) -> None:
    QApplication.processEvents()
    if not window.grab().save(str(destination)):
        raise RuntimeError(f"Could not save {destination}")


def main() -> int:
    output = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    window = MonitorWindow()
    # Documentation should remain anonymous and must not touch connected hardware.
    window._auto_connect_scheduled = True
    window.resize(1440, 900)
    window.show()

    window.preview_tools_button.setChecked(True)
    save_window(window, output / "preview-mode.png")

    window.set_mode("advanced", announce=False)
    save_window(window, output / "advanced-mode.png")

    window.show_settings()
    assert window.settings_dialog is not None
    save_window(window.settings_dialog, output / "settings.png")
    window.settings_dialog.close()

    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
