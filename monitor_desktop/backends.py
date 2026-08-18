"""Camera transports used by Monitor Desktop.

The application intentionally keeps camera protocols behind small adapters. This
allows the monitor to work immediately with a capture card while making Sony
control available through the transport a particular camera actually supports.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as element_tree
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

try:
    import gphoto2 as gp
except ImportError:  # pragma: no cover - exercised on systems without the optional binding
    gp = None


class CameraError(RuntimeError):
    """A camera operation could not be completed."""


@dataclass(frozen=True)
class CameraDevice:
    identifier: str
    name: str
    transport: str
    endpoint: str = ""


class GPhotoLiveCapture:
    """Read preview frames without releasing the libgphoto2 camera session."""

    def __init__(self, backend: "GPhotoBackend") -> None:
        self._backend = backend
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._error: CameraError | None = None
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._read_frames, daemon=True)
        self._thread.start()

    def _read_frames(self) -> None:
        while not self._closed.is_set():
            try:
                frame = self._backend._capture_preview()
            except CameraError as exc:
                self._error = exc
                return
            with self._lock:
                self._frame = frame

    def read(self) -> tuple[bool, np.ndarray | None]:
        with self._lock:
            return (self._frame is not None, None if self._frame is None else self._frame.copy())

    def isOpened(self) -> bool:  # noqa: N802 - match OpenCV's API
        return not self._closed.is_set() and self._error is None

    def release(self) -> None:
        self._closed.set()
        self._thread.join(timeout=1)


class GPhotoBackend:
    """USB camera control backed by the existing libgphoto2 project."""

    name = "gphoto2 USB"
    _setting_widgets = {
        "iso": "iso",
        "shutter": "shutterspeed",
        "aperture": "f-number",
        "white_balance": "whitebalance",
        "focus_mode": "focusmode",
    }
    _value_aliases = {
        "iso": {"Auto": "Auto ISO"},
        "white_balance": {
            "Auto": "Automatic",
            "Incandescent": "Tungsten",
            "Fluorescent": "Fluorescent: Daylight",
        },
    }

    def __init__(self) -> None:
        self.port = ""
        self.camera: Any | None = None
        self._lock = threading.RLock()

    @staticmethod
    def installed() -> bool:
        return gp is not None

    def discover(self) -> list[CameraDevice]:
        if not self.installed():
            raise CameraError("The python-gphoto2 binding is not installed. Run the bootstrap script again.")
        try:
            detected = gp.Camera.autodetect()
        except Exception as exc:
            raise CameraError(f"Could not detect a USB camera: {exc}") from exc
        devices: list[CameraDevice] = []
        for index in range(detected.count()):
            name = detected.get_name(index)
            port = detected.get_value(index)
            devices.append(CameraDevice(port, name, self.name, port))
        return devices

    def connect(self, device: CameraDevice) -> None:
        if gp is None:
            raise CameraError("The python-gphoto2 binding is not installed.")
        self.disconnect()
        port = device.endpoint or device.identifier
        try:
            ports = gp.PortInfoList()
            ports.load()
            abilities = gp.CameraAbilitiesList()
            abilities.load()
            camera = gp.Camera()
            camera.set_port_info(ports[ports.lookup_path(port)])
            camera.set_abilities(abilities[abilities.lookup_model(device.name)])
            camera.init()
        except Exception as exc:
            raise CameraError(f"Could not connect to {device.name}: {exc}") from exc
        self.port = port
        self.camera = camera

    def disconnect(self) -> None:
        if self.camera is None:
            return
        try:
            with self._lock:
                self.camera.exit()
        except Exception:
            pass
        finally:
            self.camera = None
            self.port = ""

    def start_live_view(self) -> GPhotoLiveCapture:
        self._require_connection()
        return GPhotoLiveCapture(self)

    def action(self, action: str, recordings_dir: Path | None = None) -> str:
        self._require_connection()
        if action.startswith("zoom_"):
            self._drive_zoom(action)
            return f"{action.replace('_', ' ').title()} requested."
        if action == "focus":
            self._set_widget_value("autofocus", 1)
            return "Autofocus requested."
        if action == "release_focus":
            # Sony's gphoto2 autofocus is a one-shot action rather than a held shutter state.
            return "Focus action released."
        if action == "photo":
            self._set_widget_value("capture", 1)
            return "Still capture requested on camera."
        if action == "record_start":
            self._set_widget_value("movie", 1)
            return "Movie recording requested."
        if action == "record_stop":
            self._set_widget_value("movie", 0)
            return "Movie stop requested."
        raise CameraError(f"gphoto2 does not support the action '{action}'.")

    def set_property(self, name: str, value: str) -> str:
        self._require_connection()
        widget_name = self._setting_widgets.get(name)
        if not widget_name:
            raise CameraError(f"Unsupported gphoto2 setting: {name}")
        value = self._value_aliases.get(name, {}).get(value, value)
        self._set_widget_value(widget_name, value)
        return f"Set {name} to {value}."

    def available_values(self, name: str) -> list[str]:
        """Read the choices advertised by the connected camera for a setting."""
        widget_name = self._setting_widgets.get(name)
        if not widget_name:
            return []
        try:
            with self._lock:
                widget = self._get_widget(widget_name)
                return [widget.get_choice(index) for index in range(widget.count_choices())]
        except CameraError:
            return []

    def property_writable(self, name: str) -> bool:
        widget_name = self._setting_widgets.get(name)
        if not widget_name:
            return False
        try:
            with self._lock:
                return not bool(self._get_widget(widget_name).get_readonly())
        except CameraError:
            return False

    def current_value(self, name: str) -> str | None:
        """Read the current value reported by the connected camera."""
        widget_name = self._setting_widgets.get(name)
        if not widget_name:
            return None
        try:
            with self._lock:
                value = self._get_widget(widget_name).get_value()
        except CameraError:
            return None
        return str(value)

    def supports_action(self, action: str) -> bool:
        if not action.startswith("zoom_"):
            return True
        try:
            with self._lock:
                widget = self._get_widget("zoom")
                return not bool(widget.get_readonly())
        except CameraError:
            return False

    def _capture_preview(self) -> np.ndarray:
        with self._lock:
            try:
                preview = self._camera().capture_preview()
                encoded = np.frombuffer(preview.get_data_and_size(), dtype=np.uint8)
            except Exception as exc:
                raise CameraError(f"Could not read camera live view: {exc}") from exc
        frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if frame is None:
            raise CameraError("The camera returned an invalid live-view JPEG.")
        return frame

    def _set_widget_value(self, name: str, value: int | str) -> None:
        with self._lock:
            try:
                config = self._camera().get_config()
                widget = config.get_child_by_name(name)
                if widget is None:
                    raise CameraError(f"Camera does not expose the '{name}' control.")
                widget.set_value(value)
                self._camera().set_config(config)
            except CameraError:
                raise
            except Exception as exc:
                raise CameraError(f"Could not change camera setting: {exc}") from exc

    def _drive_zoom(self, action: str) -> None:
        if action == "zoom_stop":
            return
        with self._lock:
            try:
                config = self._camera().get_config()
                widget = config.get_child_by_name("zoom")
                if widget is None:
                    raise CameraError("This gphoto2 camera does not expose a zoom control.")
                if bool(widget.get_readonly()):
                    raise CameraError("This gphoto2 camera reports zoom as read-only.")
                bottom, top, step = self._numeric_range(widget.get_range())
                current = self._numeric_value(widget.get_value())
                widget.set_value(self._zoom_target(action, current, bottom, top, step))
                self._camera().set_config(config)
            except CameraError:
                raise
            except Exception as exc:
                raise CameraError(f"Could not change camera zoom: {exc}") from exc

    @staticmethod
    def _numeric_value(value: Any) -> float:
        try:
            return float(str(value).replace(",", "."))
        except ValueError as exc:
            raise CameraError(f"Camera returned a non-numeric zoom value: {value}") from exc

    @classmethod
    def _numeric_range(cls, raw_range: Any) -> tuple[float, float, float]:
        try:
            bottom, top, step = raw_range[:3]
        except (TypeError, ValueError) as exc:
            raise CameraError("Camera did not report a usable zoom range.") from exc
        return cls._numeric_value(bottom), cls._numeric_value(top), max(cls._numeric_value(step), 1.0)

    @staticmethod
    def _zoom_target(action: str, current: float, bottom: float, top: float, step: float) -> float:
        travel = max(step, (top - bottom) * 0.035)
        direction = 1 if action == "zoom_in" else -1
        return float(np.clip(current + travel * direction, bottom, top))

    def _get_widget(self, name: str) -> Any:
        try:
            widget = self._camera().get_config().get_child_by_name(name)
        except Exception as exc:
            raise CameraError(f"Could not read camera setting: {exc}") from exc
        if widget is None:
            raise CameraError(f"Camera does not expose the '{name}' setting.")
        return widget

    def _camera(self) -> Any:
        if self.camera is None:
            raise CameraError("Choose a gphoto2 camera first.")
        return self.camera

    def _require_connection(self) -> None:
        self._camera()


class SonyRemoteApiBackend:
    """Sony's legacy Wi-Fi Camera Remote API over JSON-RPC.

    This is useful for cameras which expose the Sony Wi-Fi remote application.
    It is deliberately separate from the newer Camera Remote SDK transport.
    """

    name = "Sony Wi-Fi Remote API"
    _service_path = "/sony/camera"
    _setting_methods = {
        "iso": "setIsoSpeedRate",
        "shutter": "setShutterSpeed",
        "aperture": "setFNumber",
        "exposure": "setExposureCompensation",
        "white_balance": "setWhiteBalance",
        "focus_mode": "setFocusMode",
    }
    _actions = {
        "focus": "actHalfPressShutter",
        "release_focus": "actCancelHalfPressShutter",
        "photo": "actTakePicture",
        "record_start": "startMovieRec",
        "record_stop": "stopMovieRec",
    }
    _zoom_actions = {"zoom_in": "in", "zoom_out": "out"}

    def __init__(self, endpoint: str = "") -> None:
        self.endpoint = self._normalise_endpoint(endpoint)
        self._request_id = 1
        self.available_api_names: set[str] = set()

    @staticmethod
    def _normalise_endpoint(endpoint: str) -> str:
        endpoint = endpoint.strip().rstrip("/")
        if not endpoint:
            return ""
        if endpoint.endswith("/sony/camera"):
            return endpoint
        if endpoint.endswith("/sony"):
            return f"{endpoint}/camera"
        if "://" not in endpoint:
            endpoint = f"http://{endpoint}"
        return f"{endpoint}/sony/camera"

    @classmethod
    def discover(cls, timeout: float = 1.5) -> list[CameraDevice]:
        message = "\r\n".join(
            [
                "M-SEARCH * HTTP/1.1",
                "HOST: 239.255.255.250:1900",
                'MAN: "ssdp:discover"',
                "MX: 1",
                "ST: urn:schemas-sony-com:service:ScalarWebAPI:1",
                "",
                "",
            ]
        ).encode("ascii")
        responses: dict[str, CameraDevice] = {}
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.settimeout(timeout)
            try:
                sock.sendto(message, ("239.255.255.250", 1900))
            except OSError as exc:
                raise CameraError(f"Could not start Sony Wi-Fi discovery: {exc}") from exc
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    packet, address = sock.recvfrom(8192)
                except socket.timeout:
                    break
                headers = cls._parse_ssdp_headers(packet.decode("utf-8", "ignore"))
                location = headers.get("location", "")
                endpoint, name = cls._endpoint_from_description(location, address[0])
                if endpoint:
                    responses[endpoint] = CameraDevice(endpoint, name, cls.name, endpoint)
        return list(responses.values())

    @staticmethod
    def _parse_ssdp_headers(raw: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        for line in raw.splitlines()[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        return headers

    @classmethod
    def _endpoint_from_description(cls, location: str, fallback_host: str) -> tuple[str, str]:
        endpoint = f"http://{fallback_host}:10000{cls._service_path}"
        name = f"Sony camera at {fallback_host}"
        if not location:
            return endpoint, name
        try:
            with urllib.request.urlopen(location, timeout=2) as response:
                document = response.read()
            root = element_tree.fromstring(document)
            friendly = root.findtext(".//{*}friendlyName")
            if friendly:
                name = friendly
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1] == "X_ScalarWebAPI_ActionList_URL" and node.text:
                    endpoint = cls._normalise_endpoint(node.text)
                    break
        except (OSError, urllib.error.URLError, element_tree.ParseError):
            pass
        return endpoint, name

    def connect(self, endpoint: str | None = None) -> dict[str, Any]:
        if endpoint:
            self.endpoint = self._normalise_endpoint(endpoint)
        if not self.endpoint:
            raise CameraError("Enter a Sony camera address or use Discover on the camera Wi-Fi network.")
        # Most older cameras make their command list visible only in remote mode.
        try:
            self._call("startRecMode", [], version="1.0")
        except CameraError:
            pass
        available = self._call("getAvailableApiList", [])
        self.available_api_names = {str(value) for value in self._flatten(self._result_values(available))}
        return available

    def start_live_view(self) -> str:
        data = self._call("startLiveview", [])
        urls = self._result_values(data)
        if not urls or not isinstance(urls[0], str):
            raise CameraError("The camera did not return a live-view URL.")
        return urls[0]

    def action(self, action: str) -> str:
        if action in self._zoom_actions:
            if not self.supports_action(action):
                raise CameraError("This Sony Wi-Fi camera does not report remote zoom support.")
            self._call("actZoom", [self._zoom_actions[action], "start"])
            return f"{action.replace('_', ' ').title()} requested."
        if action == "zoom_stop":
            if not self.supports_action(action):
                return "Zoom stop ignored."
            self._call("actZoom", ["in", "stop"])
            return "Zoom stop requested."
        method = self._actions.get(action)
        if not method:
            raise CameraError(f"Unsupported Sony Wi-Fi action: {action}")
        self._call(method, [])
        return f"{action.replace('_', ' ').title()} requested."

    def set_property(self, name: str, value: str) -> str:
        method = self._setting_methods.get(name)
        if not method:
            raise CameraError(f"Unsupported Sony Wi-Fi setting: {name}")
        self._call(method, [value])
        return f"Set {name.replace('_', ' ')} to {value}."

    def available_values(self, name: str) -> list[str]:
        methods = {
            "iso": "getAvailableIsoSpeedRate",
            "shutter": "getAvailableShutterSpeed",
            "aperture": "getAvailableFNumber",
            "white_balance": "getAvailableWhiteBalance",
            "focus_mode": "getAvailableFocusMode",
        }
        if name not in methods:
            return []
        try:
            values = self._result_values(self._call(methods[name], []))
        except CameraError:
            return []
        return [str(value) for value in self._flatten(values) if isinstance(value, (str, int, float))]

    def supports_action(self, action: str) -> bool:
        if action.startswith("zoom_"):
            return "actZoom" in self.available_api_names
        return True

    def _call(self, method: str, params: list[Any], version: str = "1.0") -> dict[str, Any]:
        if not self.endpoint:
            raise CameraError("Connect to a Sony camera first.")
        payload = {"method": method, "params": params, "id": self._request_id, "version": version}
        self._request_id += 1
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")
            raise CameraError(f"Sony Wi-Fi API returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CameraError(f"Could not reach Sony camera: {exc.reason}") from exc
        if data.get("error"):
            error = data["error"]
            raise CameraError(f"Sony Wi-Fi API error: {error}")
        return data

    @staticmethod
    def _result_values(data: dict[str, Any]) -> list[Any]:
        result = data.get("result", [])
        return result if isinstance(result, list) else [result]

    @classmethod
    def _flatten(cls, value: Any) -> list[Any]:
        if isinstance(value, list):
            output: list[Any] = []
            for item in value:
                output.extend(cls._flatten(item))
            return output
        return [value]


class SonySdkServerBackend:
    """Adapter for a local Camera Remote SDK REST server.

    This transport is intentionally protocol-focused: it talks to the API server
    provided by an SDK installation instead of shipping Sony's licensed binaries.
    """

    name = "Sony Camera Remote SDK server"

    def __init__(self, base_url: str = "http://127.0.0.1:8080") -> None:
        self.base_url = base_url.rstrip("/")
        self.camera_id = ""

    def discover(self) -> list[CameraDevice]:
        payload = self._request("GET", "/api/cameras")
        raw_cameras = payload.get("cameras", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw_cameras, list):
            raise CameraError("SDK server returned an unexpected camera list.")
        devices = []
        for camera in raw_cameras:
            if not isinstance(camera, dict):
                continue
            identifier = str(camera.get("id", camera.get("cameraId", "")))
            if identifier:
                devices.append(
                    CameraDevice(
                        identifier,
                        str(camera.get("name", camera.get("model", identifier))),
                        self.name,
                        identifier,
                    )
                )
        return devices

    def connect(self, device: CameraDevice) -> None:
        self.camera_id = device.identifier

    def start_live_view(self) -> str:
        data = self._request("POST", f"/api/cameras/{self.camera_id}/actions/live-view", {})
        url = data.get("url", data.get("liveViewUrl", "")) if isinstance(data, dict) else ""
        if not url:
            raise CameraError("SDK server did not return a live-view URL.")
        return str(url)

    def action(self, action: str) -> str:
        mapping = {
            "focus": "half-press",
            "release_focus": "release-half-press",
            "photo": "shutter",
            "record_start": "movie-record",
            "record_stop": "movie-record",
        }
        if action == "zoom_stop":
            return "Zoom stop ignored."
        if action in {"zoom_in", "zoom_out"}:
            self._request(
                "POST",
                f"/api/cameras/{self.camera_id}/actions/zoom",
                {"direction": "in" if action == "zoom_in" else "out", "speed": "normal"},
            )
            return f"{action.replace('_', ' ').title()} requested."
        name = mapping.get(action)
        if not name:
            raise CameraError(f"Unsupported SDK action: {action}")
        payload = {"action": "start" if action == "record_start" else "stop"} if name == "movie-record" else {}
        self._request("POST", f"/api/cameras/{self.camera_id}/actions/{name}", payload)
        return f"{action.replace('_', ' ').title()} requested."

    def supports_action(self, action: str) -> bool:
        return True

    def set_property(self, name: str, value: str) -> str:
        self._request("PUT", f"/api/cameras/{self.camera_id}/properties/{name}", {"value": value})
        return f"Set {name.replace('_', ' ')} to {value}."

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")
            raise CameraError(f"SDK server returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CameraError(f"Could not reach SDK server: {exc.reason}") from exc
        return json.loads(raw) if raw else {}
