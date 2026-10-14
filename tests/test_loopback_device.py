from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from monitor_desktop.loopback_device import (
    OUR_LABEL,
    find_loopback_device,
    resolve_virtual_camera_device,
)


def _make_tree(tmp: str, cards: dict[str, str]) -> Path:
    """Build a fake sysfs tree: ``{videoN: card name}`` -> root path."""
    root = Path(tmp) / "video4linux"
    root.mkdir(parents=True, exist_ok=True)
    for node, card_name in cards.items():
        node_dir = root / node
        node_dir.mkdir(parents=True, exist_ok=True)
        (node_dir / "name").write_text(card_name, encoding="utf-8")
    return root


class FindLoopbackDeviceTests(unittest.TestCase):
    def test_our_label_present_returns_its_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_tree(tmp, {"video0": "USB Camera", "video10": OUR_LABEL})
            self.assertEqual(find_loopback_device(root), "/dev/video10")

    def test_dummy_device_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_tree(tmp, {"video1": "Dummy video device (0x0000)"})
            self.assertEqual(find_loopback_device(root), "/dev/video1")

    def test_our_label_wins_over_dummy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_tree(
                tmp,
                {"video0": "Dummy video device (0x0000)", "video5": OUR_LABEL},
            )
            self.assertEqual(find_loopback_device(root), "/dev/video5")

    def test_only_physical_webcams_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_tree(tmp, {"video0": "Integrated Camera", "video1": "UVC Camera"})
            self.assertIsNone(find_loopback_device(root))

    def test_numeric_sort_video2_before_video10(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_tree(tmp, {"video10": OUR_LABEL, "video2": OUR_LABEL})
            self.assertEqual(find_loopback_device(root), "/dev/video2")

    def test_missing_name_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "video4linux"
            root.mkdir(parents=True, exist_ok=True)
            (root / "video0").mkdir(parents=True, exist_ok=True)  # no name file
            (root / "video1").mkdir(parents=True, exist_ok=True)
            (root / "video1" / "name").write_text(OUR_LABEL, encoding="utf-8")
            self.assertEqual(find_loopback_device(root), "/dev/video1")

    def test_node_number_at_or_above_100_is_ignored(self) -> None:
        # pyvirtualcam only scans /dev/video[0-99], so video100+ can never be
        # opened through the default backend and must not be returned.
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_tree(tmp, {"video100": OUR_LABEL, "video150": "Dummy video device (0x0000)"})
            self.assertIsNone(find_loopback_device(root))

    def test_empty_sysfs_root_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "video4linux"
            root.mkdir(parents=True, exist_ok=True)
            self.assertIsNone(find_loopback_device(root))


class ResolveVirtualCameraDeviceTests(unittest.TestCase):
    def test_manual_override_returned_verbatim_without_touching_sysfs(self) -> None:
        nonexistent = Path("/nonexistent-sysfs-root-for-test")
        self.assertEqual(resolve_virtual_camera_device("Some Override", nonexistent), "Some Override")

    def test_empty_string_falls_through_to_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_tree(tmp, {"video3": OUR_LABEL})
            self.assertEqual(resolve_virtual_camera_device("", root), "/dev/video3")

    def test_none_falls_through_to_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_tree(tmp, {"video3": OUR_LABEL})
            self.assertEqual(resolve_virtual_camera_device(None, root), "/dev/video3")

    def test_none_with_empty_tree_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "video4linux"
            root.mkdir(parents=True, exist_ok=True)
            self.assertIsNone(resolve_virtual_camera_device(None, root))


if __name__ == "__main__":
    unittest.main()
