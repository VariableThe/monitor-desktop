"""Qt desktop application for camera monitoring and Sony control."""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import qtawesome as qta
from PySide6.QtCore import QProcess, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QFont, QFontDatabase, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .backends import (
    CameraDevice,
    CameraError,
    GPhotoBackend,
    GPhotoLiveCapture,
    SonyRemoteApiBackend,
    SonySdkServerBackend,
)
from . import __version__
from .video_tools import (
    BUILTIN_LOOK_NAMES,
    MonitorSettings,
    built_in_lut,
    fit_frame_to_box,
    load_cube_lut,
    make_histogram,
    make_vectorscope,
    make_waveform,
    parse_source,
    process_frame,
)


APP_STYLE = """
QMainWindow, QDialog { background: #050607; color: #e7e9ee; }
QWidget { font-family: "JetBrains Mono", "SF Mono", "Menlo", monospace; font-size: 12px; }
QFrame#topbar { background: #090a0d; border-bottom: 1px solid #20232b; }
QFrame#sidebar { background: #0b0d12; border-color: #20232b; }
QFrame#preview_workspace { background: #030405; }
QFrame#preview_drawer { background: #0b0d12; border-left: 1px solid #20232b; }
QFrame#preview_footer { background: #090a0d; border-top: 1px solid #20232b; }
QFrame#transport { background: #090a0d; border: 1px solid #232832; border-radius: 2px; }
QMenuBar, QMenu { background: #090a0d; color: #e7e9ee; }
QMenuBar::item:selected, QMenu::item:selected { background: #151923; }
QLabel#brand { color: #f5f7fb; font-size: 15px; font-weight: 700; }
QLabel#muted { color: #858b98; }
QLabel#status { color: #a5acb8; background: #090a0d; border-top: 1px solid #20232b; }
QLabel#timecode { color: #f05a5f; font-size: 13px; font-weight: 700; }
QLabel#preview_label { color: #8f96a3; font-size: 11px; font-weight: 700; }
QGroupBox { background: #0b0d12; border: 1px solid #232832; border-radius: 2px; margin-top: 13px; padding: 10px 8px 8px; color: #dfe4ec; font-weight: 700; }
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px; }
QLabel { color: #e0e4ec; }
QLineEdit, QComboBox { background: #10131a; border: 1px solid #2a303b; border-radius: 2px; color: #f1f4f9; min-height: 28px; padding: 0 8px; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #3b82f6; }
QComboBox::drop-down { border: 0; width: 24px; }
QPushButton, QToolButton { background: #10131a; border: 1px solid #2a303b; border-radius: 2px; color: #e7eaf0; min-height: 29px; padding: 0 9px; }
QPushButton:hover, QToolButton:hover { background: #171c26; border-color: #4a5568; }
QPushButton:disabled, QToolButton:disabled { background: #0a0c10; border-color: #1c2027; color: #59606c; }
QPushButton#primary { background: #2563eb; border-color: #3b82f6; color: #ffffff; font-weight: 700; }
QPushButton#primary:hover { background: #1d4ed8; }
QPushButton#recording { background: #b4232a; border-color: #f05a5f; color: #ffffff; font-weight: 700; }
QToolButton[mode="true"] { background: transparent; border-color: transparent; min-width: 76px; }
QToolButton[mode="true"]:checked { background: #111b34; border-color: #3b82f6; color: #ffffff; }
QToolButton[assist="true"] { min-width: 72px; }
QToolButton[assist="true"]:checked { background: #351318; border-color: #e5484d; color: #ffffff; }
QToolButton[quick="true"] { min-width: 92px; }
QToolButton[quick="true"]:checked { background: #351318; border-color: #e5484d; color: #ffffff; }
QSlider::groove:horizontal { height: 3px; background: #252b36; border-radius: 1px; }
QSlider::sub-page:horizontal { background: #3b82f6; border-radius: 1px; }
QSlider::handle:horizontal { background: #f05a5f; width: 11px; margin: -5px 0; border-radius: 5px; }
QScrollArea, QScrollArea > QWidget > QWidget { background: #0b0d12; border: 0; }
"""


MONITOR_PRESETS = {
    "Clean Preview": {"zebra": False, "false_color": False, "peaking": False, "guide": False, "flip": False, "desqueeze": "1.00x", "look": "Neutral"},
    "Focus Check": {"zebra": False, "false_color": False, "peaking": True, "guide": False, "flip": False, "desqueeze": "1.00x", "look": "Neutral"},
    "Exposure Check": {"zebra": True, "false_color": False, "peaking": False, "guide": False, "flip": False, "desqueeze": "1.00x", "look": "Neutral"},
    "Framing": {"zebra": False, "false_color": False, "peaking": False, "guide": True, "flip": False, "desqueeze": "1.00x", "look": "Neutral"},
    "Director's View": {"zebra": True, "false_color": False, "peaking": True, "guide": True, "flip": False, "desqueeze": "1.00x", "look": "Warm Film"},
}

CAMERA_PRESETS = {
    "No camera changes": {},
    "24 fps daylight": {"iso": "100", "shutter": "1/50", "white_balance": "Daylight"},
    "24 fps indoor": {"iso": "800", "shutter": "1/50", "white_balance": "Auto"},
    "24 fps low light": {"iso": "1600", "shutter": "1/50", "white_balance": "Auto"},
}

CAMERA_SETTING_NAMES = ("iso", "shutter", "aperture", "white_balance", "focus_mode")
CUSTOM_CAMERA_PRESET_PREFIX = "Custom: "
UPDATE_SCRIPT_URL = "https://raw.githubusercontent.com/VariableThe/monitor-desktop/main/scripts/update.sh"
UPDATE_COMMAND = f"curl -fsSL {UPDATE_SCRIPT_URL} | sh"


def app_icon(name: str) -> QIcon:
    return qta.icon(f"fa6s.{name}", color="#dfe5ef", color_active="#ffffff", color_disabled="#59606c")


def custom_camera_preset_path() -> Path:
    config_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    return Path(config_dir) / "camera-presets.json"


def _clean_custom_camera_presets(presets: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    cleaned: dict[str, dict[str, str]] = {}
    for raw_name, raw_values in presets.items():
        if not isinstance(raw_name, str) or not isinstance(raw_values, dict):
            continue
        name = raw_name.strip()
        values = {
            setting: value.strip()
            for setting, value in raw_values.items()
            if setting in CAMERA_SETTING_NAMES and isinstance(value, str) and value.strip()
        }
        if name and values:
            cleaned[name] = values
    return dict(sorted(cleaned.items(), key=lambda item: item[0].casefold()))


def load_custom_camera_presets(path: Path) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    stored_presets = payload.get("presets") if isinstance(payload, dict) else None
    return _clean_custom_camera_presets(stored_presets) if isinstance(stored_presets, dict) else {}


def save_custom_camera_presets(path: Path, presets: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary_path.write_text(
            json.dumps({"version": 1, "presets": _clean_custom_camera_presets(presets)}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


_application_fonts_loaded = False


def load_application_fonts() -> None:
    """Register bundled JetBrains Mono so the interface is consistent cross-platform."""
    global _application_fonts_loaded
    if _application_fonts_loaded:
        return
    fonts = Path(__file__).resolve().parent / "assets" / "fonts"
    for filename in ("JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf"):
        QFontDatabase.addApplicationFont(str(fonts / filename))
    if app := QApplication.instance():
        app.setFont(QFont("JetBrains Mono", 12))
    _application_fonts_loaded = True


class VideoSurface(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self._frame: np.ndarray | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(560, 360)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setStyleSheet("background: #020304; border: 1px solid #1d222b;")

    def present(self, frame: np.ndarray) -> None:
        self._frame = frame
        self._render_frame()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._render_frame()

    def _render_frame(self) -> None:
        if self._frame is None or self.width() < 2 or self.height() < 2:
            return
        display = fit_frame_to_box(self._frame, self.width(), self.height())
        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self.setPixmap(QPixmap.fromImage(image))


class ScopeView(QLabel):
    """A fixed-height image surface that never lets scope pixmaps resize the UI."""

    fixed_height = 118

    def __init__(self) -> None:
        super().__init__()
        self._frame: np.ndarray | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(self.fixed_height)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("background: #07090d; border: 1px solid #252b35;")

    def present(self, frame: np.ndarray) -> None:
        self._frame = frame
        self._render_frame()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._render_frame()

    def _render_frame(self) -> None:
        if self._frame is None or self.width() < 2 or self.height() < 2:
            return
        display = fit_frame_to_box(self._frame, self.width(), self.height())
        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self.setPixmap(QPixmap.fromImage(image))


class CameraSettingControl(QWidget):
    """Choose a camera setting by its supported steps or an exact typed value."""

    def __init__(self, name: str, values: list[str], on_apply: Any, on_invalid: Any) -> None:
        super().__init__()
        self.name = name
        self._values: list[str] = []
        self._on_apply = on_apply
        self._on_invalid = on_invalid
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setToolTip(f"Adjust {name.replace('_', ' ')}")
        self.slider.sliderReleased.connect(self.apply_current)
        self.slider.valueChanged.connect(self._sync_value_text)
        layout.addWidget(self.slider, 1)
        self.value_input = QLineEdit()
        self.value_input.setMinimumWidth(100)
        self.value_input.setMaximumWidth(116)
        self.value_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_input.returnPressed.connect(self.apply_current)
        layout.addWidget(self.value_input)
        self.apply_button = QToolButton()
        self.apply_button.setIcon(app_icon("check"))
        self.apply_button.setToolTip(f"Apply {name.replace('_', ' ')}")
        self.apply_button.clicked.connect(self.apply_current)
        layout.addWidget(self.apply_button)
        self.set_values(values)

    def values(self) -> list[str]:
        return self._values.copy()

    def currentText(self) -> str:  # noqa: N802 - matches Qt's selector API
        return self.value_input.text().strip()

    def setCurrentText(self, value: str) -> None:  # noqa: N802 - matches Qt's selector API
        match = self._matching_value(value)
        if match is None:
            self.value_input.setText(value)
            return
        index = self._values.index(match)
        was_blocked = self.slider.blockSignals(True)
        self.slider.setValue(index)
        self.slider.blockSignals(was_blocked)
        self._set_value_text(match)

    def set_values(self, values: list[str], current: str | None = None) -> None:
        self._values = list(dict.fromkeys(values))
        was_blocked = self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, len(self._values) - 1))
        self.slider.setValue(self._values.index(current) if current in self._values else 0)
        self.slider.blockSignals(was_blocked)
        self._set_value_text(self._values[self.slider.value()] if self._values else "")

    def _matching_value(self, value: str) -> str | None:
        normalized = value.strip().casefold()
        for choice in self._values:
            if choice.casefold() == normalized:
                return choice
        if normalized == "auto":
            return next((choice for choice in self._values if choice.casefold().startswith("auto")), None)
        return None

    def _set_value_text(self, value: str) -> None:
        self.value_input.setText(value)
        self.value_input.setToolTip(value)

    def _sync_value_text(self, index: int) -> None:
        if self._values:
            self._set_value_text(self._values[index])

    def apply_current(self) -> None:
        value = self._matching_value(self.currentText())
        if value is None:
            self._on_invalid(self.name)
            self._sync_value_text(self.slider.value())
            return
        self.setCurrentText(value)
        self._on_apply(self.name, value)


class SettingsDialog(QDialog):
    def __init__(self, parent: "MonitorWindow") -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(390)
        self.setStyleSheet(APP_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        title = QLabel("Settings")
        title.setObjectName("brand")
        layout.addWidget(title)
        application = QGroupBox("Application")
        application_layout = QVBoxLayout(application)
        application_layout.setSpacing(8)
        version = QLabel(f"Monitor Desktop {__version__}")
        version.setObjectName("muted")
        application_layout.addWidget(version)
        self.update_status = QLabel("Ready to update.")
        self.update_status.setObjectName("muted")
        self.update_status.setWordWrap(True)
        application_layout.addWidget(self.update_status)
        self.update_button = QPushButton("Update app")
        self.update_button.setObjectName("primary")
        self.update_button.setIcon(app_icon("arrows-rotate"))
        self.update_button.clicked.connect(parent.start_application_update)
        application_layout.addWidget(self.update_button)
        layout.addWidget(application)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

    def set_update_state(self, message: str, updating: bool = False) -> None:
        self.update_status.setText(message)
        self.update_button.setEnabled(not updating)
        self.update_button.setText("Updating..." if updating else "Update app")


class MonitorWindow(QMainWindow):
    def __init__(self, preset_path: Path | None = None) -> None:
        super().__init__()
        load_application_fonts()
        self.setWindowTitle("Monitor Desktop")
        self.resize(1440, 900)
        self.setMinimumSize(1180, 680)

        self.settings = MonitorSettings(zebra=False, peaking=False, guide=False)
        self.capture: cv2.VideoCapture | GPhotoLiveCapture | None = None
        self.capture_is_file = False
        self.latest_frame: np.ndarray | None = None
        self.writer: cv2.VideoWriter | None = None
        self.recording_path: Path | None = None
        self.current_lut: np.ndarray | None = None
        self.current_look = "Neutral"
        self.camera_recording = False
        self.active_backend: GPhotoBackend | SonyRemoteApiBackend | SonySdkServerBackend | None = None
        self.discovered_devices: list[CameraDevice] = []
        self.preset_path = preset_path or custom_camera_preset_path()
        self.custom_camera_presets = load_custom_camera_presets(self.preset_path)
        self.frame_count = 0
        self._auto_connect_scheduled = False
        self.settings_dialog: SettingsDialog | None = None
        self.update_process: QProcess | None = None
        self._update_output = ""

        self._build_ui()
        self._sync_monitor_settings()
        self._show_idle_frame()
        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(33)
        self.frame_timer.timeout.connect(self._tick)
        self.frame_timer.start()

    def _build_ui(self) -> None:
        self.setStyleSheet(APP_STYLE)
        self.setCentralWidget(self._build_workspace())
        self._build_menu()
        self.menuBar().hide()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        open_action = QAction("Open video file", self)
        open_action.triggered.connect(self.pick_video_file)
        file_menu.addAction(open_action)
        screenshot_action = QAction("Save monitor frame", self)
        screenshot_action.triggered.connect(self.save_screenshot)
        file_menu.addAction(screenshot_action)
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _build_workspace(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_topbar())

        self.mode_stack = QStackedWidget()
        self.preview_workspace = self._build_preview_workspace()
        self.advanced_workspace = self._build_advanced_workspace()
        self.mode_stack.addWidget(self.preview_workspace)
        self.mode_stack.addWidget(self.advanced_workspace)
        layout.addWidget(self.mode_stack, 1)

        self.status_label = QLabel("Ready. Connect a capture device, video source, or Sony camera.")
        self.status_label.setObjectName("status")
        self.status_label.setContentsMargins(14, 7, 14, 7)
        layout.addWidget(self.status_label)
        self.set_mode("preview", announce=False)
        return root

    def _build_advanced_workspace(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_sidebar())
        splitter.addWidget(self._build_monitor_area())
        splitter.addWidget(self._build_right_sidebar())
        splitter.setSizes([300, 820, 320])
        return splitter

    def _build_preview_workspace(self) -> QWidget:
        page = QFrame()
        page.setObjectName("preview_workspace")
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.preview_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.preview_splitter.setChildrenCollapsible(False)
        monitor = QFrame()
        monitor_layout = QVBoxLayout(monitor)
        monitor_layout.setContentsMargins(12, 12, 12, 0)
        monitor_layout.setSpacing(0)
        self.preview_surface = VideoSurface()
        self.preview_surface.setMinimumSize(640, 420)
        monitor_layout.addWidget(self.preview_surface, 1)
        monitor_layout.addWidget(self._build_preview_footer())
        self.preview_splitter.addWidget(monitor)
        self.preview_drawer = self._build_preview_drawer()
        self.preview_splitter.addWidget(self.preview_drawer)
        self.preview_splitter.setSizes([1100, 280])
        layout.addWidget(self.preview_splitter)
        self.preview_drawer.hide()
        return page

    def _build_preview_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("preview_footer")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(7)
        self.preview_camera_label = QLabel("Connect a Sony camera in Advanced mode to unlock camera controls.")
        self.preview_camera_label.setObjectName("muted")
        layout.addWidget(self.preview_camera_label)
        layout.addStretch(1)
        preview_screenshot = QToolButton()
        preview_screenshot.setIcon(app_icon("floppy-disk"))
        preview_screenshot.setToolTip("Save monitor frame")
        preview_screenshot.clicked.connect(self.save_screenshot)
        layout.addWidget(preview_screenshot)
        self.preview_focus_button = QToolButton()
        self.preview_focus_button.setText("Focus")
        self.preview_focus_button.setToolTip("Request autofocus")
        self.preview_focus_button.pressed.connect(lambda: self.run_camera_action("focus"))
        self.preview_focus_button.released.connect(lambda: self.run_camera_action("release_focus", quiet=True))
        layout.addWidget(self.preview_focus_button)
        self.preview_zoom_out_button = QToolButton()
        self.preview_zoom_out_button.setIcon(app_icon("magnifying-glass-minus"))
        self.preview_zoom_out_button.setToolTip("Zoom out")
        self.preview_zoom_out_button.pressed.connect(lambda: self.run_camera_action("zoom_out"))
        self.preview_zoom_out_button.released.connect(lambda: self.run_camera_action("zoom_stop", quiet=True))
        layout.addWidget(self.preview_zoom_out_button)
        self.preview_zoom_in_button = QToolButton()
        self.preview_zoom_in_button.setIcon(app_icon("magnifying-glass-plus"))
        self.preview_zoom_in_button.setToolTip("Zoom in")
        self.preview_zoom_in_button.pressed.connect(lambda: self.run_camera_action("zoom_in"))
        self.preview_zoom_in_button.released.connect(lambda: self.run_camera_action("zoom_stop", quiet=True))
        layout.addWidget(self.preview_zoom_in_button)
        self.preview_record_button = QPushButton("Start camera record")
        self.preview_record_button.setIcon(app_icon("video"))
        self.preview_record_button.clicked.connect(self.toggle_camera_recording)
        layout.addWidget(self.preview_record_button)
        return footer

    def _build_preview_drawer(self) -> QWidget:
        drawer = QFrame()
        drawer.setObjectName("preview_drawer")
        drawer.setMinimumWidth(260)
        drawer.setMaximumWidth(300)
        layout = QVBoxLayout(drawer)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("Quick monitor")
        title.setObjectName("brand")
        layout.addWidget(title)
        self.preview_camera_status = QLabel("No Sony camera connected")
        self.preview_camera_status.setObjectName("muted")
        self.preview_camera_status.setWordWrap(True)
        layout.addWidget(self.preview_camera_status)
        self.preview_live_view_button = QPushButton("Start camera live view")
        self.preview_live_view_button.setObjectName("primary")
        self.preview_live_view_button.clicked.connect(self.start_camera_live_view)
        layout.addWidget(self.preview_live_view_button)

        layout.addWidget(QLabel("Monitor setup"))
        self.preview_preset_select = QComboBox()
        self.preview_preset_select.addItems(MONITOR_PRESETS)
        self.preview_preset_select.currentTextChanged.connect(self.apply_monitor_preset)
        layout.addWidget(self.preview_preset_select)

        quick_tools = QGridLayout()
        quick_tools.setHorizontalSpacing(6)
        quick_tools.setVerticalSpacing(6)
        self.preview_assist_buttons: dict[str, QToolButton] = {}
        for index, (key, title) in enumerate((("zebra", "Zebra"), ("peaking", "Peaking"), ("guide", "Guides"))):
            button = QToolButton()
            button.setText(title)
            button.setCheckable(True)
            button.setProperty("quick", True)
            button.toggled.connect(lambda checked, name=key: self._set_assist(name, checked))
            quick_tools.addWidget(button, index // 2, index % 2)
            self.preview_assist_buttons[key] = button
        layout.addLayout(quick_tools)

        layout.addWidget(QLabel("Preview look"))
        self.preview_look_select = QComboBox()
        self.preview_look_select.addItems([*BUILTIN_LOOK_NAMES, "Custom LUT"])
        self.preview_look_select.currentTextChanged.connect(self._on_look_selected)
        layout.addWidget(self.preview_look_select)
        self.preview_lut_amount_label = QLabel("Look strength: 100%")
        self.preview_lut_amount_label.setObjectName("muted")
        layout.addWidget(self.preview_lut_amount_label)
        self.preview_lut_amount_slider = self._slider(0, 100, 100, self._on_lut_amount_changed)
        layout.addWidget(self.preview_lut_amount_slider)
        load_lut = QPushButton("Load custom LUT")
        load_lut.clicked.connect(self.pick_lut)
        layout.addWidget(load_lut)
        layout.addStretch(1)
        return drawer

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)
        brand = QLabel("MONITOR DESKTOP")
        brand.setObjectName("brand")
        brand.setStyleSheet("color: #f4f7f2; font-size: 15px; font-weight: 700; background: transparent;")
        layout.addWidget(brand)
        self.connection_label = QLabel("NO SOURCE")
        self.connection_label.setObjectName("muted")
        self.connection_label.setStyleSheet("color: #8f9b93; background: transparent;")
        layout.addWidget(self.connection_label)
        layout.addStretch(1)
        self.mode_group = QButtonGroup(self)
        self.preview_mode_button = QToolButton()
        self.preview_mode_button.setText("Preview")
        self.preview_mode_button.setCheckable(True)
        self.preview_mode_button.setProperty("mode", True)
        self.preview_mode_button.clicked.connect(lambda: self.set_mode("preview"))
        self.mode_group.addButton(self.preview_mode_button)
        layout.addWidget(self.preview_mode_button)
        self.advanced_mode_button = QToolButton()
        self.advanced_mode_button.setText("Advanced")
        self.advanced_mode_button.setCheckable(True)
        self.advanced_mode_button.setProperty("mode", True)
        self.advanced_mode_button.clicked.connect(lambda: self.set_mode("advanced"))
        self.mode_group.addButton(self.advanced_mode_button)
        layout.addWidget(self.advanced_mode_button)
        self.preview_tools_button = QToolButton()
        self.preview_tools_button.setText("Tools")
        self.preview_tools_button.setCheckable(True)
        self.preview_tools_button.setToolTip("Show quick preview controls")
        self.preview_tools_button.toggled.connect(self.toggle_preview_tools)
        layout.addWidget(self.preview_tools_button)
        self.timecode_label = QLabel("00:00:00")
        self.timecode_label.setObjectName("timecode")
        self.timecode_label.setStyleSheet("color: #e9c66a; font-family: Menlo, SF Mono, monospace; font-size: 14px; font-weight: 700; background: transparent;")
        layout.addWidget(self.timecode_label)
        self.settings_button = QToolButton()
        self.settings_button.setFixedSize(32, 30)
        self.settings_button.setIcon(app_icon("gear"))
        self.settings_button.setToolTip("Settings")
        self.settings_button.clicked.connect(self.show_settings)
        layout.addWidget(self.settings_button)
        self.record_button = QPushButton("Record monitor")
        self.record_button.setIcon(app_icon("circle"))
        self.record_button.clicked.connect(self.toggle_recording)
        layout.addWidget(self.record_button)
        return bar

    def set_mode(self, mode: str, announce: bool = True) -> None:
        preview = mode == "preview"
        self.mode_stack.setCurrentIndex(0 if preview else 1)
        self.preview_mode_button.setChecked(preview)
        self.advanced_mode_button.setChecked(not preview)
        self.preview_tools_button.setVisible(preview)
        self.status_label.setVisible(not preview)
        if preview and self.latest_frame is not None:
            self.preview_surface.present(self.latest_frame)
        if announce:
            self._notify("Preview mode" if preview else "Advanced mode")

    def toggle_preview_tools(self, visible: bool) -> None:
        self.preview_drawer.setVisible(visible)
        if visible:
            self.preview_splitter.setSizes([max(720, self.preview_splitter.width() - 280), 280])

    def _build_left_sidebar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("sidebar")
        frame.setMinimumWidth(260)
        frame.setMaximumWidth(360)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(10, 8, 10, 10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._build_source_group())
        layout.addWidget(self._build_camera_connection_group())
        layout.addWidget(self._build_assist_group())
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return frame

    def _build_source_group(self) -> QGroupBox:
        group = QGroupBox("Video source")
        layout = QVBoxLayout(group)
        layout.setSpacing(7)
        self.source_kind = QComboBox()
        self.source_kind.addItems(["UVC / HDMI capture", "RTSP or HTTP stream", "Video file"])
        self.source_kind.currentIndexChanged.connect(self._update_source_placeholder)
        layout.addWidget(self.source_kind)
        self.source_input = QLineEdit("0")
        self.source_input.setPlaceholderText("Camera index, device path, or stream URL")
        self.source_input.returnPressed.connect(self.connect_source)
        layout.addWidget(self.source_input)
        buttons = QHBoxLayout()
        connect = QPushButton("Connect")
        connect.setObjectName("primary")
        connect.setIcon(app_icon("plug"))
        connect.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        connect.clicked.connect(self.connect_source)
        buttons.addWidget(connect)
        open_button = QToolButton()
        open_button.setFixedWidth(32)
        open_button.setIcon(app_icon("folder-open"))
        open_button.setToolTip("Choose video file")
        open_button.clicked.connect(self.pick_video_file)
        buttons.addWidget(open_button)
        disconnect_button = QToolButton()
        disconnect_button.setFixedWidth(32)
        disconnect_button.setIcon(app_icon("link-slash"))
        disconnect_button.setToolTip("Disconnect video source")
        disconnect_button.clicked.connect(self.disconnect_source)
        buttons.addWidget(disconnect_button)
        layout.addLayout(buttons)
        return group

    def _build_camera_connection_group(self) -> QGroupBox:
        group = QGroupBox("Sony camera")
        layout = QVBoxLayout(group)
        layout.setSpacing(7)
        self.backend_select = QComboBox()
        self.backend_select.addItems(["Sony Wi-Fi Remote API", "gphoto2 USB", "Camera Remote SDK server"])
        if GPhotoBackend.installed():
            self.backend_select.setCurrentIndex(1)
        self.backend_select.currentIndexChanged.connect(self._update_backend_hint)
        layout.addWidget(self.backend_select)
        self.endpoint_input = QLineEdit()
        layout.addWidget(self.endpoint_input)
        self.camera_devices = QComboBox()
        self.camera_devices.setPlaceholderText("Discover a camera")
        layout.addWidget(self.camera_devices)
        controls = QVBoxLayout()
        discover = QPushButton("Discover")
        discover.setIcon(app_icon("magnifying-glass"))
        discover.clicked.connect(self.discover_camera)
        controls.addWidget(discover)
        connect = QPushButton("Connect camera")
        connect.setObjectName("primary")
        connect.setIcon(app_icon("plug"))
        connect.clicked.connect(self.connect_camera)
        controls.addWidget(connect)
        layout.addLayout(controls)
        self.camera_connection_status = QLabel()
        self.camera_connection_status.setObjectName("muted")
        self.camera_connection_status.setWordWrap(True)
        layout.addWidget(self.camera_connection_status)
        self._update_backend_hint()
        return group

    def _build_assist_group(self) -> QGroupBox:
        group = QGroupBox("Monitor assists")
        layout = QVBoxLayout(group)
        layout.setSpacing(7)
        layout.addWidget(QLabel("Monitor setup"))
        self.monitor_preset_select = QComboBox()
        self.monitor_preset_select.addItems(MONITOR_PRESETS)
        self.monitor_preset_select.currentTextChanged.connect(self.apply_monitor_preset)
        layout.addWidget(self.monitor_preset_select)
        toggle_grid = QGridLayout()
        toggle_grid.setHorizontalSpacing(6)
        toggle_grid.setVerticalSpacing(6)
        self.assist_buttons: dict[str, QToolButton] = {}
        for index, (key, title) in enumerate(
            [("zebra", "Zebra"), ("false_color", "False color"), ("peaking", "Peaking"), ("guide", "Frame guides"), ("flip", "Mirror")]
        ):
            button = QToolButton()
            button.setText(title)
            button.setCheckable(True)
            button.setProperty("assist", True)
            button.toggled.connect(lambda checked, name=key: self._set_assist(name, checked))
            toggle_grid.addWidget(button, index // 2, index % 2)
            self.assist_buttons[key] = button
        layout.addLayout(toggle_grid)
        self.zebra_text = QLabel("Zebra level: 95 IRE")
        layout.addWidget(self.zebra_text)
        self.zebra_slider = self._slider(50, 100, 95, self._on_zebra_changed)
        layout.addWidget(self.zebra_slider)
        self.peaking_text = QLabel("Peaking threshold: 55")
        layout.addWidget(self.peaking_text)
        self.peaking_slider = self._slider(10, 120, 55, self._on_peaking_changed)
        layout.addWidget(self.peaking_slider)
        desqueeze_row = QHBoxLayout()
        desqueeze_row.addWidget(QLabel("Desqueeze"))
        self.desqueeze_select = QComboBox()
        self.desqueeze_select.addItems(["1.00x", "1.33x", "1.50x", "1.80x", "2.00x"])
        self.desqueeze_select.currentIndexChanged.connect(self._sync_monitor_settings)
        desqueeze_row.addWidget(self.desqueeze_select, 1)
        layout.addLayout(desqueeze_row)
        layout.addWidget(QLabel("Preview look"))
        self.look_select = QComboBox()
        self.look_select.addItems([*BUILTIN_LOOK_NAMES, "Custom LUT"])
        self.look_select.currentTextChanged.connect(self._on_look_selected)
        layout.addWidget(self.look_select)
        self.lut_amount_label = QLabel("Look strength: 100%")
        self.lut_amount_label.setObjectName("muted")
        layout.addWidget(self.lut_amount_label)
        self.lut_amount_slider = self._slider(0, 100, 100, self._on_lut_amount_changed)
        layout.addWidget(self.lut_amount_slider)
        lut_row = QHBoxLayout()
        self.lut_label = QLabel("Neutral preview")
        self.lut_label.setObjectName("muted")
        lut_row.addWidget(self.lut_label, 1)
        lut_button = QPushButton("Load LUT")
        lut_button.clicked.connect(self.pick_lut)
        lut_row.addWidget(lut_button)
        layout.addLayout(lut_row)
        clear_lut = QPushButton("Clear custom LUT")
        clear_lut.clicked.connect(self.clear_custom_lut)
        layout.addWidget(clear_lut)
        return group

    def _build_monitor_area(self) -> QWidget:
        area = QWidget()
        layout = QVBoxLayout(area)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.video_surface = VideoSurface()
        layout.addWidget(self.video_surface, 1)
        transport = QFrame()
        transport.setObjectName("transport")
        transport_layout = QHBoxLayout(transport)
        transport_layout.setContentsMargins(8, 6, 8, 6)
        transport_label = QLabel("ADVANCED MONITOR")
        transport_label.setObjectName("preview_label")
        transport_layout.addWidget(transport_label)
        transport_layout.addStretch(1)
        screenshot = QToolButton()
        screenshot.setIcon(app_icon("floppy-disk"))
        screenshot.setToolTip("Save monitor frame")
        screenshot.clicked.connect(self.save_screenshot)
        transport_layout.addWidget(screenshot)
        layout.addWidget(transport)
        return area

    def _build_right_sidebar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("sidebar")
        frame.setMinimumWidth(350)
        frame.setMaximumWidth(380)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(10, 8, 10, 10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._build_camera_controls_group())
        layout.addWidget(self._build_scopes_group())
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return frame

    def _build_camera_controls_group(self) -> QGroupBox:
        group = QGroupBox("Camera controls")
        layout = QVBoxLayout(group)
        layout.setSpacing(7)
        self.active_camera_label = QLabel("Connect a supported Sony camera to enable controls.")
        self.active_camera_label.setObjectName("muted")
        self.active_camera_label.setWordWrap(True)
        layout.addWidget(self.active_camera_label)
        self.live_view_button = QPushButton("Start camera live view")
        self.live_view_button.setIcon(app_icon("video"))
        self.live_view_button.clicked.connect(self.start_camera_live_view)
        layout.addWidget(self.live_view_button)
        self.camera_preset_select = QComboBox()
        self._refresh_camera_preset_select()
        layout.addWidget(self.camera_preset_select)
        preset_actions = QHBoxLayout()
        self.camera_preset_button = QPushButton("Apply setup")
        self.camera_preset_button.clicked.connect(self.apply_camera_preset)
        preset_actions.addWidget(self.camera_preset_button, 1)
        self.save_camera_preset_button = QToolButton()
        self.save_camera_preset_button.setFixedWidth(32)
        self.save_camera_preset_button.setIcon(app_icon("floppy-disk"))
        self.save_camera_preset_button.setToolTip("Save current camera settings as a custom setup")
        self.save_camera_preset_button.clicked.connect(self.save_current_camera_preset)
        preset_actions.addWidget(self.save_camera_preset_button)
        self.delete_camera_preset_button = QToolButton()
        self.delete_camera_preset_button.setFixedWidth(32)
        self.delete_camera_preset_button.setIcon(app_icon("trash-can"))
        self.delete_camera_preset_button.setToolTip("Delete selected custom setup")
        self.delete_camera_preset_button.clicked.connect(self.delete_selected_camera_preset)
        preset_actions.addWidget(self.delete_camera_preset_button)
        layout.addLayout(preset_actions)
        action_row = QHBoxLayout()
        self.focus_button = QPushButton("Focus")
        self.focus_button.setIcon(app_icon("crosshairs"))
        self.focus_button.pressed.connect(lambda: self.run_camera_action("focus"))
        self.focus_button.released.connect(lambda: self.run_camera_action("release_focus", quiet=True))
        action_row.addWidget(self.focus_button)
        self.photo_button = QPushButton("Take photo")
        self.photo_button.setIcon(app_icon("camera"))
        self.photo_button.clicked.connect(lambda: self.run_camera_action("photo"))
        action_row.addWidget(self.photo_button)
        layout.addLayout(action_row)
        zoom_row = QHBoxLayout()
        self.zoom_out_button = QPushButton("Zoom out")
        self.zoom_out_button.setIcon(app_icon("magnifying-glass-minus"))
        self.zoom_out_button.pressed.connect(lambda: self.run_camera_action("zoom_out"))
        self.zoom_out_button.released.connect(lambda: self.run_camera_action("zoom_stop", quiet=True))
        zoom_row.addWidget(self.zoom_out_button)
        self.zoom_in_button = QPushButton("Zoom in")
        self.zoom_in_button.setIcon(app_icon("magnifying-glass-plus"))
        self.zoom_in_button.pressed.connect(lambda: self.run_camera_action("zoom_in"))
        self.zoom_in_button.released.connect(lambda: self.run_camera_action("zoom_stop", quiet=True))
        zoom_row.addWidget(self.zoom_in_button)
        layout.addLayout(zoom_row)
        self.camera_record_button = QPushButton("Start camera record")
        self.camera_record_button.setIcon(app_icon("video"))
        self.camera_record_button.clicked.connect(self.toggle_camera_recording)
        layout.addWidget(self.camera_record_button)
        form = QVBoxLayout()
        form.setSpacing(4)
        self.camera_setting_boxes: dict[str, CameraSettingControl] = {}
        settings = {
            "iso": ["Auto", "100", "200", "400", "800", "1600", "3200", "6400"],
            "shutter": ["1/25", "1/50", "1/60", "1/100", "1/125", "1/250"],
            "aperture": ["1.4", "1.8", "2.8", "4.0", "5.6", "8.0", "11"],
            "white_balance": ["Auto", "Daylight", "Cloudy", "Incandescent", "Fluorescent"],
            "focus_mode": ["AF-S", "AF-C", "MF"],
        }
        for name, values in settings.items():
            control = CameraSettingControl(name, values, self.set_camera_setting, self._invalid_camera_setting_value)
            label = QLabel(name.replace("_", " ").title())
            label.setObjectName("muted")
            form.addWidget(label)
            form.addWidget(control)
            self.camera_setting_boxes[name] = control
        layout.addLayout(form)
        self._set_camera_controls_enabled(False)
        return group

    def _build_scopes_group(self) -> QGroupBox:
        group = QGroupBox("Scopes")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)
        self.histogram_view = self._scope_view()
        self.waveform_view = self._scope_view()
        self.vectorscope_view = self._scope_view()
        for title, view in (("Histogram", self.histogram_view), ("Waveform", self.waveform_view), ("Vectorscope", self.vectorscope_view)):
            label = QLabel(title)
            label.setObjectName("muted")
            layout.addWidget(label)
            layout.addWidget(view)
        return group

    @staticmethod
    def _scope_view() -> ScopeView:
        return ScopeView()

    @staticmethod
    def _slider(minimum: int, maximum: int, value: int, callback: Any) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.valueChanged.connect(callback)
        return slider

    def _update_source_placeholder(self) -> None:
        values = [("0", "Camera index or /dev/video path"), ("rtsp://", "RTSP, HTTP, or MJPEG URL"), ("", "Choose a video file")]
        value, hint = values[self.source_kind.currentIndex()]
        if not self.source_input.text() or self.source_input.text() in {"0", "rtsp://"}:
            self.source_input.setText(value)
        self.source_input.setPlaceholderText(hint)

    def _update_backend_hint(self) -> None:
        hints = [
            "Join camera Wi-Fi, then Discover or enter its IP address.",
            "USB Sony control: turn the camera on, set PC Remote mode, then select Discover.",
            "Enter a local Camera Remote SDK REST server URL.",
        ]
        hint = hints[self.backend_select.currentIndex()]
        self.endpoint_input.setPlaceholderText(hint)
        self.endpoint_input.setEnabled(self.backend_select.currentIndex() != 1)
        self.camera_connection_status.setText(hint)

    def _on_zebra_changed(self) -> None:
        self.zebra_text.setText(f"Zebra level: {self.zebra_slider.value()} IRE")
        self._sync_monitor_settings()

    def _on_peaking_changed(self) -> None:
        self.peaking_text.setText(f"Peaking threshold: {self.peaking_slider.value()}")
        self._sync_monitor_settings()

    def _sync_monitor_settings(self) -> None:
        for key, button in self.assist_buttons.items():
            setattr(self.settings, key, button.isChecked())
        self.settings.zebra_level = self.zebra_slider.value()
        self.settings.peaking_strength = self.peaking_slider.value()
        self.settings.desqueeze = float(self.desqueeze_select.currentText().rstrip("x"))

    def _set_assist(self, key: str, enabled: bool) -> None:
        for buttons in (self.assist_buttons, self.preview_assist_buttons):
            button = buttons.get(key)
            if button is None or button.isChecked() == enabled:
                continue
            was_blocked = button.blockSignals(True)
            button.setChecked(enabled)
            button.blockSignals(was_blocked)
        self._sync_monitor_settings()

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        was_blocked = combo.blockSignals(True)
        combo.setCurrentText(value)
        combo.blockSignals(was_blocked)

    @staticmethod
    def _set_slider_value(slider: QSlider, value: int) -> None:
        was_blocked = slider.blockSignals(True)
        slider.setValue(value)
        slider.blockSignals(was_blocked)

    def _set_monitor_preset_value(self, name: str) -> None:
        self._set_combo_value(self.monitor_preset_select, name)
        self._set_combo_value(self.preview_preset_select, name)

    def apply_monitor_preset(self, name: str) -> None:
        preset = MONITOR_PRESETS.get(name)
        if preset is None:
            return
        for key in ("zebra", "false_color", "peaking", "guide", "flip"):
            self._set_assist(key, bool(preset[key]))
        self._set_combo_value(self.desqueeze_select, str(preset["desqueeze"]))
        self._apply_builtin_look(str(preset["look"]), notify=False)
        self._set_monitor_preset_value(name)
        self._sync_monitor_settings()
        self._notify(f"Monitor setup: {name}.")

    def _on_look_selected(self, name: str) -> None:
        if name == "Custom LUT":
            if self.current_look != "Custom LUT":
                self.pick_lut()
            return
        self._apply_builtin_look(name)

    def _apply_builtin_look(self, name: str, notify: bool = True) -> None:
        self.current_lut = built_in_lut(name)
        self.current_look = name
        self.settings.lut_path = ""
        self.lut_label.setText("Neutral preview" if name == "Neutral" else f"Built-in look: {name}")
        self._sync_look_controls()
        if notify:
            self._notify(f"Preview look: {name}.")

    def _on_lut_amount_changed(self, amount: int) -> None:
        self.settings.lut_amount = amount / 100
        self.lut_amount_label.setText(f"Look strength: {amount}%")
        self.preview_lut_amount_label.setText(f"Look strength: {amount}%")
        for slider in (self.lut_amount_slider, self.preview_lut_amount_slider):
            if slider.value() != amount:
                self._set_slider_value(slider, amount)

    def _sync_look_controls(self) -> None:
        self._set_combo_value(self.look_select, self.current_look)
        self._set_combo_value(self.preview_look_select, self.current_look)
        self._on_lut_amount_changed(round(self.settings.lut_amount * 100))

    def clear_custom_lut(self) -> None:
        self._apply_builtin_look("Neutral", notify=False)
        self._notify("Custom LUT cleared.")

    def pick_video_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Open video source", str(Path.home()), "Video files (*.mp4 *.mov *.mkv *.avi *.m4v);;All files (*)")
        if filename:
            self.source_kind.setCurrentIndex(2)
            self.source_input.setText(filename)
            self.connect_source()

    def connect_source(self) -> None:
        source_value = self.source_input.text().strip()
        if not source_value:
            self._notify("Enter a source before connecting.", error=True)
            return
        source = parse_source(source_value)
        self._release_capture()
        capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            capture.release()
            self._notify(f"Could not open video source: {source_value}", error=True)
            return
        self.capture = capture
        self.capture_is_file = self.source_kind.currentIndex() == 2 or Path(source_value).is_file()
        self.connection_label.setText("VIDEO CONNECTED")
        if self.source_kind.currentIndex() == 0 and source == 0:
            self._notify("Connected to capture device 0. For Sony USB control, use gphoto2 USB in the Sony camera panel.")
        else:
            self._notify(f"Connected to {source_value}.")

    def _set_capture(self, capture: cv2.VideoCapture | GPhotoLiveCapture, label: str) -> None:
        self._release_capture()
        self.capture = capture
        self.capture_is_file = False
        self.connection_label.setText(label.upper())

    def disconnect_source(self) -> None:
        self._release_capture()
        self.connection_label.setText("NO SOURCE")
        self._show_idle_frame()
        self._notify("Video source disconnected.")

    def _release_capture(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        if self.writer is not None:
            self.writer.release()
            self.writer = None
            self._set_record_button(False)

    def _populate_camera_devices(self, devices: list[CameraDevice]) -> None:
        self.discovered_devices = devices
        self.camera_devices.clear()
        for device in devices:
            self.camera_devices.addItem(device.name, device)
        if devices:
            self.camera_devices.setCurrentIndex(0)

    def discover_camera(self) -> None:
        try:
            backend, devices = self._discover_selected_backend()
        except CameraError as exc:
            self._notify(str(exc), error=True)
            return
        self.active_backend = backend
        self._populate_camera_devices(devices)
        if devices:
            self.camera_devices.setCurrentIndex(0)
            self.camera_connection_status.setText(f"Found {len(devices)} camera(s). Select one and connect.")
            self._notify(f"Found {len(devices)} camera(s).")
        else:
            self.camera_connection_status.setText("No camera found. Check the connection or enter its address.")
            self._notify("No camera was discovered.", error=True)

    def auto_connect_usb_camera(self) -> None:
        """Connect exactly one detected USB camera without changing its settings."""
        if self.active_backend is not None or not GPhotoBackend.installed():
            return
        backend = GPhotoBackend()
        try:
            devices = backend.discover()
        except CameraError:
            return
        if len(devices) != 1:
            if devices:
                self._populate_camera_devices(devices)
                self.camera_connection_status.setText(f"Found {len(devices)} USB cameras. Choose one in Advanced mode.")
            return
        device = devices[0]
        self._populate_camera_devices(devices)
        try:
            backend.connect(device)
        except CameraError:
            return
        self._complete_camera_connection(backend, device, automatic=True)
        self.start_camera_live_view(automatic=True)

    def _discover_selected_backend(self) -> tuple[GPhotoBackend | SonyRemoteApiBackend | SonySdkServerBackend, list[CameraDevice]]:
        index = self.backend_select.currentIndex()
        endpoint = self.endpoint_input.text().strip()
        if index == 0:
            backend = SonyRemoteApiBackend(endpoint)
            devices = SonyRemoteApiBackend.discover()
            if not devices and endpoint:
                devices = [CameraDevice(backend.endpoint, "Sony camera at entered address", backend.name, backend.endpoint)]
            return backend, devices
        if index == 1:
            backend = GPhotoBackend()
            return backend, backend.discover()
        backend = SonySdkServerBackend(endpoint or "http://127.0.0.1:8080")
        return backend, backend.discover()

    def connect_camera(self) -> None:
        index = self.backend_select.currentIndex()
        expected = (SonyRemoteApiBackend, GPhotoBackend, SonySdkServerBackend)[index]
        try:
            if index == 0 and self.camera_devices.count() == 0:
                backend = SonyRemoteApiBackend(self.endpoint_input.text())
                backend.connect()
                device = CameraDevice(backend.endpoint, "Sony Wi-Fi camera", backend.name, backend.endpoint)
            else:
                if not isinstance(self.active_backend, expected) or not self.discovered_devices:
                    backend, devices = self._discover_selected_backend()
                    self.active_backend = backend
                    self._populate_camera_devices(devices)
                if not self.discovered_devices or self.active_backend is None:
                    raise CameraError("No camera is available to connect.")
                device = self.camera_devices.currentData()
                if not isinstance(device, CameraDevice):
                    device = self.discovered_devices[0]
                backend = self.active_backend
                if isinstance(backend, SonyRemoteApiBackend):
                    backend.connect(device.endpoint)
                else:
                    backend.connect(device)
        except CameraError as exc:
            self._set_camera_controls_enabled(False)
            self._notify(str(exc), error=True)
            return

        self._complete_camera_connection(backend, device)

    def _complete_camera_connection(
        self,
        backend: GPhotoBackend | SonyRemoteApiBackend | SonySdkServerBackend,
        device: CameraDevice,
        automatic: bool = False,
    ) -> None:
        self.active_backend = backend
        self.active_camera_label.setText(f"{device.name}\n{backend.name}")
        self.preview_camera_label.setText(f"{device.name} connected")
        self.preview_camera_status.setText(f"Connected via {backend.name}")
        self.camera_connection_status.setText(f"Connected: {device.name}")
        self._set_camera_controls_enabled(True)
        self._load_available_settings()
        self._notify(f"{'Auto-connected to' if automatic else 'Connected to'} {device.name}.")

    def _load_available_settings(self) -> None:
        if not isinstance(self.active_backend, (SonyRemoteApiBackend, GPhotoBackend)):
            return
        for name, combo in self.camera_setting_boxes.items():
            values = self.active_backend.available_values(name)
            if not values:
                if isinstance(self.active_backend, GPhotoBackend):
                    self._set_camera_setting_enabled(combo, False)
                continue
            current = self.active_backend.current_value(name) if isinstance(self.active_backend, GPhotoBackend) else combo.currentText()
            combo.set_values(values, current)
            if isinstance(self.active_backend, GPhotoBackend):
                self._set_camera_setting_enabled(combo, self.active_backend.property_writable(name))

    @staticmethod
    def _set_camera_setting_enabled(control: CameraSettingControl, enabled: bool) -> None:
        control.setEnabled(enabled)

    def _set_camera_controls_enabled(self, enabled: bool) -> None:
        self.live_view_button.setEnabled(enabled)
        self.preview_live_view_button.setEnabled(enabled)
        self.focus_button.setEnabled(enabled)
        self.preview_focus_button.setEnabled(enabled)
        self.photo_button.setEnabled(enabled)
        self.zoom_out_button.setEnabled(enabled)
        self.zoom_in_button.setEnabled(enabled)
        self.preview_zoom_out_button.setEnabled(enabled)
        self.preview_zoom_in_button.setEnabled(enabled)
        self.camera_record_button.setEnabled(enabled)
        self.preview_record_button.setEnabled(enabled)
        self.camera_preset_select.setEnabled(enabled)
        self.camera_preset_button.setEnabled(enabled)
        self.save_camera_preset_button.setEnabled(enabled)
        self.delete_camera_preset_button.setEnabled(enabled)
        for control in self.camera_setting_boxes.values():
            control.setEnabled(enabled)

    def run_camera_action(self, action: str, quiet: bool = False) -> bool:
        if self.active_backend is None:
            return False
        try:
            if isinstance(self.active_backend, GPhotoBackend):
                message = self.active_backend.action(action, self._output_dir())
            else:
                message = self.active_backend.action(action)
        except CameraError as exc:
            if not quiet:
                self._notify(str(exc), error=True)
            return False
        if not quiet:
            self._notify(message)
        return True

    def toggle_camera_recording(self) -> None:
        starting = not self.camera_recording
        if not self.run_camera_action("record_start" if starting else "record_stop"):
            return
        self.camera_recording = starting
        self._set_camera_record_buttons()

    def _set_camera_record_buttons(self) -> None:
        for button in (self.camera_record_button, self.preview_record_button):
            button.setText("Stop camera record" if self.camera_recording else "Start camera record")
            button.setIcon(app_icon("stop" if self.camera_recording else "video"))
            button.setObjectName("recording" if self.camera_recording else "")
            button.style().unpolish(button)
            button.style().polish(button)

    def set_camera_setting(self, name: str, value: str) -> None:
        if self.active_backend is None:
            return
        try:
            self._notify(self.active_backend.set_property(name, value))
        except CameraError as exc:
            self._notify(str(exc), error=True)

    def _invalid_camera_setting_value(self, name: str) -> None:
        self._notify(f"Choose a value supported by the camera for {name.replace('_', ' ')}.", error=True)

    @staticmethod
    def _matching_camera_value(control: CameraSettingControl, requested: str) -> str | None:
        values = control.values()
        if requested in values:
            return requested
        requested_lower = requested.casefold()
        for value in values:
            lowered = value.casefold()
            if lowered == requested_lower or (requested_lower == "auto" and lowered.startswith("auto")):
                return value
        return None

    def _refresh_camera_preset_select(self, selected: str | None = None) -> None:
        if selected is None and hasattr(self, "camera_preset_select"):
            selected = self.camera_preset_select.currentText()
        names = [*CAMERA_PRESETS, *(f"{CUSTOM_CAMERA_PRESET_PREFIX}{name}" for name in self.custom_camera_presets)]
        was_blocked = self.camera_preset_select.blockSignals(True)
        self.camera_preset_select.clear()
        self.camera_preset_select.addItems(names)
        if selected in names:
            self.camera_preset_select.setCurrentText(selected)
        self.camera_preset_select.blockSignals(was_blocked)

    def _selected_custom_camera_preset(self) -> tuple[str, dict[str, str]] | None:
        selected = self.camera_preset_select.currentText()
        if not selected.startswith(CUSTOM_CAMERA_PRESET_PREFIX):
            return None
        name = selected.removeprefix(CUSTOM_CAMERA_PRESET_PREFIX)
        values = self.custom_camera_presets.get(name)
        return (name, values) if values else None

    def _camera_preset_values(self) -> tuple[str, dict[str, str]] | None:
        selected = self.camera_preset_select.currentText()
        if selected in CAMERA_PRESETS:
            return selected, CAMERA_PRESETS[selected]
        return self._selected_custom_camera_preset()

    def _write_custom_camera_presets(self) -> bool:
        try:
            save_custom_camera_presets(self.preset_path, self.custom_camera_presets)
        except OSError as exc:
            self._notify(f"Could not save custom setups: {exc}", error=True)
            return False
        return True

    def save_current_camera_preset(self) -> None:
        if self.active_backend is None:
            self._notify("Connect a camera before saving a custom setup.", error=True)
            return
        values = {
            setting: control.currentText()
            for setting, control in self.camera_setting_boxes.items()
            if control.isEnabled() and control.currentText()
        }
        if not values:
            self._notify("This camera has no writable settings to save.", error=True)
            return
        name, accepted = QInputDialog.getText(self, "Save custom setup", "Setup name")
        name = name.strip()
        if not accepted or not name:
            return
        existing_name = next((item for item in self.custom_camera_presets if item.casefold() == name.casefold()), name)
        updating = existing_name in self.custom_camera_presets
        self.custom_camera_presets[existing_name] = values
        if not self._write_custom_camera_presets():
            return
        selected = f"{CUSTOM_CAMERA_PRESET_PREFIX}{existing_name}"
        self.custom_camera_presets = load_custom_camera_presets(self.preset_path)
        self._refresh_camera_preset_select(selected)
        self._notify(f"{'Updated' if updating else 'Saved'} custom setup: {existing_name}.")

    def delete_selected_camera_preset(self) -> None:
        selected = self._selected_custom_camera_preset()
        if selected is None:
            self._notify("Choose a custom setup to delete.", error=True)
            return
        name, _ = selected
        choice = QMessageBox.question(
            self,
            "Delete custom setup",
            f"Delete custom setup '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        del self.custom_camera_presets[name]
        if not self._write_custom_camera_presets():
            return
        self._refresh_camera_preset_select()
        self._notify(f"Deleted custom setup: {name}.")

    def apply_camera_preset(self) -> None:
        if self.active_backend is None:
            self._notify("Connect a camera before applying a camera setup.", error=True)
            return
        preset = self._camera_preset_values()
        if preset is None:
            self._notify("Choose a camera setup to apply.", error=True)
            return
        name, requested = preset
        if not requested:
            self._notify("Camera setup makes no changes.")
            return
        applied: list[str] = []
        skipped: list[str] = []
        for setting, value in requested.items():
            combo = self.camera_setting_boxes[setting]
            camera_value = self._matching_camera_value(combo, value)
            if not camera_value or not combo.isEnabled():
                skipped.append(setting.replace("_", " "))
                continue
            try:
                self.active_backend.set_property(setting, camera_value)
            except CameraError:
                skipped.append(setting.replace("_", " "))
                continue
            combo.setCurrentText(camera_value)
            applied.append(setting.replace("_", " "))
        if applied:
            suffix = f" Skipped: {', '.join(skipped)}." if skipped else ""
            self._notify(f"Applied {name}: {', '.join(applied)}.{suffix}")
        else:
            self._notify(f"{name} is not available on this camera.", error=True)

    def start_camera_live_view(self, automatic: bool = False) -> None:
        if self.active_backend is None:
            self._notify("Connect a camera before starting its live view.", error=True)
            return
        try:
            capture_or_url = self.active_backend.start_live_view()
        except CameraError as exc:
            self._notify(str(exc), error=True)
            return
        if isinstance(capture_or_url, str):
            self.source_kind.setCurrentIndex(1)
            self.source_input.setText(capture_or_url)
            self.connect_source()
        else:
            self._set_capture(capture_or_url, "Camera live view")
            self._notify("Auto-started camera live view." if automatic else "Camera live view started.")
        self.set_mode("preview", announce=False)

    def pick_lut(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Load LUT", str(Path.home()), "Cube LUT (*.cube)")
        if not filename:
            self._sync_look_controls()
            return
        lut = load_cube_lut(filename)
        if lut is None:
            self._notify("This LUT file could not be read.", error=True)
            return
        self.current_lut = lut
        self.current_look = "Custom LUT"
        self.settings.lut_path = filename
        self.lut_label.setText(f"Custom LUT: {Path(filename).name}")
        self._sync_look_controls()
        self._notify(f"Loaded LUT: {Path(filename).name}")

    def toggle_recording(self) -> None:
        if self.writer is not None:
            self.writer.release()
            self.writer = None
            self._set_record_button(False)
            self._notify(f"Saved monitor recording: {self.recording_path.name if self.recording_path else ''}")
            return
        if self.latest_frame is None:
            self._notify("Wait for a video frame before recording.", error=True)
            return
        self._output_dir().mkdir(parents=True, exist_ok=True)
        self.recording_path = self._output_dir() / f"monitor_{dt.datetime.now():%Y%m%d_%H%M%S}.mp4"
        height, width = self.latest_frame.shape[:2]
        self.writer = cv2.VideoWriter(str(self.recording_path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (width, height))
        if not self.writer.isOpened():
            self.writer = None
            self._notify("Could not start the MP4 writer on this system.", error=True)
            return
        self._set_record_button(True)
        self._notify(f"Recording monitor feed to {self.recording_path.name}.")

    def _set_record_button(self, recording: bool) -> None:
        self.record_button.setText("Stop recording" if recording else "Record monitor")
        self.record_button.setIcon(app_icon("stop" if recording else "circle"))
        self.record_button.setObjectName("recording" if recording else "")
        self.record_button.style().unpolish(self.record_button)
        self.record_button.style().polish(self.record_button)

    def save_screenshot(self) -> None:
        if self.latest_frame is None:
            self._notify("No monitor frame is available yet.", error=True)
            return
        output = self._output_dir()
        output.mkdir(parents=True, exist_ok=True)
        filename = output / f"frame_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
        if cv2.imwrite(str(filename), self.latest_frame):
            self._notify(f"Saved frame: {filename.name}")
        else:
            self._notify("Could not save the monitor frame.", error=True)

    @staticmethod
    def _output_dir() -> Path:
        return Path.cwd() / "recordings"

    def _tick(self) -> None:
        if self.capture is None:
            return
        ok, frame = self.capture.read()
        if not ok or frame is None:
            if self.capture_is_file and isinstance(self.capture, cv2.VideoCapture):
                self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            return
        processed = process_frame(frame, self.settings, self.current_lut)
        self.latest_frame = processed
        if self.writer is not None:
            self.writer.write(processed)
        self.video_surface.present(processed)
        self.preview_surface.present(processed)
        self.frame_count += 1
        self.timecode_label.setText(dt.datetime.now().strftime("%H:%M:%S"))
        if self.frame_count % 3 == 0:
            self.histogram_view.present(make_histogram(frame))
            self.waveform_view.present(make_waveform(frame))
            self.vectorscope_view.present(make_vectorscope(frame))

    def _show_idle_frame(self) -> None:
        idle = np.zeros((720, 1280, 3), dtype=np.uint8)
        idle[:] = (7, 8, 6)
        cv2.rectangle(idle, (80, 80), (1200, 640), (45, 50, 43), 1)
        cv2.line(idle, (640, 270), (640, 450), (48, 55, 47), 1)
        cv2.line(idle, (550, 360), (730, 360), (48, 55, 47), 1)
        cv2.putText(idle, "NO VIDEO SIGNAL", (504, 520), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (154, 164, 154), 2, cv2.LINE_AA)
        self.video_surface.present(idle)
        self.preview_surface.present(idle)

    def _notify(self, message: str, error: bool = False) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #f05a5f; background: #090a0d; border-top: 1px solid #20232b;" if error else "")

    def show_settings(self) -> None:
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def start_application_update(self) -> None:
        if sys.platform == "win32":
            if self.settings_dialog is not None:
                self.settings_dialog.set_update_state("In-app updates are currently available on macOS and Linux.")
            return
        if self.update_process is not None and self.update_process.state() != QProcess.ProcessState.NotRunning:
            return
        if self.settings_dialog is None:
            self.show_settings()
        assert self.settings_dialog is not None
        self._update_output = ""
        self.settings_dialog.set_update_state("Downloading and installing the latest release...", updating=True)
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_update_output)
        process.finished.connect(self._finish_application_update)
        process.errorOccurred.connect(self._handle_update_error)
        self.update_process = process
        process.start("/bin/sh", ["-c", UPDATE_COMMAND])

    def _read_update_output(self) -> None:
        if self.update_process is None:
            return
        self._update_output += bytes(self.update_process.readAllStandardOutput()).decode("utf-8", errors="replace")

    def _finish_application_update(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if self.settings_dialog is None:
            return
        if exit_code == 0:
            message = "Update installed. Restart Monitor Desktop to use it."
            self.settings_dialog.set_update_state(message)
            self._notify(message)
        else:
            detail = next((line for line in reversed(self._update_output.splitlines()) if line.strip()), "The updater exited unexpectedly.")
            self.settings_dialog.set_update_state(f"Update failed: {detail}")
            self._notify(f"Update failed: {detail}", error=True)

    def _handle_update_error(self, _error: QProcess.ProcessError) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.set_update_state("Could not start the updater.")

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if not self._auto_connect_scheduled:
            self._auto_connect_scheduled = True
            QTimer.singleShot(400, self.auto_connect_usb_camera)

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        self._release_capture()
        if isinstance(self.active_backend, GPhotoBackend):
            self.active_backend.disconnect()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Monitor Desktop")
    app.setOrganizationName("Monitor Desktop")
    window = MonitorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
