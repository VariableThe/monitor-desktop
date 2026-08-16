"""Qt desktop application for camera monitoring and Sony control."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStyle,
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
from .video_tools import (
    MonitorSettings,
    fit_frame_to_box,
    load_cube_lut,
    make_histogram,
    make_vectorscope,
    make_waveform,
    parse_source,
    process_frame,
)


APP_STYLE = """
QMainWindow { background: #151714; color: #ecf0eb; }
QWidget { font-family: "Inter", "Segoe UI", sans-serif; font-size: 13px; }
QFrame#topbar { background: #1b1e1a; border-bottom: 1px solid #343932; }
QFrame#sidebar { background: #1b1e1a; border-color: #343932; }
QMenuBar, QMenu { background: #1b1e1a; color: #edf2eb; }
QMenuBar::item:selected, QMenu::item:selected { background: #394138; }
QLabel#brand { color: #f6f8f3; font-size: 16px; font-weight: 700; }
QLabel#muted { color: #98a198; }
QLabel#status { color: #b9c5bb; background: #1b1e1a; border-top: 1px solid #343932; }
QLabel#timecode { color: #f4ca57; font-family: "Menlo", monospace; font-size: 14px; font-weight: 700; }
QGroupBox { background: #1b1e1a; border: 1px solid #343932; border-radius: 5px; margin-top: 13px; padding: 10px 8px 8px; color: #dbe2d9; font-weight: 650; }
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px; }
QLabel { color: #dfe6df; }
QLineEdit, QComboBox { background: #242823; border: 1px solid #454c43; border-radius: 4px; color: #f1f5f0; min-height: 28px; padding: 0 8px; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #86c7b0; }
QComboBox::drop-down { border: 0; width: 24px; }
QPushButton, QToolButton { background: #2c322c; border: 1px solid #485149; border-radius: 4px; color: #edf2eb; min-height: 29px; padding: 0 9px; }
QPushButton:hover, QToolButton:hover { background: #394138; border-color: #748073; }
QPushButton:disabled, QToolButton:disabled { background: #222620; border-color: #30342f; color: #687068; }
QPushButton#primary { background: #2d806c; border-color: #45aa8d; color: #ffffff; font-weight: 650; }
QPushButton#primary:hover { background: #379a80; }
QPushButton#recording { background: #9a3937; border-color: #e2635f; color: #ffffff; font-weight: 650; }
QToolButton[assist="true"] { min-width: 72px; }
QToolButton[assist="true"]:checked { background: #2b6659; border-color: #74bfaa; color: #ffffff; }
QSlider::groove:horizontal { height: 4px; background: #454c43; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #6ab69e; border-radius: 2px; }
QSlider::handle:horizontal { background: #f1ca5b; width: 13px; margin: -5px 0; border-radius: 6px; }
QScrollArea, QScrollArea > QWidget > QWidget { background: #1b1e1a; border: 0; }
"""


class VideoSurface(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self._frame: np.ndarray | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(560, 360)
        self.setStyleSheet("background: #070806; border: 1px solid #252a24;")

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


class MonitorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
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
        self.active_backend: GPhotoBackend | SonyRemoteApiBackend | SonySdkServerBackend | None = None
        self.discovered_devices: list[CameraDevice] = []
        self.frame_count = 0

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

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_sidebar())
        splitter.addWidget(self._build_monitor_area())
        splitter.addWidget(self._build_right_sidebar())
        splitter.setSizes([300, 820, 320])
        layout.addWidget(splitter, 1)

        self.status_label = QLabel("Ready. Connect a capture device, video source, or Sony camera.")
        self.status_label.setObjectName("status")
        self.status_label.setContentsMargins(14, 7, 14, 7)
        layout.addWidget(self.status_label)
        return root

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)
        brand = QLabel("MONITOR DESKTOP")
        brand.setObjectName("brand")
        brand.setStyleSheet("color: #f6f8f3; font-size: 16px; font-weight: 700; background: transparent;")
        layout.addWidget(brand)
        self.connection_label = QLabel("NO SOURCE")
        self.connection_label.setObjectName("muted")
        self.connection_label.setStyleSheet("color: #98a198; background: transparent;")
        layout.addWidget(self.connection_label)
        layout.addStretch(1)
        self.timecode_label = QLabel("00:00:00")
        self.timecode_label.setObjectName("timecode")
        self.timecode_label.setStyleSheet("color: #f4ca57; font-family: Menlo, monospace; font-size: 14px; font-weight: 700; background: transparent;")
        layout.addWidget(self.timecode_label)
        self.record_button = QPushButton("Record monitor")
        self.record_button.clicked.connect(self.toggle_recording)
        layout.addWidget(self.record_button)
        return bar

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
        connect.clicked.connect(self.connect_source)
        buttons.addWidget(connect)
        open_button = QToolButton()
        open_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        open_button.setToolTip("Choose video file")
        open_button.clicked.connect(self.pick_video_file)
        buttons.addWidget(open_button)
        disconnect_button = QToolButton()
        disconnect_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop))
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
        self.backend_select.currentIndexChanged.connect(self._update_backend_hint)
        layout.addWidget(self.backend_select)
        self.endpoint_input = QLineEdit()
        layout.addWidget(self.endpoint_input)
        self.camera_devices = QComboBox()
        self.camera_devices.setPlaceholderText("Discover a camera")
        layout.addWidget(self.camera_devices)
        controls = QHBoxLayout()
        discover = QPushButton("Discover")
        discover.clicked.connect(self.discover_camera)
        controls.addWidget(discover)
        connect = QPushButton("Connect camera")
        connect.setObjectName("primary")
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
            button.toggled.connect(self._sync_monitor_settings)
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
        lut_row = QHBoxLayout()
        self.lut_label = QLabel("No LUT loaded")
        self.lut_label.setObjectName("muted")
        lut_row.addWidget(self.lut_label, 1)
        lut_button = QPushButton("Load LUT")
        lut_button.clicked.connect(self.pick_lut)
        lut_row.addWidget(lut_button)
        layout.addLayout(lut_row)
        return group

    def _build_monitor_area(self) -> QWidget:
        area = QWidget()
        layout = QVBoxLayout(area)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.video_surface = VideoSurface()
        layout.addWidget(self.video_surface, 1)
        transport = QFrame()
        transport.setStyleSheet("background: #1b1e1a; border: 1px solid #343932; border-radius: 5px;")
        transport_layout = QHBoxLayout(transport)
        transport_layout.setContentsMargins(8, 6, 8, 6)
        transport_layout.addWidget(QLabel("MONITOR OUTPUT"))
        transport_layout.addStretch(1)
        screenshot = QToolButton()
        screenshot.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
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
        self.live_view_button.clicked.connect(self.start_camera_live_view)
        layout.addWidget(self.live_view_button)
        action_row = QHBoxLayout()
        self.focus_button = QPushButton("Focus")
        self.focus_button.pressed.connect(lambda: self.run_camera_action("focus"))
        self.focus_button.released.connect(lambda: self.run_camera_action("release_focus", quiet=True))
        action_row.addWidget(self.focus_button)
        self.photo_button = QPushButton("Take photo")
        self.photo_button.clicked.connect(lambda: self.run_camera_action("photo"))
        action_row.addWidget(self.photo_button)
        layout.addLayout(action_row)
        self.camera_record_button = QPushButton("Start camera record")
        self.camera_record_button.clicked.connect(self.toggle_camera_recording)
        layout.addWidget(self.camera_record_button)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.camera_setting_boxes: dict[str, QComboBox] = {}
        settings = {
            "iso": ["Auto", "100", "200", "400", "800", "1600", "3200", "6400"],
            "shutter": ["1/25", "1/50", "1/60", "1/100", "1/125", "1/250"],
            "aperture": ["1.4", "1.8", "2.8", "4.0", "5.6", "8.0", "11"],
            "white_balance": ["Auto", "Daylight", "Cloudy", "Incandescent", "Fluorescent"],
        }
        for name, values in settings.items():
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            box = QComboBox()
            box.addItems(values)
            row_layout.addWidget(box, 1)
            set_button = QToolButton()
            set_button.setText("Set")
            set_button.setToolTip(f"Apply {name.replace('_', ' ')}")
            set_button.clicked.connect(lambda _checked=False, key=name, combo=box: self.set_camera_setting(key, combo.currentText()))
            row_layout.addWidget(set_button)
            form.addRow(name.replace("_", " ").title(), row)
            self.camera_setting_boxes[name] = box
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
    def _scope_view() -> QLabel:
        view = QLabel()
        view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        view.setMinimumHeight(118)
        view.setStyleSheet("background: #0b0d0a; border: 1px solid #343932;")
        return view

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
            "Connect over USB in PC Remote mode. Requires gphoto2.",
            "Enter a local Camera Remote SDK REST server URL.",
        ]
        hint = hints[self.backend_select.currentIndex()]
        self.endpoint_input.setPlaceholderText(hint)
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

    def discover_camera(self) -> None:
        try:
            backend, devices = self._discover_selected_backend()
        except CameraError as exc:
            self._notify(str(exc), error=True)
            return
        self.active_backend = backend
        self.discovered_devices = devices
        self.camera_devices.clear()
        for device in devices:
            self.camera_devices.addItem(device.name, device)
        if devices:
            self.camera_connection_status.setText(f"Found {len(devices)} camera(s). Select one and connect.")
            self._notify(f"Found {len(devices)} camera(s).")
        else:
            self.camera_connection_status.setText("No camera found. Check the connection or enter its address.")
            self._notify("No camera was discovered.", error=True)

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
                    self.discovered_devices = devices
                    self.camera_devices.clear()
                    for found in devices:
                        self.camera_devices.addItem(found.name, found)
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
        self.active_backend = backend
        self.active_camera_label.setText(f"{device.name}\n{backend.name}")
        self.camera_connection_status.setText(f"Connected: {device.name}")
        self._set_camera_controls_enabled(True)
        self._load_available_settings()
        self._notify(f"Connected to {device.name}.")

    def _load_available_settings(self) -> None:
        if not isinstance(self.active_backend, (SonyRemoteApiBackend, GPhotoBackend)):
            return
        for name, combo in self.camera_setting_boxes.items():
            values = self.active_backend.available_values(name)
            if not values:
                if isinstance(self.active_backend, GPhotoBackend):
                    self._set_camera_setting_enabled(combo, False)
                continue
            current = combo.currentText()
            combo.clear()
            combo.addItems(dict.fromkeys(values))
            if current in values:
                combo.setCurrentText(current)
            if isinstance(self.active_backend, GPhotoBackend):
                self._set_camera_setting_enabled(combo, self.active_backend.property_writable(name))

    @staticmethod
    def _set_camera_setting_enabled(combo: QComboBox, enabled: bool) -> None:
        combo.setEnabled(enabled)
        parent = combo.parentWidget()
        if parent is not None:
            for button in parent.findChildren(QToolButton):
                button.setEnabled(enabled)

    def _set_camera_controls_enabled(self, enabled: bool) -> None:
        self.live_view_button.setEnabled(enabled)
        self.focus_button.setEnabled(enabled)
        self.photo_button.setEnabled(enabled)
        self.camera_record_button.setEnabled(enabled)
        for combo in self.camera_setting_boxes.values():
            combo.setEnabled(enabled)
            parent = combo.parentWidget()
            if parent is not None:
                for button in parent.findChildren(QToolButton):
                    button.setEnabled(enabled)

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
        starting = self.camera_record_button.text().startswith("Start")
        if not self.run_camera_action("record_start" if starting else "record_stop"):
            return
        self.camera_record_button.setText("Stop camera record" if starting else "Start camera record")
        self.camera_record_button.setObjectName("recording" if starting else "")
        self.camera_record_button.style().unpolish(self.camera_record_button)
        self.camera_record_button.style().polish(self.camera_record_button)

    def set_camera_setting(self, name: str, value: str) -> None:
        if self.active_backend is None:
            return
        try:
            self._notify(self.active_backend.set_property(name, value))
        except CameraError as exc:
            self._notify(str(exc), error=True)

    def start_camera_live_view(self) -> None:
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
            self._notify("Camera live view started.")

    def pick_lut(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Load LUT", str(Path.home()), "Cube LUT (*.cube)")
        if not filename:
            return
        lut = load_cube_lut(filename)
        if lut is None:
            self._notify("This LUT file could not be read.", error=True)
            return
        self.current_lut = lut
        self.settings.lut_path = filename
        self.lut_label.setText(Path(filename).name)
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
        self.frame_count += 1
        self.timecode_label.setText(dt.datetime.now().strftime("%H:%M:%S"))
        if self.frame_count % 3 == 0:
            self._set_image(self.histogram_view, make_histogram(frame))
            self._set_image(self.waveform_view, make_waveform(frame))
            self._set_image(self.vectorscope_view, make_vectorscope(frame))

    def _show_idle_frame(self) -> None:
        idle = np.zeros((720, 1280, 3), dtype=np.uint8)
        idle[:] = (7, 8, 6)
        cv2.rectangle(idle, (80, 80), (1200, 640), (45, 50, 43), 1)
        cv2.line(idle, (640, 270), (640, 450), (48, 55, 47), 1)
        cv2.line(idle, (550, 360), (730, 360), (48, 55, 47), 1)
        cv2.putText(idle, "NO VIDEO SIGNAL", (504, 520), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (154, 164, 154), 2, cv2.LINE_AA)
        self.video_surface.present(idle)

    @staticmethod
    def _set_image(label: QLabel, frame: np.ndarray) -> None:
        if frame is None or label.width() < 2 or label.height() < 2:
            return
        display = fit_frame_to_box(frame, label.width(), label.height())
        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        label.setPixmap(QPixmap.fromImage(image))

    def _notify(self, message: str, error: bool = False) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #f08b85; background: #1b1e1a; border-top: 1px solid #343932;" if error else "")

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
