from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


BUILTIN_LOOK_NAMES = ("Neutral", "Warm Film", "Soft Matte", "Teal and Amber", "Monochrome")


@dataclass
class MonitorSettings:
    zebra: bool = True
    zebra_level: int = 95
    false_color: bool = False
    peaking: bool = True
    peaking_strength: int = 55
    histogram: bool = True
    waveform: bool = True
    vectorscope: bool = True
    guide: bool = True
    flip: bool = False
    desqueeze: float = 1.0
    lut_path: str = ""
    lut_amount: float = 1.0


def parse_source(value: str) -> int | str:
    value = value.strip()
    if value.isdigit():
        return int(value)
    return value


def fit_frame_to_box(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    if width <= 2 or height <= 2:
        return frame
    h, w = frame.shape[:2]
    scale = min(width / w, height / h)
    resized = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    y = (height - resized.shape[0]) // 2
    x = (width - resized.shape[1]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    return canvas


def load_cube_lut(path: str) -> np.ndarray | None:
    if not path:
        return None
    lut_file = Path(path)
    if not lut_file.exists():
        return None

    size = None
    values: list[list[float]] = []
    for raw in lut_file.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        keyword = parts[0].upper()
        if keyword == "LUT_3D_SIZE" and len(parts) >= 2:
            size = int(parts[1])
            continue
        if keyword in {"TITLE", "DOMAIN_MIN", "DOMAIN_MAX"}:
            continue
        if len(parts) >= 3:
            try:
                values.append([float(parts[0]), float(parts[1]), float(parts[2])])
            except ValueError:
                pass

    if not size or len(values) < size**3:
        return None
    return np.array(values[: size**3], dtype=np.float32).reshape((size, size, size, 3))


def apply_cube_lut(frame: np.ndarray, lut: np.ndarray, amount: float) -> np.ndarray:
    if lut is None or amount <= 0:
        return frame
    size = lut.shape[0]
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    coords = np.clip((rgb * (size - 1)).round().astype(np.int32), 0, size - 1)
    graded = lut[coords[:, :, 0], coords[:, :, 1], coords[:, :, 2]]
    graded_bgr = cv2.cvtColor(np.clip(graded * 255, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
    amount = float(np.clip(amount, 0, 1))
    return cv2.addWeighted(frame, 1 - amount, graded_bgr, amount, 0)


@lru_cache(maxsize=None)
def built_in_lut(name: str, size: int = 17) -> np.ndarray | None:
    """Return an original monitoring LUT for a named preview look."""
    if name == "Neutral":
        return None
    if name not in BUILTIN_LOOK_NAMES:
        raise ValueError(f"Unknown built-in look: {name}")

    values = np.linspace(0, 1, size, dtype=np.float32)
    red, green, blue = np.meshgrid(values, values, values, indexing="ij")
    rgb = np.stack((red, green, blue), axis=-1)

    if name == "Warm Film":
        rgb = np.clip((rgb - 0.5) * 1.05 + 0.5, 0, 1)
        rgb = np.clip(rgb * np.array((1.06, 1.0, 0.9), dtype=np.float32), 0, 1)
        rgb = np.power(rgb, np.array((0.98, 1.0, 1.04), dtype=np.float32))
    elif name == "Soft Matte":
        rgb = np.clip(0.055 + rgb * 0.89, 0, 1)
        luma = np.sum(rgb * np.array((0.2126, 0.7152, 0.0722), dtype=np.float32), axis=-1, keepdims=True)
        rgb = np.clip(luma + (rgb - luma) * 0.84, 0, 1)
    elif name == "Teal and Amber":
        luma = np.sum(rgb * np.array((0.2126, 0.7152, 0.0722), dtype=np.float32), axis=-1, keepdims=True)
        shadows = np.clip((0.48 - luma) * 1.7, 0, 1)
        highlights = np.clip((luma - 0.52) * 1.7, 0, 1)
        rgb = rgb + shadows * np.array((-0.04, 0.035, 0.065), dtype=np.float32)
        rgb = rgb + highlights * np.array((0.07, 0.025, -0.045), dtype=np.float32)
        rgb = np.clip((rgb - 0.5) * 1.06 + 0.5, 0, 1)
    elif name == "Monochrome":
        luma = np.sum(rgb * np.array((0.2126, 0.7152, 0.0722), dtype=np.float32), axis=-1, keepdims=True)
        rgb = np.repeat(np.power(luma, 0.94), 3, axis=-1)

    return np.clip(rgb, 0, 1).astype(np.float32)


def apply_false_color(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.applyColorMap(gray, cv2.COLORMAP_TURBO)


def apply_zebra(frame: np.ndarray, level: int) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    threshold = int(np.clip(level, 1, 100) * 2.55)
    mask = gray >= threshold
    yy, xx = np.indices(gray.shape)
    stripes = ((xx + yy) // 10) % 2 == 0
    out = frame.copy()
    out[mask & stripes] = (255, 255, 255)
    out[mask & ~stripes] = (0, 0, 0)
    return cv2.addWeighted(frame, 0.72, out, 0.28, 0)


def apply_peaking(frame: np.ndarray, strength: int) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Laplacian(gray, cv2.CV_16S, ksize=3)
    mask = np.abs(edges) > max(8, strength)
    out = frame.copy()
    out[mask] = (0, 255, 255)
    return out


def draw_guides(frame: np.ndarray) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]
    color = (210, 210, 210)
    for x in (w // 3, 2 * w // 3):
        cv2.line(out, (x, 0), (x, h), color, 1, cv2.LINE_AA)
    for y in (h // 3, 2 * h // 3):
        cv2.line(out, (0, y), (w, y), color, 1, cv2.LINE_AA)
    safe_w, safe_h = int(w * 0.9), int(h * 0.9)
    x0, y0 = (w - safe_w) // 2, (h - safe_h) // 2
    cv2.rectangle(out, (x0, y0), (x0 + safe_w, y0 + safe_h), (80, 180, 255), 1)
    return out


def process_frame(frame: np.ndarray, settings: MonitorSettings, lut: np.ndarray | None) -> np.ndarray:
    out = frame.copy()
    if settings.flip:
        out = cv2.flip(out, 1)
    if settings.desqueeze and abs(settings.desqueeze - 1.0) > 0.01:
        h, w = out.shape[:2]
        out = cv2.resize(out, (max(1, int(w * settings.desqueeze)), h), interpolation=cv2.INTER_LINEAR)
    out = apply_cube_lut(out, lut, settings.lut_amount)
    if settings.false_color:
        out = apply_false_color(out)
    if settings.zebra:
        out = apply_zebra(out, settings.zebra_level)
    if settings.peaking:
        out = apply_peaking(out, settings.peaking_strength)
    if settings.guide:
        out = draw_guides(out)
    return out


def make_histogram(frame: np.ndarray, size: tuple[int, int] = (320, 120)) -> np.ndarray:
    width, height = size
    canvas = np.full((height, width, 3), 18, dtype=np.uint8)
    colors = [(255, 80, 80), (80, 255, 120), (80, 150, 255)]
    for channel, color in enumerate(colors):
        hist = cv2.calcHist([frame], [channel], None, [256], [0, 256]).flatten()
        hist = hist / (hist.max() or 1)
        points = []
        for x in range(width):
            idx = min(255, int(x * 256 / width))
            y = height - int(hist[idx] * (height - 8)) - 4
            points.append((x, y))
        cv2.polylines(canvas, [np.array(points, dtype=np.int32)], False, color, 1, cv2.LINE_AA)
    return canvas


def make_waveform(frame: np.ndarray, size: tuple[int, int] = (320, 160)) -> np.ndarray:
    width, height = size
    small = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    canvas = np.full((height, width, 3), 12, dtype=np.uint8)
    for x in range(width):
        column = gray[:, x]
        ys = height - 1 - (column.astype(np.float32) / 255 * (height - 1)).astype(np.int32)
        canvas[ys, x] = (80, 220, 255)
    return cv2.GaussianBlur(canvas, (3, 3), 0)


def make_vectorscope(frame: np.ndarray, size: tuple[int, int] = (320, 160)) -> np.ndarray:
    width, height = size
    hsv = cv2.cvtColor(cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].flatten().astype(np.float32) / 180 * 2 * np.pi
    sat = hsv[:, :, 1].flatten().astype(np.float32) / 255
    radius = sat * (min(width, height) * 0.45)
    cx, cy = width // 2, height // 2
    xs = np.clip((cx + np.cos(hue) * radius).astype(np.int32), 0, width - 1)
    ys = np.clip((cy + np.sin(hue) * radius).astype(np.int32), 0, height - 1)
    canvas = np.full((height, width, 3), 14, dtype=np.uint8)
    cv2.circle(canvas, (cx, cy), int(min(width, height) * 0.45), (80, 80, 80), 1)
    canvas[ys, xs] = (240, 240, 240)
    return cv2.GaussianBlur(canvas, (3, 3), 0)
