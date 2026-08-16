from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from monitor_desktop.video_tools import MonitorSettings, load_cube_lut, process_frame


class VideoToolsTests(unittest.TestCase):
    def test_process_frame_applies_desqueeze_and_assists(self) -> None:
        frame = np.zeros((80, 120, 3), dtype=np.uint8)
        frame[:, 60:] = (255, 255, 255)
        settings = MonitorSettings(zebra=True, peaking=True, guide=True, desqueeze=1.5)
        processed = process_frame(frame, settings, None)
        self.assertEqual(processed.shape, (80, 180, 3))
        self.assertEqual(processed.dtype, np.uint8)

    def test_load_cube_lut_reads_valid_file(self) -> None:
        values = "\n".join("0.0 0.0 0.0" for _ in range(8))
        with tempfile.TemporaryDirectory() as directory:
            lut_path = Path(directory) / "test.cube"
            lut_path.write_text(f"LUT_3D_SIZE 2\n{values}\n", encoding="utf-8")
            lut = load_cube_lut(str(lut_path))
        self.assertIsNotNone(lut)
        assert lut is not None
        self.assertEqual(lut.shape, (2, 2, 2, 3))


if __name__ == "__main__":
    unittest.main()
