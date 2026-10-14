#!/usr/bin/env python3
"""Render the bundled viewfindrr icon SVG to a PNG for Qt window and dialog icons.

Runs headless through the ``offscreen`` QPA plugin, so it works over SSH and in
CI. PySide6 is the only dependency; the SVG is the source of truth and the PNG
is a build artifact kept next to it in the package.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = PROJECT_ROOT / "monitor_desktop" / "assets" / "icon.svg"
PNG_PATH = PROJECT_ROOT / "monitor_desktop" / "assets" / "icon.png"
ICON_SIZE = 256


def _ensure_offscreen_platform() -> None:
    """Force the offscreen QPA plugin so no display server is required."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def render_icon() -> Path:
    """Rasterise ``icon.svg`` into ``icon.png`` and return the PNG path."""
    _ensure_offscreen_platform()

    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    if not SVG_PATH.is_file():
        raise SystemExit(f"error: icon SVG not found at {SVG_PATH}")

    renderer = QSvgRenderer(str(SVG_PATH))
    if not renderer.isValid():
        raise SystemExit(f"error: {SVG_PATH} is not a valid SVG document")

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    image = QImage(ICON_SIZE, ICON_SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0.0, 0.0, float(ICON_SIZE), float(ICON_SIZE)))
    finally:
        painter.end()

    if not image.save(str(PNG_PATH), "PNG"):
        raise SystemExit(f"error: could not write {PNG_PATH}")
    return PNG_PATH


def main() -> int:
    output = render_icon()
    print(f"wrote {output} ({ICON_SIZE}x{ICON_SIZE} PNG, transparency preserved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
