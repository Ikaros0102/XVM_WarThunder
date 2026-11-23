"""Passive screen capture via mss and 16:9 adaptive ROI cropping for scoreboard columns."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import mss
from PIL import Image, ImageDraw

from wt_xvm.config import NormalizedRoi, OcrRoiConfig

logger = logging.getLogger("wt_xvm.ocr.screen_capture")


@dataclass(slots=True)
class CapturedColumns:
    """Cropped left (Allies) and right (Enemies) scoreboard nickname column images."""

    left_img: Image.Image
    right_img: Image.Image
    preview_png: bytes


def compute_safe_16_9_frame(width: int, height: int) -> tuple[int, int, int, int]:
    """Compute centered 16:9 UI safe-frame (x0, y0, safe_w, safe_h) for ultrawide or standard screens."""
    if width <= 0 or height <= 0:
        return (0, 0, width, height)
    target_ratio = 16.0 / 9.0
    actual_ratio = width / float(height)
    if actual_ratio > target_ratio + 0.01:
        safe_w = int(round(height * target_ratio))
        x0 = (width - safe_w) // 2
        return (x0, 0, safe_w, height)
    if actual_ratio < target_ratio - 0.01:
        safe_h = int(round(width / target_ratio))
        y0 = (height - safe_h) // 2
        return (0, y0, width, safe_h)
    return (0, 0, width, height)


def _crop_roi(full_img: Image.Image, safe_box: tuple[int, int, int, int], roi: NormalizedRoi) -> Image.Image:
    sx, sy, sw, sh = safe_box
    x1 = max(0, min(full_img.width, int(sx + roi.x * sw)))
    y1 = max(0, min(full_img.height, int(sy + roi.y * sh)))
    x2 = max(x1 + 1, min(full_img.width, int(sx + (roi.x + roi.w) * sw)))
    y2 = max(y1 + 1, min(full_img.height, int(sy + (roi.y + roi.h) * sh)))
    return full_img.crop((x1, y1, x2, y2))


def crop_scoreboard_columns(full_img: Image.Image, roi_cfg: OcrRoiConfig) -> CapturedColumns:
    """Crop ally and enemy nickname columns and render an annotated preview PNG."""
    safe_box = compute_safe_16_9_frame(full_img.width, full_img.height)
    left_img = _crop_roi(full_img, safe_box, roi_cfg.left_nicks)
    right_img = _crop_roi(full_img, safe_box, roi_cfg.right_nicks)

    # Render annotated downscaled preview for Settings UI ROI calibration
    preview = full_img.copy()
    draw = ImageDraw.Draw(preview)
    sx, sy, sw, sh = safe_box
    for roi, color in ((roi_cfg.left_nicks, "#38bdf8"), (roi_cfg.right_nicks, "#f87171")):
        x1 = int(sx + roi.x * sw)
        y1 = int(sy + roi.y * sh)
        x2 = int(sx + (roi.x + roi.w) * sw)
        y2 = int(sy + (roi.y + roi.h) * sh)
        draw.rectangle([x1, y1, x2, y2], outline=color, width=4)

    preview.thumbnail((1280, 720))
    buf = io.BytesIO()
    preview.save(buf, format="PNG")
    return CapturedColumns(left_img=left_img, right_img=right_img, preview_png=buf.getvalue())


def capture_scoreboard_columns(roi_cfg: OcrRoiConfig, monitor_index: int = 1) -> CapturedColumns:
    """Capture primary monitor passively via mss and crop left/right scoreboard columns."""
    with mss.mss() as sct:
        monitors = sct.monitors
        idx = monitor_index if 0 < monitor_index < len(monitors) else 1
        shot = sct.grab(monitors[idx])
        full_img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    return crop_scoreboard_columns(full_img, roi_cfg)
