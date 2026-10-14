"""Guard against naming drift between the app constant and the setup script."""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QIcon

from monitor_desktop.app import window_icon
from monitor_desktop.loopback_device import OUR_LABEL

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup-v4l2loopback.sh"
ICON_ASSET = PROJECT_ROOT / "monitor_desktop" / "assets" / "icon.png"
BRAND = "viewfindrr"


def setup_script_default_card_label() -> str:
    """Read the ``CARD_LABEL`` default out of the setup script's source text."""
    text = SETUP_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r'^card_label="\$\{CARD_LABEL:-([^"]*)\}"$', text, re.MULTILINE)
    assert match is not None, f"no CARD_LABEL default found in {SETUP_SCRIPT}"
    return match.group(1)


class NamingDriftTests(unittest.TestCase):
    def test_our_label_matches_the_setup_script_default(self) -> None:
        # The app resolves the loopback node by card label, so the label it
        # matches on must be the one the setup script provisions. File-only:
        # no sudo, no hardware, no sysfs access.
        self.assertEqual(OUR_LABEL, setup_script_default_card_label())
        self.assertEqual(OUR_LABEL, BRAND)

    def test_window_icon_asset_exists(self) -> None:
        self.assertTrue(ICON_ASSET.is_file(), f"missing icon asset: {ICON_ASSET}")

    def test_window_icon_loads_as_a_non_null_icon(self) -> None:
        icon = window_icon()
        self.assertIsInstance(icon, QIcon)
        self.assertFalse(icon.isNull())
        self.assertTrue(icon.availableSizes())


if __name__ == "__main__":
    unittest.main()
