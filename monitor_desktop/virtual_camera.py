"""System virtual-camera output for the monitor feed.

``pyvirtualcam`` deliberately connects to a virtual-camera driver that is
already installed on the computer. Keeping that boundary here makes the UI
work with OBS Virtual Camera on macOS and Windows, and v4l2loopback on Linux,
without making source capture depend on a particular camera transport.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import cv2
import numpy as np


class VirtualCameraError(RuntimeError):
    """The local virtual-camera output could not be started or written to."""


VirtualCameraFactory = Callable[..., Any]


class VirtualCameraOutput:
    """Own one virtual-camera connection and send BGR monitor frames to it."""

    def __init__(self, camera_factory: VirtualCameraFactory | None = None, fps: int = 30) -> None:
        self._camera_factory = camera_factory or self._open_pyvirtualcam_camera
        self._fps = fps
        self._camera: Any | None = None
        self._width = 0
        self._height = 0
        self.device = ""

    @property
    def active(self) -> bool:
        return self._camera is not None

    @property
    def size(self) -> tuple[int, int] | None:
        return (self._width, self._height) if self.active else None

    def start(self, frame: np.ndarray, device: str | None = None) -> str:
        """Open the system output at the first frame's dimensions and send it."""
        if self.active:
            return self.device
        self._validate_frame(frame)
        height, width = frame.shape[:2]
        try:
            camera = self._camera_factory(width=width, height=height, fps=self._fps, device=device or None)
        except VirtualCameraError:
            raise
        except Exception as exc:
            requested = f" '{device}'" if device else ""
            raise VirtualCameraError(f"Could not open the virtual camera{requested}: {exc}") from exc

        self._camera = camera
        self._width = width
        self._height = height
        self.device = str(getattr(camera, "device", None) or device or "Virtual Camera")
        try:
            self.send(frame)
        except VirtualCameraError:
            try:
                self.stop()
            except VirtualCameraError:
                pass
            raise
        return self.device

    def send(self, frame: np.ndarray) -> None:
        """Send one frame, resizing only when a stream changes resolution."""
        if self._camera is None:
            raise VirtualCameraError("Virtual camera is not running.")
        self._validate_frame(frame)
        output = frame
        if output.shape[:2] != (self._height, self._width):
            output = cv2.resize(output, (self._width, self._height), interpolation=cv2.INTER_AREA)
        try:
            self._camera.send(np.ascontiguousarray(output))
        except Exception as exc:
            raise VirtualCameraError(f"Could not send a frame to {self.device}: {exc}") from exc

    def stop(self) -> None:
        """Close the output. This is intentionally safe to call more than once."""
        camera = self._camera
        self._camera = None
        self._width = 0
        self._height = 0
        self.device = ""
        if camera is None:
            return
        try:
            camera.close()
        except Exception as exc:
            raise VirtualCameraError(f"Could not stop the virtual camera: {exc}") from exc

    @staticmethod
    def _validate_frame(frame: np.ndarray) -> None:
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
            raise VirtualCameraError("Virtual camera output requires an 8-bit, three-channel video frame.")
        if frame.shape[0] < 1 or frame.shape[1] < 1:
            raise VirtualCameraError("Virtual camera output requires a non-empty video frame.")

    @staticmethod
    def _open_pyvirtualcam_camera(*, width: int, height: int, fps: int, device: str | None) -> Any:
        try:
            import pyvirtualcam
        except ImportError as exc:
            raise VirtualCameraError("Virtual camera support is not installed. Run the Monitor Desktop installer again.") from exc
        return pyvirtualcam.Camera(
            width=width,
            height=height,
            fps=fps,
            device=device,
            fmt=pyvirtualcam.PixelFormat.BGR,
        )
