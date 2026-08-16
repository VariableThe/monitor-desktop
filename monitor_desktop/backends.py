"""Camera transports used by Monitor Desktop.

The application intentionally keeps camera protocols behind small adapters. This
allows the monitor to work immediately with a capture card while making Sony
control available through the transport a particular camera actually supports.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
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


class CameraError(RuntimeError):
    """A camera operation could not be completed."""


@dataclass(frozen=True)
class CameraDevice:
    identifier: str
    name: str
    transport: str
    endpoint: str = ""


class MJPEGProcessCapture:
    """Expose gphoto2's JPEG live-view stream with VideoCapture-like methods."""

    def __init__(self, command: list[str]) -> None:
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._read_frames, daemon=True)
        self._thread.start()

    def _read_frames(self) -> None:
        assert self._process.stdout is not None
        buffer = bytearray()
        while not self._closed:
            chunk = self._process.stdout.read(8192)
            if not chunk:
                return
            buffer.extend(chunk)
            while True:
                start = buffer.find(b"\xff\xd8")
                end = buffer.find(b"\xff\xd9", start + 2)
                if start < 0 or end < 0:
                    if len(buffer) > 4_000_000:
                        del buffer[:-1024]
                    break
                encoded = np.frombuffer(buffer[start : end + 2], dtype=np.uint8)
                del buffer[: end + 2]
                frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                if frame is not None:
                    with self._lock:
                        self._frame = frame

    def read(self) -> tuple[bool, np.ndarray | None]:
        with self._lock:
            return (self._frame is not None, None if self._frame is None else self._frame.copy())

    def isOpened(self) -> bool:  # noqa: N802 - match OpenCV's API
        return not self._closed and self._process.poll() is None

    def release(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._process.kill()


class GPhotoBackend:
    """USB camera control backed by the existing libgphoto2 project."""

    name = "gphoto2 USB"

    def __init__(self) -> None:
        self.port = ""

    @staticmethod
    def installed() -> bool:
        return shutil.which("gphoto2") is not None

    def discover(self) -> list[CameraDevice]:
        if not self.installed():
            raise CameraError("gphoto2 is not installed. Install libgphoto2, then try again.")
        result = self._run(["--auto-detect"], timeout=12)
        devices: list[CameraDevice] = []
        for line in result.stdout.splitlines():
            if not line.strip() or line.lstrip().startswith(("Model", "-")):
                continue
            match = re.match(r"\s*(.*?)\s{2,}(usb:[^\s]+)", line, re.IGNORECASE)
            if match:
                name, port = match.groups()
                devices.append(CameraDevice(port, name.strip(), self.name, port))
        return devices

    def connect(self, device: CameraDevice) -> None:
        self.port = device.endpoint or device.identifier
        self._run(["--summary"], timeout=12)

    def start_live_view(self) -> MJPEGProcessCapture:
        self._require_connection()
        return MJPEGProcessCapture(self._command(["--capture-movie", "--stdout"]))

    def action(self, action: str, recordings_dir: Path | None = None) -> str:
        self._require_connection()
        if action == "focus":
            self._run(["--set-config", "autofocusdrive=1"])
            return "Autofocus requested."
        if action == "photo":
            if recordings_dir is None:
                raise CameraError("A download folder is required for a still capture.")
            recordings_dir.mkdir(parents=True, exist_ok=True)
            filename = recordings_dir / f"sony_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
            self._run(["--capture-image-and-download", "--filename", str(filename)], timeout=45)
            return f"Saved still: {filename.name}"
        if action == "record_start":
            self._run(["--set-config", "movie=1"])
            return "Movie recording requested."
        if action == "record_stop":
            self._run(["--set-config", "movie=0"])
            return "Movie stop requested."
        raise CameraError(f"gphoto2 does not support the action '{action}'.")

    def set_property(self, name: str, value: str) -> str:
        self._require_connection()
        self._run(["--set-config", f"{name}={value}"])
        return f"Set {name} to {value}."

    def _require_connection(self) -> None:
        if not self.port:
            raise CameraError("Choose a gphoto2 camera first.")

    def _command(self, args: list[str]) -> list[str]:
        return ["gphoto2", "--port", self.port, *args]

    def _run(self, args: list[str], timeout: float = 15) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                self._command(args) if self.port else ["gphoto2", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CameraError("gphoto2 timed out while talking to the camera.") from exc
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "Unknown gphoto2 error"
            raise CameraError(detail)
        return result


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

    def __init__(self, endpoint: str = "") -> None:
        self.endpoint = self._normalise_endpoint(endpoint)
        self._request_id = 1

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
        return self._call("getAvailableApiList", [])

    def start_live_view(self) -> str:
        data = self._call("startLiveview", [])
        urls = self._result_values(data)
        if not urls or not isinstance(urls[0], str):
            raise CameraError("The camera did not return a live-view URL.")
        return urls[0]

    def action(self, action: str) -> str:
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
        name = mapping.get(action)
        if not name:
            raise CameraError(f"Unsupported SDK action: {action}")
        payload = {"action": "start" if action == "record_start" else "stop"} if name == "movie-record" else {}
        self._request("POST", f"/api/cameras/{self.camera_id}/actions/{name}", payload)
        return f"{action.replace('_', ' ').title()} requested."

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
