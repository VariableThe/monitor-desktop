"""Guard the XDG desktop entry and its installer integration."""

from __future__ import annotations

import unittest
from pathlib import Path

from monitor_desktop.loopback_device import OUR_LABEL

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_TEMPLATE = PROJECT_ROOT / "packaging" / "viewfindrr.desktop"
INSTALL_SCRIPT = PROJECT_ROOT / "scripts" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "scripts" / "update.sh"
ICON_PNG = PROJECT_ROOT / "monitor_desktop" / "assets" / "icon.png"
ICON_SVG = PROJECT_ROOT / "monitor_desktop" / "assets" / "icon.svg"
BRAND = "viewfindrr"


def parse_desktop_entry(path: Path) -> dict[str, str]:
    """Parse ``Key=Value`` pairs, tolerant of surrounding whitespace."""
    entries: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        entries[key.strip()] = value.strip()
    return entries


class DesktopEntryTests(unittest.TestCase):
    def test_template_has_required_keys(self) -> None:
        self.assertTrue(DESKTOP_TEMPLATE.is_file(), f"missing desktop template: {DESKTOP_TEMPLATE}")
        text = DESKTOP_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("[Desktop Entry]", text)
        entries = parse_desktop_entry(DESKTOP_TEMPLATE)
        self.assertEqual(entries.get("Type"), "Application")
        self.assertEqual(entries.get("Name"), BRAND)
        self.assertEqual(entries.get("Icon"), BRAND)
        self.assertEqual(entries.get("Exec"), "@EXEC@")
        self.assertEqual(entries.get("Terminal"), "false")
        self.assertIn("StartupWMClass", entries)
        self.assertIn("Categories", entries)

    def test_brand_consistency(self) -> None:
        entries = parse_desktop_entry(DESKTOP_TEMPLATE)
        self.assertEqual(entries.get("Name"), BRAND)
        self.assertEqual(entries.get("Icon"), BRAND)
        self.assertEqual(entries.get("StartupWMClass"), BRAND)
        self.assertEqual(OUR_LABEL, BRAND)

    def test_installers_reference_template_and_applications_dir(self) -> None:
        for script in (INSTALL_SCRIPT, UPDATE_SCRIPT):
            text = script.read_text(encoding="utf-8")
            self.assertIn("packaging/viewfindrr.desktop", text, f"{script.name} never references the template")
            self.assertIn("applications", text, f"{script.name} never installs into an applications directory")

    def test_icon_assets_exist(self) -> None:
        self.assertTrue(ICON_PNG.is_file(), f"missing icon asset: {ICON_PNG}")
        self.assertTrue(ICON_SVG.is_file(), f"missing icon asset: {ICON_SVG}")


if __name__ == "__main__":
    unittest.main()
