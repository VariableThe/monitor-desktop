from __future__ import annotations

import unittest

import numpy as np

from monitor_desktop.virtual_camera import VirtualCameraError, VirtualCameraOutput


class FakeCamera:
    device = "Test Virtual Camera"

    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []
        self.closed = False

    def send(self, frame: np.ndarray) -> None:
        self.frames.append(frame.copy())

    def close(self) -> None:
        self.closed = True


class VirtualCameraOutputTests(unittest.TestCase):
    def test_starts_with_bgr_frame_and_resizes_later_source_frames(self) -> None:
        created: dict[str, object] = {}
        camera = FakeCamera()

        def factory(**kwargs: object) -> FakeCamera:
            created.update(kwargs)
            return camera

        output = VirtualCameraOutput(factory, fps=24)
        first = np.zeros((180, 320, 3), dtype=np.uint8)
        device = output.start(first, "Test Virtual Camera")
        output.send(np.zeros((360, 640, 3), dtype=np.uint8))

        self.assertEqual(device, "Test Virtual Camera")
        self.assertEqual(created, {"width": 320, "height": 180, "fps": 24, "device": "Test Virtual Camera"})
        self.assertTrue(output.active)
        self.assertEqual(output.size, (320, 180))
        self.assertEqual([frame.shape for frame in camera.frames], [(180, 320, 3), (180, 320, 3)])

        output.stop()

        self.assertFalse(output.active)
        self.assertTrue(camera.closed)

    def test_rejects_invalid_video_frames_before_opening_a_camera(self) -> None:
        output = VirtualCameraOutput(lambda **kwargs: FakeCamera())

        with self.assertRaises(VirtualCameraError):
            output.start(np.zeros((20, 20), dtype=np.uint8))

        self.assertFalse(output.active)


if __name__ == "__main__":
    unittest.main()
