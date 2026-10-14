"""Linux v4l2loopback device discovery via sysfs.

``pyvirtualcam`` on Linux only talks to v4l2loopback, and its ``device``
argument must be a ``/dev/video<N>`` **path** (or ``None`` to auto-scan).
A card label such as ``"My Camera"`` is treated as a literal path
and fails to open. The app therefore has to map a card label to a device
node itself before calling :class:`VirtualCameraOutput.start`.

Discovery uses sysfs only (``/sys/class/video4linux/video<N>/name`` holds
the human-readable card name, e.g. ``USB2.0 HD UVC WebCam: USB2.0 HD``), so
it works without ``v4l2-ctl``, ``v4l2loopback-ctl`` or ``ffmpeg`` installed.

Calling contract for the app:

* Linux: pass the result of :func:`resolve_virtual_camera_device` (a
  ``/dev/videoN`` path) as ``device`` to ``VirtualCameraOutput.start``.
  ``None`` means "not provisioned" — the caller should show remediation
  (e.g. run the setup script that creates the loopback device) instead of
  attempting to stream.
* Other platforms (macOS/Windows): do not call this module at all. The
  caller passes the OBS device name/override straight through.
"""

from __future__ import annotations

import re
from pathlib import Path

OUR_LABEL = "Monitor Desktop"
FALLBACK_LABELS = ("Dummy video device", "v4l2 loopback")
SCANNED_NODE_LIMIT = 100  # mirrors pyvirtualcam's /dev/video[0-99] scan

_VIDEO_NODE_RE = re.compile(r"^video(\d+)$")


def _iter_video_nodes(sysfs_root: Path) -> list[tuple[int, str]]:
    """Return ``(node_number, entry_name)`` pairs sorted numerically."""
    try:
        entries = list(sysfs_root.iterdir())
    except OSError:
        return []
    nodes: list[tuple[int, str]] = []
    for entry in entries:
        match = _VIDEO_NODE_RE.match(entry.name)
        if match is None:
            continue
        number = int(match.group(1))
        # pyvirtualcam only scans /dev/video[0-99]; anything higher can never
        # be opened via the default backend, so ignore it rather than
        # returning a path the caller cannot use.
        if number >= SCANNED_NODE_LIMIT:
            continue
        nodes.append((number, entry.name))
    nodes.sort(key=lambda item: item[0])
    return nodes


def _read_card_name(sysfs_root: Path, entry_name: str) -> str | None:
    """Return the stripped card name for a node, or ``None`` if unreadable."""
    try:
        return (sysfs_root / entry_name / "name").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def find_loopback_device(sysfs_root: Path = Path("/sys/class/video4linux")) -> str | None:
    """Find our v4l2loopback node and return its ``/dev/videoN`` path."""
    names: list[tuple[int, str, str]] = []
    for number, entry_name in _iter_video_nodes(sysfs_root):
        card_name = _read_card_name(sysfs_root, entry_name)
        if card_name is None:
            continue
        names.append((number, entry_name, card_name))

    # Pass 1: exact match on our provisioned card label.
    for _, entry_name, card_name in names:
        if card_name == OUR_LABEL:
            return f"/dev/{entry_name}"

    # Pass 2: default v4l2loopback card name for a hand-rolled
    # ``modprobe v4l2loopback`` with no label config
    # (e.g. ``Dummy video device (0x0000)``).
    for _, entry_name, card_name in names:
        if any(card_name.startswith(label) or label in card_name for label in FALLBACK_LABELS):
            return f"/dev/{entry_name}"

    # Allowlist only: never guess a physical UVC/webcam node via substring
    # heuristics like "camera"/"usb".
    return None


def resolve_virtual_camera_device(
    manual: str | None, sysfs_root: Path = Path("/sys/class/video4linux")
) -> str | None:
    """Resolve the device argument for ``VirtualCameraOutput.start``.

    A non-empty ``manual`` string is returned verbatim so the existing
    macOS/Windows OBS-name flow and the Linux advanced override keep working
    untouched. Otherwise discovery runs via :func:`find_loopback_device`.
    """
    if manual is not None and manual.strip():
        return manual
    return find_loopback_device(sysfs_root)
