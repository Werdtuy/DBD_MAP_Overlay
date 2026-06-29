from __future__ import annotations

from .config import AppConfig
from .focus import get_monitors


def compute_auto_ocr_region(config: AppConfig) -> list[int]:
    monitors = get_monitors()
    monitor_index = min(max(config.overlay.monitor_index, 0), len(monitors) - 1)
    monitor = monitors[monitor_index]

    width = max(260, int(monitor.width * config.detection.auto_region_width_ratio))
    height = max(58, int(monitor.height * config.detection.auto_region_height_ratio))
    center_x = monitor.x + monitor.width // 2
    top = monitor.y + int(monitor.height * config.detection.auto_region_top_ratio)
    left = center_x - width // 2

    left = max(monitor.x, min(left, monitor.x + monitor.width - width))
    top = max(monitor.y, min(top, monitor.y + monitor.height - height))
    return [left, top, width, height]


def active_ocr_region(config: AppConfig) -> list[int]:
    if config.detection.auto_ocr_region:
        return compute_auto_ocr_region(config)
    if len(config.detection.ocr_region) < 4:
        return [0, 0, 1, 1]
    left, top, width, height = [int(value) for value in config.detection.ocr_region[:4]]
    return [left, top, max(1, width), max(1, height)]
