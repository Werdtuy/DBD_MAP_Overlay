from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from queue import Queue
import shutil
import sys
import threading
import uuid

from PIL import Image
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .app_logging import configure_logging
from .auto_launch import ensure_auto_launcher, is_dead_by_daylight_running, start_watcher_if_needed
from .config import AppConfig, ConfigStore, EscapeStreakPlayer, Profile
from .detector import DetectionResult, DetectionWorker
from .focus import FocusGate, get_monitors
from .hotkeys import HotkeyManager
from .hens_callouts import CALLOUTS_URL, import_hens_callouts
from .license_gate import LicenseStore
from .maps import MapAsset, MapLibrary
from .ocr_region import active_ocr_region, compute_auto_ocr_region
from .plugins import PluginManager
from .rendering import AnimatedImage, render_frame
from .streak_sync import StreakSyncClient, StreakSyncError
from .tesseract import is_tesseract_path, tesseract_search_report
from .update_status import AppUpdateStatus, check_for_app_update, stage_app_update
from .updates import MapUpdateChecker


COLORS = {
    "bg": "#050506",
    "sidebar": "#090708",
    "surface": "#100D0E",
    "panel": "#171214",
    "panel_dark": "#0A0809",
    "input": "#221A1C",
    "input_hover": "#322528",
    "accent": "#C91F32",
    "accent_hover": "#F04452",
    "accent_dark": "#75131D",
    "text": "#F6E7D8",
    "muted": "#B29B8C",
    "border": "#4A2428",
    "gold": "#D7B98B",
    "danger": "#7C111B",
}


APP_STYLESHEET = f"""
* {{
    font-family: "Segoe UI";
    color: {COLORS["text"]};
}}
QMainWindow, QWidget#root {{
    background: {COLORS["bg"]};
}}
QFrame#topBar, QFrame#sidebar {{
    background: rgba(7, 7, 8, 226);
    border: 0;
}}
QFrame#navRail {{
    background: rgba(4, 5, 6, 232);
    border: 1px solid rgba(86, 24, 31, 150);
    border-radius: 8px;
}}
QFrame#contentSurface {{
    background: rgba(8, 8, 9, 224);
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
}}
QFrame#card, QScrollArea#card {{
    background: rgba(17, 14, 15, 220);
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
}}
QFrame#darkCard {{
    background: rgba(5, 5, 6, 232);
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
}}
QLabel#muted {{
    color: {COLORS["muted"]};
}}
QLabel#title {{
    color: {COLORS["text"]};
    font-size: 26px;
    font-weight: 800;
}}
QLabel#sectionTitle {{
    color: {COLORS["text"]};
    font-size: 18px;
    font-weight: 800;
}}
QLabel#hudTitle {{
    color: {COLORS["text"]};
    font-size: 16px;
    font-weight: 900;
    letter-spacing: 1px;
}}
QLineEdit, QTextEdit, QComboBox, QSpinBox, QListWidget {{
    background: {COLORS["input"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    selection-background-color: {COLORS["accent_dark"]};
    color: {COLORS["text"]};
    padding: 6px;
}}
QTextEdit {{
    background: {COLORS["panel_dark"]};
}}
QPushButton {{
    background: {COLORS["accent"]};
    border: 0;
    border-radius: 6px;
    color: {COLORS["text"]};
    padding: 8px 12px;
    font-weight: 700;
}}
QPushButton:hover {{
    background: {COLORS["accent_hover"]};
}}
QPushButton:disabled {{
    background: {COLORS["input"]};
    color: {COLORS["muted"]};
}}
QPushButton[secondary="true"] {{
    background: {COLORS["input"]};
    border: 1px solid {COLORS["border"]};
}}
QPushButton[secondary="true"]:hover {{
    background: {COLORS["input_hover"]};
}}
QCheckBox {{
    spacing: 9px;
}}
QCheckBox::indicator {{
    width: 34px;
    height: 18px;
    border-radius: 9px;
    background: {COLORS["input_hover"]};
    border: 1px solid {COLORS["border"]};
}}
QCheckBox::indicator:checked {{
    background: {COLORS["accent"]};
}}
QSlider::groove:horizontal {{
    height: 5px;
    background: {COLORS["input_hover"]};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {COLORS["accent"]};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {COLORS["accent"]};
    width: 18px;
    height: 18px;
    margin: -7px 0;
    border-radius: 9px;
}}
QListWidget::item {{
    padding: 7px;
    border-radius: 5px;
}}
QListWidget::item:selected {{
    background: {COLORS["accent_dark"]};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS["input_hover"]};
    border-radius: 5px;
}}
"""


def pil_to_pixmap(image: Image.Image) -> QPixmap:
    rgba = image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimage = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimage.copy())


def make_button(text: str, command=None, secondary: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("secondary", secondary)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if command:
        button.clicked.connect(command)
    return button


def label(text: str, role: str | None = None) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setObjectName(role)
    return widget


def selected_position_grid(corner: str) -> tuple[int, int]:
    legacy = {
        "top_left": (0, 0),
        "top_center": (0, 1),
        "top_right": (0, 3),
        "middle_left": (1, 0),
        "middle_right": (1, 3),
        "bottom_left": (3, 0),
        "bottom_center": (3, 1),
        "bottom_right": (3, 3),
    }
    if corner in legacy:
        return legacy[corner]
    if corner.startswith("grid_"):
        parts = corner.split("_")
        if len(parts) == 3:
            try:
                row = min(max(int(parts[1]), 0), 3)
                col = min(max(int(parts[2]), 0), 3)
                if row in {0, 3} or col in {0, 3}:
                    return row, col
            except ValueError:
                pass
    return 1, 3


def card(object_name: str = "card") -> QFrame:
    frame = QFrame()
    frame.setObjectName(object_name)
    return frame


class HorrorRoot(QWidget):
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QColor("#040405"))
        gradient.setColorAt(0.55, QColor("#070808"))
        gradient.setColorAt(1.0, QColor("#13080A"))
        painter.fillRect(self.rect(), gradient)

        painter.setPen(QPen(QColor(74, 18, 24, 70), 1))
        for idx in range(18):
            x = int(self.width() * (idx / 17))
            painter.drawLine(x, 0, max(0, x - 120), self.height())
        painter.setPen(QPen(QColor(125, 28, 37, 35), 2))
        for idx in range(6):
            y = 64 + idx * 113
            painter.drawLine(0, y, self.width(), y - 80)


class ClawLogo(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(46, 46)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(COLORS["accent"]), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for idx, x in enumerate((14, 23, 32)):
            painter.drawLine(x, 8 + idx * 2, x - 4, 35)
            painter.drawLine(x - 1, 8 + idx * 2, x + 4, 33)


class NavButton(QPushButton):
    def __init__(self, icon_name: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.icon_name = icon_name
        self.nav_text = text
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(124, 96)
        self.setStyleSheet("background: transparent; border: 0;")

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        selected = self.isChecked()
        if selected:
            painter.fillRect(rect, QColor(45, 9, 13, 190))
            painter.setPen(QPen(QColor(COLORS["accent"]), 2))
            painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())
            painter.drawRoundedRect(QRectF(rect), 4, 4)
        elif self.underMouse():
            painter.fillRect(rect, QColor(30, 18, 20, 150))
        color = QColor(COLORS["accent"] if selected else "#8D8D8D")
        self._draw_icon(painter, QRectF(36, 15, 52, 42), color)
        painter.setPen(QColor(COLORS["accent"] if selected else COLORS["muted"]))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.drawText(QRectF(6, 61, self.width() - 12, 24), Qt.AlignmentFlag.AlignCenter, self.nav_text.upper())

    def _draw_icon(self, painter: QPainter, rect: QRectF, color: QColor) -> None:
        painter.setPen(QPen(color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx = rect.center().x()
        cy = rect.center().y()
        if self.icon_name == "claw":
            for idx, x in enumerate((rect.left() + 14, rect.center().x(), rect.right() - 14)):
                painter.drawLine(QPointF(x, rect.top() + 2 + idx), QPointF(x - 5, rect.bottom() - 4))
                painter.drawLine(QPointF(x, rect.top() + 2 + idx), QPointF(x + 5, rect.bottom() - 6))
        elif self.icon_name == "streak":
            painter.drawEllipse(QPointF(cx, cy), 15, 15)
            painter.drawLine(QPointF(cx, rect.top() + 4), QPointF(cx, cy + 3))
            painter.drawLine(QPointF(cx, cy + 3), QPointF(cx + 10, cy - 8))
        elif self.icon_name == "ocr":
            painter.drawEllipse(QRectF(rect.left() + 8, rect.top() + 5, 24, 24))
            painter.drawLine(QPointF(rect.left() + 28, rect.top() + 29), QPointF(rect.right() - 7, rect.bottom() - 6))
        elif self.icon_name == "keys":
            key_rect = rect.adjusted(5, 10, -5, -10)
            painter.drawRoundedRect(key_rect, 4, 4)
            for row in range(2):
                for col in range(4):
                    painter.drawRect(QRectF(key_rect.left() + 7 + col * 9, key_rect.top() + 7 + row * 9, 5, 5))
        elif self.icon_name == "settings":
            painter.drawEllipse(QPointF(cx, cy), 9, 9)
            for angle in range(0, 360, 60):
                import math

                rad = math.radians(angle)
                inner = QPointF(cx + math.cos(rad) * 15, cy + math.sin(rad) * 15)
                outer = QPointF(cx + math.cos(rad) * 23, cy + math.sin(rad) * 23)
                painter.drawLine(inner, outer)
        elif self.icon_name == "logs":
            for idx in range(4):
                y = rect.top() + 9 + idx * 8
                painter.drawLine(QPointF(rect.left() + 8, y), QPointF(rect.right() - 8, y))
                painter.drawEllipse(QPointF(rect.left() + 3, y), 1.5, 1.5)
        elif self.icon_name == "maps":
            painter.drawRect(QRectF(rect.left() + 8, rect.top() + 7, 30, 28))
            painter.drawLine(QPointF(rect.left() + 18, rect.top() + 7), QPointF(rect.left() + 18, rect.top() + 35))
            painter.drawLine(QPointF(rect.left() + 28, rect.top() + 7), QPointF(rect.left() + 28, rect.top() + 35))
        else:
            painter.drawRoundedRect(rect.adjusted(8, 8, -8, -8), 5, 5)


class UiBus(QObject):
    run_on_main = Signal(object)
    detected = Signal(object)


class PositionPicker(QWidget):
    position_changed = Signal(int, int)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.setMinimumHeight(270)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(COLORS["panel_dark"]))
        margin = 22
        area = self.rect().adjusted(margin, margin, -margin, -margin)
        painter.setPen(QPen(QColor(COLORS["border"]), 2))
        painter.drawRoundedRect(area, 8, 8)
        selected = self._selected()
        points = self._points(area)
        for row, col, center in points:
            is_selected = (row, col) == selected
            rect = QRectF(center.x() - 32, center.y() - 22, 64, 44)
            painter.setBrush(QColor(COLORS["accent_dark"] if is_selected else COLORS["input"]))
            painter.setPen(QPen(QColor(COLORS["accent"] if is_selected else COLORS["border"]), 3 if is_selected else 2))
            painter.drawRoundedRect(rect, 6, 6)
            painter.setPen(QColor(COLORS["text"] if is_selected else COLORS["muted"]))
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{row + 1},{col + 1}")

    def mousePressEvent(self, event) -> None:
        area = self.rect().adjusted(22, 22, -22, -22)
        clicked = QPointF(event.position())
        nearest = min(
            self._points(area),
            key=lambda item: (item[2].x() - clicked.x()) ** 2 + (item[2].y() - clicked.y()) ** 2,
        )
        self.position_changed.emit(nearest[0], nearest[1])

    @staticmethod
    def edge_points() -> list[tuple[int, int]]:
        return [(row, col) for row in range(4) for col in range(4) if row in {0, 3} or col in {0, 3}]

    def _points(self, area) -> list[tuple[int, int, QPointF]]:
        left = area.left() + 34
        right = area.right() - 34
        top = area.top() + 24
        bottom = area.bottom() - 24
        return [
            (
                row,
                col,
                QPointF(left + (right - left) * col / 3, top + (bottom - top) * row / 3),
            )
            for row, col in self.edge_points()
        ]

    def _selected(self) -> tuple[int, int]:
        return selected_position_grid(self.config.overlay.corner)


class QtOverlayWindow(QWidget):
    def __init__(self, config: AppConfig, logger: logging.Logger) -> None:
        super().__init__(None)
        self.config = config
        self.logger = logger
        self.visible = config.overlay.enabled
        self.asset: MapAsset | None = None
        self.animator: AnimatedImage | None = None
        self._last_hidden_reason = ""
        self._readout_timer = QTimer(self)
        self._readout_timer.setSingleShot(True)
        self._readout_timer.timeout.connect(self.clear_ocr_readout)
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.timeout.connect(self._render_next_frame)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("background: transparent; border: 0;")
        layout.addWidget(self.image_label)
        self.streak_frame = QFrame()
        self.streak_frame.setObjectName("darkCard")
        streak_layout = QVBoxLayout(self.streak_frame)
        streak_layout.setContentsMargins(8, 4, 8, 5)
        streak_layout.setSpacing(0)
        self.streak_title = QLabel("")
        self.streak_title.setObjectName("hudTitle")
        self.streak_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.streak_detail = QLabel("")
        self.streak_detail.setObjectName("muted")
        self.streak_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        streak_layout.addWidget(self.streak_title)
        streak_layout.addWidget(self.streak_detail)
        layout.addWidget(self.streak_frame)
        self.readout_label = QLabel("")
        self.readout_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.readout_label.setWordWrap(True)
        self.readout_label.setStyleSheet("background: transparent; color: white; font-weight: 800;")
        layout.addWidget(self.readout_label)
        self.refresh_escape_streak()

    def start(self) -> None:
        self._apply_visibility()

    def stop(self) -> None:
        self._frame_timer.stop()
        self._readout_timer.stop()
        self.close()

    def set_asset(self, asset: MapAsset | None) -> None:
        self.asset = asset
        self.animator = None
        if asset:
            try:
                self.animator = AnimatedImage(asset.path)
                self.logger.info("Overlay map set to %s", asset.name)
            except Exception as exc:
                self.logger.error("Could not load map image %s: %s", asset.path, exc)
        self._render_next_frame()
        self._apply_visibility()

    def toggle(self) -> None:
        self.visible = not self.visible
        self.config.overlay.enabled = self.visible
        self._apply_visibility()

    def show_for_setup(self, _seconds: int = 15) -> None:
        self.visible = True
        self.config.overlay.enabled = True
        self._apply_visibility()

    def status(self) -> str:
        if self._last_hidden_reason:
            return f"Hidden: {self._last_hidden_reason}"
        return "Visible"

    def refresh_settings(self) -> None:
        self.setWindowOpacity(self.config.overlay.opacity)
        self.refresh_escape_streak()
        self._render_next_frame()
        self._apply_visibility()

    def set_ocr_readout(self, map_name: str, confidence: float, hotkey: str = "") -> None:
        hotkey_text = f" [{hotkey.upper()}]" if hotkey else ""
        self.readout_label.setText(f"Map Detected: {map_name}{hotkey_text}\nAccuracy: {confidence:.0%}")
        self._readout_timer.start(2000)
        self._apply_visibility()

    def clear_ocr_readout(self) -> None:
        self.readout_label.setText("")
        self._apply_visibility()

    def refresh_escape_streak(self) -> None:
        streak = self.config.escape_streak
        self.streak_frame.setVisible(streak.enabled)
        if not streak.enabled:
            return
        lobby = streak.lobby_code.strip().upper() or "LOCAL LOBBY"
        players = [f"P{idx + 1}:{player.status[:1].upper()}" for idx, player in enumerate(streak.players[:4])]
        self.streak_title.setText(f"ESCAPE STREAK  {max(0, int(streak.streak))}")
        self.streak_detail.setText(f"{lobby}  |  {'  '.join(players)}")

    def _window_height(self) -> int:
        streak_height = 54 if self.config.escape_streak.enabled else 0
        readout_height = 44 if self.readout_label.text() else 0
        return int(self.config.overlay.size) + streak_height + readout_height

    def _position(self) -> tuple[int, int]:
        overlay = self.config.overlay
        monitors = get_monitors()
        monitor = monitors[min(max(overlay.monitor_index, 0), len(monitors) - 1)]
        size = int(overlay.size)
        height = self._window_height()
        left = monitor.x + overlay.margin_x
        top = monitor.y + overlay.margin_y
        right = monitor.x + monitor.width - size - overlay.margin_x
        bottom = monitor.y + monitor.height - height - overlay.margin_y
        x_points = [left + round((right - left) * idx / 3) for idx in range(4)]
        y_points = [top + round((bottom - top) * idx / 3) for idx in range(4)]
        row, col = selected_position_grid(overlay.corner)
        return x_points[col], y_points[row]

    def _apply_visibility(self) -> None:
        should_show = self.visible and self.config.overlay.enabled and self.asset is not None
        if should_show:
            size = int(self.config.overlay.size)
            x, y = self._position()
            self.setGeometry(x, y, size, self._window_height())
            self.setWindowOpacity(self.config.overlay.opacity)
            self.show()
            self.raise_()
            self._last_hidden_reason = ""
        else:
            self._last_hidden_reason = self._hidden_reason()
            self.hide()

    def _hidden_reason(self) -> str:
        if not self.visible or not self.config.overlay.enabled:
            return "overlay disabled"
        if self.asset is None:
            return "no map selected"
        return "unknown"

    def _render_next_frame(self) -> None:
        self._frame_timer.stop()
        if not self.animator:
            self.image_label.clear()
            return
        frame, duration = self.animator.next_frame()
        overlay = self.config.overlay
        rendered = render_frame(
            frame,
            overlay.size,
            overlay.zoom,
            overlay.border_width,
            overlay.border_color,
            overlay.corner_radius,
            0,
        )
        self.image_label.setPixmap(pil_to_pixmap(rendered))
        if self.config.detection.performance_mode and not self._allow_performance_animation():
            return
        speed = max(0.1, overlay.animation_speed)
        self._frame_timer.start(max(20, int(duration / speed)))

    def _allow_performance_animation(self) -> bool:
        return bool(self.asset and self.asset.path.suffix.lower() == ".gif")


class QtPreviewRenderer(QObject):
    def __init__(self, label_widget: QLabel, config: AppConfig) -> None:
        super().__init__()
        self.label = label_widget
        self.config = config
        self.asset: MapAsset | None = None
        self.animator: AnimatedImage | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render)

    def set_asset(self, asset: MapAsset | None) -> None:
        self.asset = asset
        try:
            self.animator = AnimatedImage(asset.path) if asset else None
        except Exception:
            self.animator = None
        self._render()

    def refresh(self) -> None:
        self._render()

    def _render(self) -> None:
        self._timer.stop()
        if not self.animator:
            self.label.setText("No map selected")
            self.label.setPixmap(QPixmap())
            return
        frame, duration = self.animator.next_frame()
        overlay = self.config.overlay
        rendered = render_frame(
            frame,
            min(320, overlay.size),
            overlay.zoom,
            overlay.border_width,
            overlay.border_color,
            overlay.corner_radius,
            0,
        )
        self.label.setPixmap(pil_to_pixmap(rendered))
        self.label.setText("")
        if self.config.detection.performance_mode:
            return
        self._timer.start(max(20, int(duration / max(0.1, overlay.animation_speed))))


class QtOcrRegionWindow(QWidget):
    def __init__(self, logger: logging.Logger) -> None:
        super().__init__(None)
        self.logger = logger
        self.remaining = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def show_region(self, region: list[int], seconds: int = 8) -> None:
        left, top, width, height = [max(0, int(value)) for value in region]
        if width <= 0 or height <= 0:
            self.logger.warning("Cannot show OCR region: width and height must be greater than zero")
            return
        self.setGeometry(left, top, width, height)
        self.remaining = max(1, int(seconds))
        self.show()
        self.raise_()
        self._timer.start(1000)
        self.update()
        self.logger.info("Showing OCR scan region at left=%s top=%s width=%s height=%s", left, top, width, height)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(4, 4, -4, -4)
        painter.setPen(QPen(QColor("#00E5FF"), 4))
        painter.drawRect(rect)
        label_rect = QRectF(self.width() / 2 - 100, 7, 200, 28)
        painter.fillRect(label_rect, QColor("#111827"))
        painter.setPen(QPen(QColor("#00E5FF"), 1))
        painter.drawRect(label_rect)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, f"OCR scan box: {self.remaining}s")

    def _tick(self) -> None:
        self.remaining -= 1
        if self.remaining <= 0:
            self._timer.stop()
            self.hide()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self.close()


class OverlayQtApp(QMainWindow):
    def __init__(self, root_path: Path, start_minimized: bool = False, close_when_dbd_exits: bool = False) -> None:
        self.qt_app = QApplication.instance() or QApplication(sys.argv)
        super().__init__()
        self.root_path = root_path
        self.start_minimized = start_minimized
        self.close_when_dbd_exits = close_when_dbd_exits
        self.store = ConfigStore(root_path)
        self.config = self.store.load()
        self.config.map_library_visible = False
        self.config.overlay.border_width = 0
        if not self.config.escape_streak.sync_player_id:
            self.config.escape_streak.sync_player_id = uuid.uuid4().hex
        self.logger, self.log_queue = configure_logging(root_path)
        self.logger.info("Settings imported automatically from %s", self.store.path)
        ensure_auto_launcher(root_path, self.logger)
        start_watcher_if_needed(root_path, self.logger)

        self.library = MapLibrary(root_path, self.config.maps_dir)
        self.library.reload()
        self.focus_gate = FocusGate(self.config, self.logger)
        self.plugins = PluginManager(root_path)
        self.plugins.load()
        self.update_checker = MapUpdateChecker(self.config, self.library, self.logger)
        self.overlay = QtOverlayWindow(self.config, self.logger)
        self.ocr_region_overlay = QtOcrRegionWindow(self.logger)
        self.hotkeys = HotkeyManager(self.config, self.focus_gate, self.logger)
        self.detector = DetectionWorker(self.config, self.library, self.focus_gate, self.logger, self._detected_from_thread)

        self.bus = UiBus()
        self.bus.run_on_main.connect(lambda fn: fn())
        self.bus.detected.connect(self._handle_detection)
        self.preview: QtPreviewRenderer | None = None
        self.current_map_name = ""
        self.current_variant_index = 0
        self._monitor_names: list[str] = []
        self._game_absent_checks = 0
        self._map_settings_visible = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_now)
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._pump_logs_once)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_overlay_status_once)
        self._game_timer = QTimer(self)
        self._game_timer.timeout.connect(self._watch_game_lifetime)
        self._streak_sync_timer = QTimer(self)
        self._streak_sync_timer.timeout.connect(self._poll_streak_lobby)
        self._streak_sync_busy = False
        self._streak_applying_remote = False

        self.setWindowTitle("DBD Companion Overlay")
        self.resize(1180, 780)
        self.setMinimumSize(1020, 680)
        self.setStyleSheet(APP_STYLESHEET)
        self._set_app_icon()
        self._build_ui()
        self._save_now()
        self._auto_find_tesseract()
        self._register_hotkeys()
        self.overlay.start()
        self._update_hens_maps_on_startup()
        if not self.config.detection.performance_mode:
            self.update_checker.check_async()
        else:
            self.logger.info("Performance mode enabled: one-time map load runs, ongoing background polling is disabled")
        self._select_initial_map()
        self._pump_logs_once()
        self._update_overlay_status_once()
        self._apply_performance_timer_state()
        self._apply_streak_sync_timer_state()
        if self.start_minimized:
            QTimer.singleShot(300, self.showMinimized)
        if self.close_when_dbd_exits:
            self._game_timer.start(5000)
        self.logger.info("App ready. Loaded %s map(s).", len(self.library.entries))

    def run(self) -> None:
        self.showMinimized() if self.start_minimized else self.show()
        self.qt_app.exec()

    def closeEvent(self, event) -> None:
        self._sync_text_settings_to_config()
        self._save_now()
        self.hotkeys.unregister()
        self._log_timer.stop()
        self._status_timer.stop()
        self._game_timer.stop()
        self._streak_sync_timer.stop()
        self.ocr_region_overlay.stop()
        self.overlay.stop()
        event.accept()

    def _set_app_icon(self) -> None:
        icon_path = self._resource_path("assets", "app_icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _resource_path(self, *parts: str) -> Path:
        if getattr(sys, "frozen", False):
            return Path(getattr(sys, "_MEIPASS", self.root_path)).joinpath(*parts)
        return self.root_path.joinpath(*parts)

        self.tabs.addTab(self._build_overlay_tab(), "▣ Map")
        self.tabs.addTab(self._build_escape_streak_tab(), "◆ Streak")
        self.tabs.addTab(self._build_detection_tab(), "⌕ OCR")
        self.tabs.addTab(self._build_hotkeys_tab(), "⌨ Keys")
        self.tabs.addTab(self._build_settings_tab(), "⚙ Settings")
        self.tabs.addTab(self._build_logs_tab(), "≡ Logs")
        self._refresh_map_list()
        self._apply_map_library_visibility()

    def _build_ui(self) -> None:
        root = HorrorRoot()
        root.setObjectName("root")
        self.setCentralWidget(root)
        shell = QVBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        top = QFrame()
        top.setObjectName("topBar")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(18, 8, 18, 8)
        top_layout.addWidget(ClawLogo())
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = label("DBD Companion Overlay", "title")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title_box.addWidget(title)
        title_box.addWidget(label(__version__, "muted"))
        top_layout.addLayout(title_box)
        top_layout.addStretch(1)
        self.app_update_status_label = label("", "muted")
        top_layout.addWidget(self.app_update_status_label)
        self.app_update_button = make_button("Check for Updates", self._check_for_app_updates, secondary=True)
        top_layout.addWidget(self.app_update_button)
        shell.addWidget(top)

        body = QHBoxLayout()
        body.setContentsMargins(12, 0, 12, 14)
        body.setSpacing(12)
        shell.addLayout(body, 1)

        nav = QFrame()
        nav.setObjectName("navRail")
        nav.setFixedWidth(138)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 10, 0, 10)
        nav_layout.setSpacing(0)
        self.map_library_nav_button = NavButton("maps", "Maps")
        self.map_library_nav_button.clicked.connect(self._toggle_map_library)
        nav_layout.addWidget(self.map_library_nav_button)
        nav_layout.addSpacing(6)
        body.addWidget(nav)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(290)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(14, 18, 14, 14)
        sidebar_layout.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(label("Map Library", "sectionTitle"))
        header.addStretch(1)
        self.map_toggle_button = make_button("Hide", self._toggle_map_library, secondary=True)
        header.addWidget(self.map_toggle_button)
        sidebar_layout.addLayout(header)
        self.map_list = QListWidget()
        self.map_list.itemClicked.connect(lambda item: self.select_map(item.text(), "manual"))
        sidebar_layout.addWidget(self.map_list, 1)
        row = QGridLayout()
        row.setSpacing(8)
        row.addWidget(make_button("Add", self._add_map), 0, 0)
        row.addWidget(make_button("Reload", self.reload_maps), 0, 1)
        row.addWidget(make_button("Open Folder", self._open_maps_folder, secondary=True), 1, 0, 1, 2)
        row.addWidget(make_button("Update Hens Maps", self._import_hens_maps), 2, 0, 1, 2)
        row.addWidget(label("Callout maps: Hens333 website\nImages credited to Lethia", "muted"), 3, 0, 1, 2)
        row.addWidget(make_button("Open Hens333 Callouts", self._open_hens_callouts_site, secondary=True), 4, 0, 1, 2)
        sidebar_layout.addLayout(row)
        body.addWidget(self.sidebar)

        content = QFrame()
        content.setObjectName("contentSurface")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack)
        body.addWidget(content, 1)

        pages = [
            ("claw", "General", self._build_overlay_tab()),
            ("streak", "Players", self._build_escape_streak_tab()),
            ("ocr", "OCR", self._build_detection_tab()),
            ("keys", "Hotkeys", self._build_hotkeys_tab()),
            ("settings", "Settings", self._build_settings_tab()),
            ("logs", "Logs", self._build_logs_tab()),
        ]
        self.nav_buttons: list[NavButton] = []
        for index, (icon_name, page_name, page) in enumerate(pages):
            button = NavButton(icon_name, page_name)
            button.clicked.connect(lambda _checked=False, idx=index: self._set_nav_page(idx))
            nav_layout.addWidget(button)
            self.nav_buttons.append(button)
            self.stack.addWidget(page)
        nav_layout.addStretch(1)
        self._set_nav_page(0)
        self._refresh_map_list()
        self._apply_map_library_visibility()

    def _set_nav_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for idx, button in enumerate(self.nav_buttons):
            button.setChecked(idx == index)

    def _build_overlay_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        preview_card = card()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(18, 18, 18, 18)
        preview_header = QHBoxLayout()
        preview_header.addWidget(label("Live Preview", "sectionTitle"))
        preview_header.addStretch(1)
        self.preview_toggle_hotkey_label = label(self._toggle_overlay_hotkey_text(), "muted")
        preview_header.addWidget(self.preview_toggle_hotkey_label)
        preview_layout.addLayout(preview_header)
        self.preview_label = QLabel("No map selected")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(430)
        self.preview_label.setStyleSheet(f"background: {COLORS['panel_dark']}; border-radius: 4px;")
        preview_layout.addWidget(self.preview_label, 1)
        self.preview_streak_frame = card("darkCard")
        streak_layout = QVBoxLayout(self.preview_streak_frame)
        streak_layout.setContentsMargins(12, 8, 12, 8)
        self.preview_streak_title = label("", "hudTitle")
        self.preview_streak_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_streak_detail = label("", "muted")
        self.preview_streak_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        streak_layout.addWidget(self.preview_streak_title)
        streak_layout.addWidget(self.preview_streak_detail)
        preview_layout.addWidget(self.preview_streak_frame)
        self.preview = QtPreviewRenderer(self.preview_label, self.config)
        self._refresh_escape_streak_preview()
        layout.addWidget(preview_card, 3)

        controls_scroll = QScrollArea()
        controls_scroll.setObjectName("card")
        controls_scroll.setWidgetResizable(True)
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(16, 16, 16, 16)
        controls_layout.setSpacing(12)
        controls_scroll.setWidget(controls)
        self.enabled_check = QCheckBox("Overlay enabled")
        self.enabled_check.setChecked(self.config.overlay.enabled)
        self.enabled_check.toggled.connect(self._toggle_enabled)
        controls_layout.addWidget(self.enabled_check)
        controls_layout.addWidget(make_button("Show Test Overlay", self._show_test_overlay))
        self.overlay_status_label = label("Overlay status: starting", "muted")
        controls_layout.addWidget(self.overlay_status_label)
        controls_layout.addWidget(label("Position", "sectionTitle"))
        self.position_picker = PositionPicker(self.config)
        self.position_picker.position_changed.connect(self._set_position_grid)
        controls_layout.addWidget(self.position_picker)
        self.map_settings_button = make_button("Show Map Settings", self._toggle_map_settings, secondary=True)
        controls_layout.addWidget(self.map_settings_button)
        self.map_settings_frame = card("darkCard")
        settings_layout = QVBoxLayout(self.map_settings_frame)
        settings_layout.setContentsMargins(12, 12, 12, 12)
        settings_layout.setSpacing(12)
        self._build_monitor_picker(settings_layout)
        self._add_slider(settings_layout, "Opacity", self.config.overlay.opacity, 0.2, 1.0, self._set_opacity)
        self._add_slider(settings_layout, "Size", self.config.overlay.size, 120, 720, self._set_size)
        self._add_slider(settings_layout, "Zoom", self.config.overlay.zoom, 0.4, 2.4, self._set_zoom)
        self._add_slider(settings_layout, "Corner radius", self.config.overlay.corner_radius, 0, 80, self._set_radius)
        self._add_slider(settings_layout, "Animation speed", self.config.overlay.animation_speed, 0.25, 3.0, self._set_animation_speed)
        self.rotate_check = QCheckBox("Minimap rotation ready")
        self.rotate_check.setChecked(self.config.overlay.rotate_with_minimap)
        self.rotate_check.toggled.connect(self._set_rotation)
        settings_layout.addWidget(self.rotate_check)
        self._build_profile_picker(settings_layout)
        controls_layout.addWidget(self.map_settings_frame)
        controls_layout.addStretch(1)
        self._apply_map_settings_visibility()
        layout.addWidget(controls_scroll, 2)
        return page

    def _build_overlay_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(0)

        controls = card()
        controls.setFixedWidth(430)
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(26, 24, 22, 18)
        controls_layout.setSpacing(14)

        controls_layout.addWidget(label("Overlay Enabled", "sectionTitle"))
        self.enabled_check = QCheckBox("Enable in-game overlay")
        self.enabled_check.setChecked(self.config.overlay.enabled)
        self.enabled_check.toggled.connect(self._toggle_enabled)
        controls_layout.addWidget(self.enabled_check)
        controls_layout.addWidget(make_button("Show Test Overlay", self._show_test_overlay))
        self.overlay_status_label = label("Overlay status: starting", "muted")
        controls_layout.addWidget(self.overlay_status_label)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet(f"color: {COLORS['border']};")
        controls_layout.addWidget(separator)

        controls_layout.addWidget(label("Map Selection", "sectionTitle"))
        map_row = QHBoxLayout()
        self.current_map_label = label(self.current_map_name or "No map selected", "muted")
        map_row.addWidget(self.current_map_label, 1)
        map_row.addWidget(make_button("Open Map Library", self._toggle_map_library, secondary=True))
        controls_layout.addLayout(map_row)

        controls_layout.addWidget(label("Overlay Position", "sectionTitle"))
        self.position_picker = PositionPicker(self.config)
        self.position_picker.setMinimumHeight(170)
        self.position_picker.position_changed.connect(self._set_position_grid)
        controls_layout.addWidget(self.position_picker)

        controls_layout.addWidget(label("Overlay Scale", "sectionTitle"))
        self._add_slider(controls_layout, "Opacity", self.config.overlay.opacity, 0.2, 1.0, self._set_opacity)
        self._add_slider(controls_layout, "Size", self.config.overlay.size, 120, 720, self._set_size)
        self.map_settings_button = make_button("Show Advanced Map Settings", self._toggle_map_settings, secondary=True)
        controls_layout.addWidget(self.map_settings_button)

        self.map_settings_frame = card("darkCard")
        settings_layout = QVBoxLayout(self.map_settings_frame)
        settings_layout.setContentsMargins(14, 14, 14, 14)
        settings_layout.setSpacing(12)
        self._build_monitor_picker(settings_layout)
        self._add_slider(settings_layout, "Zoom", self.config.overlay.zoom, 0.4, 2.4, self._set_zoom)
        self._add_slider(settings_layout, "Corner radius", self.config.overlay.corner_radius, 0, 80, self._set_radius)
        self._add_slider(settings_layout, "Animation speed", self.config.overlay.animation_speed, 0.25, 3.0, self._set_animation_speed)
        self.rotate_check = QCheckBox("Minimap rotation ready")
        self.rotate_check.setChecked(self.config.overlay.rotate_with_minimap)
        self.rotate_check.toggled.connect(self._set_rotation)
        settings_layout.addWidget(self.rotate_check)
        self._build_profile_picker(settings_layout)
        controls_layout.addWidget(self.map_settings_frame)
        controls_layout.addStretch(1)
        self._apply_map_settings_visibility()
        layout.addWidget(controls)

        preview_card = card()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(24, 24, 24, 20)
        preview_layout.setSpacing(14)
        preview_header = QVBoxLayout()
        preview_header.addWidget(label("Live Preview", "sectionTitle"))
        preview_header.addWidget(label("This is how your overlay will look in-game.", "muted"))
        preview_layout.addLayout(preview_header)
        self.preview_label = QLabel("No map selected")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(430)
        self.preview_label.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            "stop:0 #050607, stop:0.55 #09100E, stop:1 #16090A);"
            f"border: 1px solid {COLORS['border']}; border-radius: 8px;"
        )
        preview_layout.addWidget(self.preview_label, 1)
        self.preview_streak_frame = card("darkCard")
        streak_layout = QVBoxLayout(self.preview_streak_frame)
        streak_layout.setContentsMargins(12, 8, 12, 8)
        self.preview_streak_title = label("", "hudTitle")
        self.preview_streak_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_streak_detail = label("", "muted")
        self.preview_streak_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        streak_layout.addWidget(self.preview_streak_title)
        streak_layout.addWidget(self.preview_streak_detail)
        preview_layout.addWidget(self.preview_streak_frame)
        self.preview_toggle_hotkey_label = label(self._toggle_overlay_hotkey_text(), "muted")
        preview_layout.addWidget(self.preview_toggle_hotkey_label)
        self.preview = QtPreviewRenderer(self.preview_label, self.config)
        self._refresh_escape_streak_preview()
        layout.addWidget(preview_card, 1)
        return page

    def _build_escape_streak_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        left = card()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(18, 18, 18, 18)
        left_layout.setSpacing(14)
        left_layout.addWidget(label("Escape Streak", "sectionTitle"))
        self.streak_enabled_check = QCheckBox("Show streak under the map")
        self.streak_enabled_check.setChecked(self.config.escape_streak.enabled)
        self.streak_enabled_check.toggled.connect(self._set_escape_streak_enabled)
        left_layout.addWidget(self.streak_enabled_check)
        left_layout.addWidget(label("Lobby code", "muted"))
        self.lobby_code_entry = QLineEdit(self.config.escape_streak.lobby_code)
        self.lobby_code_entry.textChanged.connect(self._sync_escape_streak_settings)
        left_layout.addWidget(self.lobby_code_entry)
        row = QHBoxLayout()
        row.addWidget(label("Current streak", "muted"))
        self.streak_spin = QSpinBox()
        self.streak_spin.setRange(0, 999)
        self.streak_spin.setValue(max(0, int(self.config.escape_streak.streak)))
        self.streak_spin.valueChanged.connect(self._sync_escape_streak_settings)
        row.addWidget(self.streak_spin)
        row.addWidget(make_button("+1", lambda: self.streak_spin.setValue(self.streak_spin.value() + 1)))
        row.addWidget(make_button("-1", lambda: self.streak_spin.setValue(max(0, self.streak_spin.value() - 1)), secondary=True))
        row.addWidget(make_button("Reset", lambda: self.streak_spin.setValue(0), secondary=True))
        left_layout.addLayout(row)

        online = card("darkCard")
        online_layout = QVBoxLayout(online)
        online_layout.setContentsMargins(12, 12, 12, 12)
        online_layout.setSpacing(8)
        online_layout.addWidget(label("Online Lobby", "sectionTitle"))
        self.streak_sync_enabled_check = QCheckBox("Sync this streak with friends")
        self.streak_sync_enabled_check.setChecked(self.config.escape_streak.sync_enabled)
        self.streak_sync_enabled_check.toggled.connect(self._set_streak_sync_enabled)
        online_layout.addWidget(self.streak_sync_enabled_check)
        online_layout.addWidget(label("Sync server URL", "muted"))
        self.streak_server_entry = QLineEdit(self.config.escape_streak.sync_server_url)
        self.streak_server_entry.setPlaceholderText("https://your-worker.your-domain.workers.dev")
        self.streak_server_entry.textChanged.connect(self._sync_escape_streak_settings)
        online_layout.addWidget(self.streak_server_entry)
        online_layout.addWidget(label("Your display name", "muted"))
        self.streak_player_name_entry = QLineEdit(self.config.escape_streak.sync_player_name)
        self.streak_player_name_entry.setPlaceholderText("Your name")
        self.streak_player_name_entry.textChanged.connect(self._sync_escape_streak_settings)
        online_layout.addWidget(self.streak_player_name_entry)
        online_layout.addWidget(label("Lobby code", "muted"))
        self.streak_online_code_entry = QLineEdit(self.config.escape_streak.sync_lobby_code)
        self.streak_online_code_entry.setPlaceholderText("BLOOD-742")
        self.streak_online_code_entry.textChanged.connect(self._sync_escape_streak_settings)
        online_layout.addWidget(self.streak_online_code_entry)
        online_actions = QHBoxLayout()
        online_actions.addWidget(make_button("Create Code", self._create_streak_lobby))
        online_actions.addWidget(make_button("Join Code", self._join_streak_lobby, secondary=True))
        online_actions.addWidget(make_button("Push Now", self._push_streak_lobby_now, secondary=True))
        online_layout.addLayout(online_actions)
        self.streak_sync_status_label = label("Online sync idle.", "muted")
        online_layout.addWidget(self.streak_sync_status_label)
        left_layout.addWidget(online)

        left_layout.addStretch(1)
        layout.addWidget(left, 2)

        right = card()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(18, 18, 18, 18)
        right_layout.setSpacing(12)
        right_layout.addWidget(label("Team", "sectionTitle"))
        self.player_name_entries = []
        self.player_status_menus = []
        while len(self.config.escape_streak.players) < 4:
            self.config.escape_streak.players.append(EscapeStreakPlayer())
        for idx, player in enumerate(self.config.escape_streak.players[:4]):
            player_card = card("darkCard")
            player_layout = QGridLayout(player_card)
            player_layout.setContentsMargins(12, 10, 12, 10)
            player_layout.addWidget(label(f"Player {idx + 1}", "muted"), 0, 0)
            name_entry = QLineEdit(player.name)
            name_entry.setPlaceholderText(f"Player {idx + 1}")
            name_entry.textChanged.connect(self._sync_escape_streak_settings)
            status = QComboBox()
            status.addItems(["Ready", "Escaped", "Dead", "Disconnected"])
            status.setCurrentText(player.status if player.status in ["Ready", "Escaped", "Dead", "Disconnected"] else "Ready")
            status.currentTextChanged.connect(self._sync_escape_streak_settings)
            player_layout.addWidget(name_entry, 1, 0)
            player_layout.addWidget(status, 1, 1)
            self.player_name_entries.append(name_entry)
            self.player_status_menus.append(status)
            right_layout.addWidget(player_card)
        right_layout.addStretch(1)
        layout.addWidget(right, 3)
        return page

    def _build_detection_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        left = card()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(18, 18, 18, 18)
        self.performance_check = QCheckBox("Performance mode")
        self.performance_check.setChecked(self.config.detection.performance_mode)
        self.performance_check.toggled.connect(self._set_performance_mode)
        left_layout.addWidget(self.performance_check)
        self.template_check = QCheckBox("Fallback template matching")
        self.template_check.setChecked(self.config.detection.fallback_template_matching)
        self.template_check.toggled.connect(self._set_template_mode)
        left_layout.addWidget(self.template_check)
        self._add_slider(left_layout, "OCR confidence", self.config.detection.confidence_threshold, 0.4, 0.98, self._set_confidence)
        left_layout.addStretch(1)
        layout.addWidget(left, 1)

        right = card()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(18, 18, 18, 18)
        right_layout.setSpacing(10)
        right_layout.addWidget(label("OCR Scan Region", "sectionTitle"))
        self.auto_region_check = QCheckBox("Auto position from screen resolution")
        self.auto_region_check.setChecked(self.config.detection.auto_ocr_region)
        self.auto_region_check.toggled.connect(self._toggle_auto_region)
        right_layout.addWidget(self.auto_region_check)
        region = active_ocr_region(self.config)
        grid = QGridLayout()
        self.region_entries = []
        for idx, title in enumerate(("Left", "Top", "Width", "Height")):
            grid.addWidget(label(title, "muted"), 0, idx)
            entry = QLineEdit(str(region[idx]))
            entry.textChanged.connect(self._save_region)
            self.region_entries.append(entry)
            grid.addWidget(entry, 1, idx)
        right_layout.addLayout(grid)
        self._sync_region_entry_state()
        right_layout.addWidget(make_button("Auto Calculate Region", self._auto_calculate_region, secondary=True))
        right_layout.addWidget(make_button("Show OCR Scan Box", self._show_ocr_region))
        right_layout.addWidget(label("Tesseract executable", "muted"))
        tess_row = QHBoxLayout()
        self.tesseract_entry = QLineEdit(self.config.detection.tesseract_cmd)
        self.tesseract_entry.textChanged.connect(self._set_tesseract)
        tess_row.addWidget(self.tesseract_entry, 1)
        tess_row.addWidget(make_button("Find", self._find_tesseract_clicked, secondary=True))
        tess_row.addWidget(make_button("Browse", self._browse_tesseract, secondary=True))
        right_layout.addLayout(tess_row)
        right_layout.addWidget(label("Tesseract search output", "muted"))
        self.tesseract_output = QTextEdit()
        self.tesseract_output.setMinimumHeight(100)
        right_layout.addWidget(self.tesseract_output)
        self._show_tesseract_search_output("Ready. Press Find to search common Tesseract install locations.")
        self.ocr_result = QTextEdit()
        self.ocr_result.setMinimumHeight(120)
        self.ocr_result.setText("Run a live OCR test while the map name is visible in game.")
        right_layout.addWidget(self.ocr_result)
        action_row = QHBoxLayout()
        action_row.addWidget(make_button("Test OCR Now", self._test_ocr, secondary=True))
        self.force_update_button = make_button(self._force_update_button_text(), self.force_update_map)
        action_row.addWidget(self.force_update_button)
        right_layout.addLayout(action_row)
        layout.addWidget(right, 2)
        return page

    def _build_hotkeys_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        frame = card()
        grid = QGridLayout(frame)
        grid.setContentsMargins(18, 18, 18, 18)
        self.hotkey_entries: dict[str, QLineEdit] = {}
        rows = [
            ("toggle_overlay", "Toggle overlay"),
            ("reload_maps", "Reload maps"),
            ("cycle_variant", "Cycle variant"),
            ("force_select", "Force map menu"),
            ("force_update_map", "Force OCR map update"),
        ]
        for row, (key, title) in enumerate(rows):
            grid.addWidget(label(title), row, 0)
            entry = QLineEdit(getattr(self.config.hotkeys, key))
            entry.textChanged.connect(lambda _text, item=key: self._set_hotkey(item))
            self.hotkey_entries[key] = entry
            grid.addWidget(entry, row, 1)
        grid.addWidget(make_button("Apply Hotkeys", self._apply_hotkeys_from_ui), len(rows), 0, 1, 2)
        layout.addWidget(frame)
        layout.addStretch(1)
        return page

    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        details = LicenseStore(self.root_path).load_details()
        frame = card()
        grid = QGridLayout(frame)
        grid.setContentsMargins(18, 18, 18, 18)
        grid.addWidget(label("License", "sectionTitle"), 0, 0, 1, 2)
        self.license_key_entry = QLineEdit(details.get("license_key", ""))
        self.license_key_entry.setReadOnly(True)
        grid.addWidget(label("License key", "muted"), 1, 0, 1, 2)
        grid.addWidget(self.license_key_entry, 2, 0)
        grid.addWidget(make_button("Copy", self._copy_license_key, secondary=True), 2, 1)
        access, expires, remaining = self._license_time_text(details)
        rows = [
            ("Access", access),
            ("Expiration", expires),
            ("Remaining", remaining),
            ("Devices", self._devices_text(details)),
        ]
        for idx, (key, value) in enumerate(rows, start=3):
            grid.addWidget(label(key, "muted"), idx, 0)
            value_label = label(value)
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(value_label, idx, 1)
        layout.addWidget(frame)
        layout.addStretch(1)
        return page

    def _build_logs_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        return page

    def _build_monitor_picker(self, layout: QVBoxLayout) -> None:
        monitors = get_monitors()
        self._monitor_names = [f"{idx + 1}: {m.name} ({m.width}x{m.height})" for idx, m in enumerate(monitors)]
        layout.addWidget(label("Monitor", "sectionTitle"))
        self.monitor_menu = QComboBox()
        self.monitor_menu.addItems(self._monitor_names)
        self.monitor_menu.setCurrentIndex(min(self.config.overlay.monitor_index, len(self._monitor_names) - 1))
        self.monitor_menu.currentIndexChanged.connect(self._set_monitor)
        layout.addWidget(self.monitor_menu)

    def _build_profile_picker(self, layout: QVBoxLayout) -> None:
        layout.addWidget(label("Overlay Profile", "sectionTitle"))
        self.profile_menu = QComboBox()
        self.profile_menu.addItems([profile.name for profile in self.config.profiles])
        self.profile_menu.setCurrentText(self.config.active_profile)
        self.profile_menu.currentTextChanged.connect(self._set_profile)
        layout.addWidget(self.profile_menu)
        layout.addWidget(make_button("New From Current", self._new_profile, secondary=True))

    def _add_slider(self, layout: QVBoxLayout, title: str, value: float, minimum: float, maximum: float, command) -> None:
        row = QVBoxLayout()
        value_label = label(f"{title}: {value:g}")
        row.addWidget(value_label)
        slider = QSlider(Qt.Orientation.Horizontal)
        scale = 100 if isinstance(value, float) or maximum <= 10 else 1
        slider.setRange(int(minimum * scale), int(maximum * scale))
        slider.setValue(int(float(value) * scale))

        def changed(raw: int) -> None:
            actual = round(raw / scale, 2)
            value_label.setText(f"{title}: {actual:g}")
            command(actual)

        slider.valueChanged.connect(changed)
        row.addWidget(slider)
        layout.addLayout(row)

    def _refresh_map_list(self) -> None:
        self.map_list.clear()
        for name in self.library.names():
            item = QListWidgetItem(name)
            self.map_list.addItem(item)
        self._highlight_current_map()

    def _toggle_map_library(self) -> None:
        self.config.map_library_visible = not self.config.map_library_visible
        self._apply_map_library_visibility()
        self._save_later()

    def _apply_map_library_visibility(self) -> None:
        visible = self.config.map_library_visible
        self.sidebar.setVisible(visible)
        if hasattr(self, "sidebar_show_button"):
            self.sidebar_show_button.setVisible(not visible)
        if hasattr(self, "map_library_nav_button"):
            self.map_library_nav_button.setChecked(visible)
        self.map_toggle_button.setText("Hide" if visible else "Show")

    def _toggle_map_settings(self) -> None:
        self._map_settings_visible = not self._map_settings_visible
        self._apply_map_settings_visibility()

    def _apply_map_settings_visibility(self) -> None:
        self.map_settings_frame.setVisible(self._map_settings_visible)
        self.map_settings_button.setText("Hide Map Settings" if self._map_settings_visible else "Show Map Settings")

    def _select_initial_map(self) -> None:
        name = self.config.last_selected_map if self.config.last_selected_map in self.library.entries else ""
        if not name and self.library.names():
            name = self.library.names()[0]
        if name:
            self.select_map(name, "startup")

    def select_map(self, name: str, source: str) -> None:
        entry = self.library.get(name)
        if not entry or not entry.variants:
            return
        if name != self.current_map_name:
            self.current_variant_index = 0
        self.current_map_name = name
        self.current_variant_index = min(self.current_variant_index, len(entry.variants) - 1)
        asset = entry.variants[self.current_variant_index]
        self.config.last_selected_map = name
        self.overlay.set_asset(asset)
        if source == "manual":
            self.overlay.clear_ocr_readout()
        if self.preview:
            self.preview.set_asset(asset)
        if hasattr(self, "current_map_label"):
            self.current_map_label.setText(name)
        self.plugins.emit_map_changed(name)
        self._highlight_current_map()
        self._save_later()
        self.logger.info("Selected %s via %s", name, source)

    def reload_maps(self) -> None:
        self.library.reload()
        self._refresh_map_list()
        if self.current_map_name in self.library.entries:
            self.select_map(self.current_map_name, "reload")
        elif self.library.names():
            self.select_map(self.library.names()[0], "reload")
        else:
            self.overlay.set_asset(None)
            if self.preview:
                self.preview.set_asset(None)
        self.logger.info("Reloaded map library from %s", self.library.maps_path)

    def cycle_variant(self) -> None:
        entry = self.library.get(self.current_map_name)
        if not entry or len(entry.variants) <= 1:
            return
        self.current_variant_index = (self.current_variant_index + 1) % len(entry.variants)
        self.select_map(self.current_map_name, "variant")

    def _register_hotkeys(self) -> None:
        self.hotkeys.register(
            {
                "toggle_overlay": lambda: self.bus.run_on_main.emit(self._toggle_overlay_hotkey),
                "reload_maps": lambda: self.bus.run_on_main.emit(self.reload_maps),
                "cycle_variant": lambda: self.bus.run_on_main.emit(self.cycle_variant),
                "force_select": lambda: self.bus.run_on_main.emit(self.show),
                "force_update_map": lambda: self.bus.run_on_main.emit(self.force_update_map),
            }
        )

    def _detected_from_thread(self, result: DetectionResult) -> None:
        self.bus.detected.emit(result)

    def _handle_detection(self, result: DetectionResult) -> None:
        if result and result.map_name in self.library.entries:
            self.select_map(result.map_name, result.source)

    def _pump_logs_once(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.append(line)
        except Exception:
            pass

    def _update_overlay_status_once(self) -> None:
        self.overlay_status_label.setText(f"Overlay status: {self.overlay.status()}")

    def _highlight_current_map(self) -> None:
        for row in range(self.map_list.count()):
            item = self.map_list.item(row)
            item.setSelected(item.text() == self.current_map_name)

    def _set_position_grid(self, row: int, col: int) -> None:
        self.config.overlay.corner = f"grid_{row}_{col}"
        self.position_picker.update()
        self.overlay.refresh_settings()
        self._save_later()

    def _toggle_enabled(self, checked: bool) -> None:
        self.config.overlay.enabled = checked
        self.overlay.visible = checked
        self.overlay.refresh_settings()
        self._save_later()

    def _toggle_overlay_hotkey(self) -> None:
        self.overlay.toggle()
        self.enabled_check.setChecked(self.config.overlay.enabled)
        self._update_overlay_status_once()
        self._save_later()

    def _show_test_overlay(self) -> None:
        self.overlay.show_for_setup(15)

    def _set_monitor(self, index: int) -> None:
        self.config.overlay.monitor_index = max(0, index)
        self.overlay.refresh_settings()
        self._save_later()

    def _set_opacity(self, value: float) -> None:
        self.config.overlay.opacity = float(value)
        self.overlay.refresh_settings()
        self._save_later()

    def _set_size(self, value: float) -> None:
        self.config.overlay.size = int(value)
        self.overlay.refresh_settings()
        if self.preview:
            self.preview.refresh()
        self._save_later()

    def _set_zoom(self, value: float) -> None:
        self.config.overlay.zoom = float(value)
        self.overlay.refresh_settings()
        if self.preview:
            self.preview.refresh()
        self._save_later()

    def _set_radius(self, value: float) -> None:
        self.config.overlay.corner_radius = int(value)
        self.overlay.refresh_settings()
        if self.preview:
            self.preview.refresh()
        self._save_later()

    def _set_animation_speed(self, value: float) -> None:
        self.config.overlay.animation_speed = float(value)
        self._save_later()

    def _set_rotation(self, checked: bool) -> None:
        self.config.overlay.rotate_with_minimap = checked
        self._save_later()

    def _set_profile(self, value: str) -> None:
        if not value:
            return
        self.config.active_profile = value
        self.enabled_check.setChecked(self.config.overlay.enabled)
        self.rotate_check.setChecked(self.config.overlay.rotate_with_minimap)
        self.position_picker.update()
        self.overlay.refresh_settings()
        if self.preview:
            self.preview.refresh()
        self._save_later()

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New Overlay Profile", "Profile name")
        if not ok or not name:
            return
        existing = {profile.name for profile in self.config.profiles}
        if name in existing:
            self.logger.warning("Profile already exists: %s", name)
            return
        self.config.profiles.append(Profile(name=name, overlay=deepcopy(self.config.overlay)))
        self.config.active_profile = name
        self.profile_menu.blockSignals(True)
        self.profile_menu.clear()
        self.profile_menu.addItems([profile.name for profile in self.config.profiles])
        self.profile_menu.setCurrentText(name)
        self.profile_menu.blockSignals(False)
        self._save_later()

    def _set_escape_streak_enabled(self, checked: bool) -> None:
        self.config.escape_streak.enabled = checked
        self.overlay.refresh_settings()
        self._refresh_escape_streak_preview()
        self._save_later()
        self._push_streak_state_if_connected()

    def _sync_escape_streak_settings(self) -> None:
        if self._streak_applying_remote:
            return
        self.config.escape_streak.enabled = self.streak_enabled_check.isChecked()
        self.config.escape_streak.lobby_code = self.lobby_code_entry.text().strip()
        self.config.escape_streak.streak = int(self.streak_spin.value())
        if hasattr(self, "streak_sync_enabled_check"):
            self.config.escape_streak.sync_enabled = self.streak_sync_enabled_check.isChecked()
        if hasattr(self, "streak_server_entry"):
            self.config.escape_streak.sync_server_url = self.streak_server_entry.text().strip()
        if hasattr(self, "streak_player_name_entry"):
            self.config.escape_streak.sync_player_name = self.streak_player_name_entry.text().strip()
        if hasattr(self, "streak_online_code_entry"):
            self.config.escape_streak.sync_lobby_code = self.streak_online_code_entry.text().strip().upper()
        players = []
        for name_entry, status_menu in zip(self.player_name_entries, self.player_status_menus):
            players.append(EscapeStreakPlayer(name=name_entry.text().strip(), status=status_menu.currentText()))
        self.config.escape_streak.players = (players + [EscapeStreakPlayer() for _ in range(4)])[:4]
        self.overlay.refresh_settings()
        self._refresh_escape_streak_preview()
        self._apply_streak_sync_timer_state()
        self._save_later()
        self._push_streak_state_if_connected()

    def _refresh_escape_streak_preview(self) -> None:
        if not hasattr(self, "preview_streak_frame"):
            return
        streak = self.config.escape_streak
        self.preview_streak_frame.setVisible(streak.enabled)
        if not streak.enabled:
            return
        lobby = streak.lobby_code.strip().upper() or "LOCAL LOBBY"
        players = [f"P{idx + 1}:{player.status[:1].upper()}" for idx, player in enumerate(streak.players[:4])]
        self.preview_streak_title.setText(f"ESCAPE STREAK  {max(0, int(streak.streak))}")
        self.preview_streak_detail.setText(f"{lobby}  |  {'  '.join(players)}")

    def _set_streak_sync_enabled(self, checked: bool) -> None:
        self.config.escape_streak.sync_enabled = checked
        self._sync_escape_streak_settings()
        self._set_streak_sync_status("Online sync enabled." if checked else "Online sync disabled.")

    def _apply_streak_sync_timer_state(self) -> None:
        streak = self.config.escape_streak
        should_poll = bool(streak.sync_enabled and streak.sync_server_url.strip() and streak.sync_lobby_code.strip())
        if should_poll and not self._streak_sync_timer.isActive():
            self._streak_sync_timer.start(2500)
        elif not should_poll:
            self._streak_sync_timer.stop()

    def _set_streak_sync_status(self, message: str) -> None:
        if hasattr(self, "streak_sync_status_label"):
            self.streak_sync_status_label.setText(message)
        self.logger.info("Streak sync: %s", message)

    def _streak_client(self) -> StreakSyncClient:
        return StreakSyncClient(self.config.escape_streak.sync_server_url)

    def _create_streak_lobby(self) -> None:
        self._sync_escape_streak_settings()
        self._set_streak_sync_status("Creating online lobby...")

        def worker() -> None:
            try:
                data = self._streak_client().create_lobby(
                    self.config.escape_streak.sync_player_id,
                    self.config.escape_streak.sync_player_name,
                    self.config.escape_streak,
                )
            except Exception as exc:
                self.bus.run_on_main.emit(lambda error=exc: self._set_streak_sync_status(str(error)))
                return
            self.bus.run_on_main.emit(lambda result=data: self._finish_streak_lobby_join(result, "Created"))

        threading.Thread(target=worker, name="StreakLobbyCreate", daemon=True).start()

    def _join_streak_lobby(self) -> None:
        self._sync_escape_streak_settings()
        self._set_streak_sync_status("Joining online lobby...")

        def worker() -> None:
            try:
                data = self._streak_client().join_lobby(
                    self.config.escape_streak.sync_lobby_code,
                    self.config.escape_streak.sync_player_id,
                    self.config.escape_streak.sync_player_name,
                )
            except Exception as exc:
                self.bus.run_on_main.emit(lambda error=exc: self._set_streak_sync_status(str(error)))
                return
            self.bus.run_on_main.emit(lambda result=data: self._finish_streak_lobby_join(result, "Joined"))

        threading.Thread(target=worker, name="StreakLobbyJoin", daemon=True).start()

    def _finish_streak_lobby_join(self, data: dict, action: str) -> None:
        code = str(data.get("code", "")).upper()
        state = data.get("state", {})
        if code:
            self.config.escape_streak.sync_lobby_code = code
            self.config.escape_streak.lobby_code = code
        self.config.escape_streak.sync_enabled = True
        self._apply_remote_streak_state(state)
        self._refresh_streak_sync_widgets()
        self._apply_streak_sync_timer_state()
        self._save_later()
        self._set_streak_sync_status(f"{action} lobby {self.config.escape_streak.sync_lobby_code}.")

    def _push_streak_lobby_now(self) -> None:
        self._sync_escape_streak_settings()
        self._push_streak_state_if_connected(force=True)

    def _push_streak_state_if_connected(self, force: bool = False) -> None:
        streak = self.config.escape_streak
        if not force and not streak.sync_enabled:
            return
        if not (streak.sync_server_url.strip() and streak.sync_lobby_code.strip()):
            return
        if self._streak_sync_busy:
            return
        self._streak_sync_busy = True

        def worker() -> None:
            try:
                data = self._streak_client().push_state(
                    self.config.escape_streak.sync_lobby_code,
                    self.config.escape_streak.sync_player_id,
                    self.config.escape_streak,
                )
            except Exception as exc:
                self.bus.run_on_main.emit(lambda error=exc: self._finish_streak_sync_error(error))
                return
            self.bus.run_on_main.emit(lambda result=data: self._finish_streak_sync_push(result))

        threading.Thread(target=worker, name="StreakLobbyPush", daemon=True).start()

    def _poll_streak_lobby(self) -> None:
        if self._streak_sync_busy:
            return
        streak = self.config.escape_streak
        if not (streak.sync_enabled and streak.sync_server_url.strip() and streak.sync_lobby_code.strip()):
            self._apply_streak_sync_timer_state()
            return
        self._streak_sync_busy = True

        def worker() -> None:
            try:
                data = self._streak_client().fetch_lobby(self.config.escape_streak.sync_lobby_code)
            except Exception as exc:
                self.bus.run_on_main.emit(lambda error=exc: self._finish_streak_sync_error(error))
                return
            self.bus.run_on_main.emit(lambda result=data: self._finish_streak_sync_poll(result))

        threading.Thread(target=worker, name="StreakLobbyPoll", daemon=True).start()

    def _finish_streak_sync_push(self, data: dict) -> None:
        self._streak_sync_busy = False
        state = data.get("state", {})
        if isinstance(state, dict):
            self.config.escape_streak.sync_revision = int(state.get("sync_revision", self.config.escape_streak.sync_revision) or 0)
        self._set_streak_sync_status(f"Synced lobby {self.config.escape_streak.sync_lobby_code}.")

    def _finish_streak_sync_poll(self, data: dict) -> None:
        self._streak_sync_busy = False
        state = data.get("state", {})
        if isinstance(state, dict):
            remote_revision = int(state.get("sync_revision", 0) or 0)
            if remote_revision >= self.config.escape_streak.sync_revision:
                self._apply_remote_streak_state(state)
                self._refresh_streak_sync_widgets()
                self.overlay.refresh_settings()
                self._refresh_escape_streak_preview()
        self._set_streak_sync_status(f"Connected to {self.config.escape_streak.sync_lobby_code}.")

    def _finish_streak_sync_error(self, error: Exception) -> None:
        self._streak_sync_busy = False
        self._set_streak_sync_status(str(error))

    def _apply_remote_streak_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            return
        self._streak_applying_remote = True
        try:
            streak = self.config.escape_streak
            streak.enabled = bool(state.get("enabled", streak.enabled))
            streak.lobby_code = str(state.get("lobby_code", streak.lobby_code))
            streak.streak = int(state.get("streak", streak.streak) or 0)
            streak.sync_revision = int(state.get("sync_revision", streak.sync_revision) or 0)
            players = []
            for item in state.get("players", [])[:4]:
                if isinstance(item, dict):
                    players.append(
                        EscapeStreakPlayer(
                            name=str(item.get("name", "")),
                            status=str(item.get("status", "Ready")),
                        )
                    )
            streak.players = (players + [EscapeStreakPlayer() for _ in range(4)])[:4]
            self._refresh_streak_widgets()
        finally:
            self._streak_applying_remote = False

    def _refresh_streak_widgets(self) -> None:
        if not hasattr(self, "streak_enabled_check"):
            return
        streak = self.config.escape_streak
        self.streak_enabled_check.blockSignals(True)
        self.streak_enabled_check.setChecked(streak.enabled)
        self.streak_enabled_check.blockSignals(False)
        self.lobby_code_entry.blockSignals(True)
        self.lobby_code_entry.setText(streak.lobby_code)
        self.lobby_code_entry.blockSignals(False)
        self.streak_spin.blockSignals(True)
        self.streak_spin.setValue(max(0, int(streak.streak)))
        self.streak_spin.blockSignals(False)
        for idx, (name_entry, status_menu) in enumerate(zip(self.player_name_entries, self.player_status_menus)):
            player = streak.players[idx] if idx < len(streak.players) else EscapeStreakPlayer()
            name_entry.blockSignals(True)
            name_entry.setText(player.name)
            name_entry.blockSignals(False)
            status_menu.blockSignals(True)
            status_menu.setCurrentText(player.status if player.status in ["Ready", "Escaped", "Dead", "Disconnected"] else "Ready")
            status_menu.blockSignals(False)

    def _refresh_streak_sync_widgets(self) -> None:
        if not hasattr(self, "streak_sync_enabled_check"):
            return
        streak = self.config.escape_streak
        for widget, value in (
            (self.streak_sync_enabled_check, streak.sync_enabled),
            (self.streak_server_entry, streak.sync_server_url),
            (self.streak_player_name_entry, streak.sync_player_name),
            (self.streak_online_code_entry, streak.sync_lobby_code),
        ):
            widget.blockSignals(True)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            else:
                widget.setText(str(value))
            widget.blockSignals(False)

    def _set_performance_mode(self, checked: bool) -> None:
        self.config.detection.performance_mode = checked
        self._apply_performance_timer_state()
        if self.preview:
            self.preview.refresh()
        self.overlay.refresh_settings()
        self._save_later()

    def _apply_performance_timer_state(self) -> None:
        if self.config.detection.performance_mode:
            self._log_timer.stop()
            self._status_timer.stop()
            self.logger.info("Performance mode enabled: background activity disabled")
            self._pump_logs_once()
            self._update_overlay_status_once()
        else:
            self.logger.info("Performance mode disabled: UI polling and startup checks can run")
            self._log_timer.start(1000)
            self._status_timer.start(2000)

    def _set_template_mode(self, checked: bool) -> None:
        self.config.detection.fallback_template_matching = checked
        self._save_later()

    def _set_confidence(self, value: float) -> None:
        self.config.detection.confidence_threshold = float(value)
        self._save_later()

    def _toggle_auto_region(self, checked: bool) -> None:
        self.config.detection.auto_ocr_region = checked
        self._sync_region_entry_state()
        self._set_region_entries(active_ocr_region(self.config))
        self._save_later()

    def _sync_region_entry_state(self) -> None:
        enabled = not self.config.detection.auto_ocr_region
        for entry in self.region_entries:
            entry.setEnabled(enabled)

    def _set_region_entries(self, region: list[int]) -> None:
        for entry, value in zip(self.region_entries, region):
            entry.blockSignals(True)
            entry.setText(str(value))
            entry.blockSignals(False)
        self._sync_region_entry_state()

    def _save_region(self) -> None:
        if self.config.detection.auto_ocr_region:
            return
        try:
            self.config.detection.ocr_region = [max(0, int(entry.text())) for entry in self.region_entries]
            self._save_later()
        except ValueError:
            pass

    def _auto_calculate_region(self) -> None:
        region = compute_auto_ocr_region(self.config)
        self.config.detection.ocr_region = region
        self._set_region_entries(region)
        self._save_later()
        self.logger.info("Auto OCR region set to left=%s top=%s width=%s height=%s", *region)

    def _current_ocr_region(self) -> list[int]:
        if self.config.detection.auto_ocr_region:
            region = active_ocr_region(self.config)
            self.config.detection.ocr_region = region
            self._set_region_entries(region)
            self._save_later()
            return region
        self._save_region()
        return self.config.detection.ocr_region

    def _show_ocr_region(self) -> None:
        self.ocr_region_overlay.show_region(self._current_ocr_region(), seconds=8)

    def _set_tesseract(self) -> None:
        self.config.detection.tesseract_cmd = self.tesseract_entry.text().strip()
        self._save_later()

    def _auto_find_tesseract(self) -> None:
        if is_tesseract_path(self.config.detection.tesseract_cmd):
            self._show_tesseract_search_output(f"Saved Tesseract path is valid:\n{self.config.detection.tesseract_cmd}")
            return
        path, searched = tesseract_search_report()
        if not path:
            self._show_tesseract_search_output("Tesseract not found automatically.", searched)
            self.logger.info("Tesseract was not found automatically")
            return
        self.config.detection.tesseract_cmd = str(path)
        self.tesseract_entry.setText(str(path))
        self._show_tesseract_search_output(f"Found and saved Tesseract:\n{path}", searched)
        self.store.save(self.config)
        self.logger.info("Found Tesseract at %s", path)

    def _find_tesseract_clicked(self) -> None:
        path, searched = tesseract_search_report()
        if not path:
            self._show_tesseract_search_output("Tesseract not found.", searched)
            self.logger.warning("Could not find tesseract.exe automatically. Use Browse to select it manually.")
            return
        self.tesseract_entry.setText(str(path))
        self._set_tesseract()
        self.store.save(self.config)
        self._show_tesseract_search_output(f"Found and saved Tesseract:\n{path}", searched)
        self.logger.info("Tesseract path saved: %s", path)

    def _show_tesseract_search_output(self, message: str, searched=None) -> None:
        if not hasattr(self, "tesseract_output"):
            return
        text = message
        if searched:
            text += "\n\nSearched:\n"
            for path in searched:
                marker = "FOUND" if path.exists() else "missing"
                text += f"- [{marker}] {path}\n"
        self.tesseract_output.setText(text)

    def _browse_tesseract(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select tesseract.exe", "", "Executable (*.exe);;All files (*.*)")
        if path:
            self.tesseract_entry.setText(path)
            self._set_tesseract()
            self._show_tesseract_search_output(f"Manually selected and saved Tesseract:\n{path}")

    def _test_ocr(self) -> None:
        self.ocr_region_overlay.show_region(self._current_ocr_region(), seconds=8)
        self.ocr_result.setText("Testing OCR...")

        def run_test() -> None:
            result = self.detector.test_once()
            self.bus.run_on_main.emit(lambda: self._show_ocr_result(result))

        threading.Thread(target=run_test, daemon=True).start()

    def force_update_map(self) -> None:
        self._current_ocr_region()
        self.logger.info("Force OCR map update requested")
        self.ocr_result.setText("Force updating map from OCR...")

        def run_update() -> None:
            result = self.detector.test_once()
            self.bus.run_on_main.emit(lambda: self._apply_forced_ocr_result(result))

        threading.Thread(target=run_update, daemon=True).start()

    def _apply_forced_ocr_result(self, result: DetectionResult | None) -> None:
        if not result:
            self.ocr_result.setText("Force update found no confident map match.")
            self.logger.info("Force OCR map update found no confident map match")
            return
        if result.map_name not in self.library.entries:
            self.ocr_result.setText(f"Force update matched {result.map_name}, but that map is not loaded.")
            self.logger.warning("Force OCR matched %s, but that map is not loaded", result.map_name)
            return
        self._handle_detection(result)
        self.overlay.set_ocr_readout(result.map_name, result.confidence, self.config.hotkeys.force_update_map)
        self.ocr_result.setText(
            f"Force update applied: {result.map_name}\n"
            f"Confidence: {result.confidence:.0%}\n"
            f"Source: {result.source}\n"
            f"Raw text: {result.raw_text}"
        )
        self.logger.info("Force OCR map update applied: %s (%.0f%%)", result.map_name, result.confidence * 100)

    def _show_ocr_result(self, result: DetectionResult | None) -> None:
        if not result:
            self.ocr_result.setText("No confident map match detected.")
            return
        applied = result.map_name in self.library.entries
        if applied:
            self._handle_detection(result)
            self.overlay.set_ocr_readout(result.map_name, result.confidence, self.config.hotkeys.force_update_map)
        self.ocr_result.setText(
            f"Matched: {result.map_name}\n"
            f"Confidence: {result.confidence:.0%}\n"
            f"Source: {result.source}\n"
            f"Overlay updated: {'yes' if applied else 'no - map is not loaded'}\n"
            f"Raw text: {result.raw_text}"
        )

    def _set_hotkey(self, key: str) -> None:
        setattr(self.config.hotkeys, key, self.hotkey_entries[key].text().strip())
        if key == "force_update_map":
            self._refresh_force_update_labels()
        elif key == "toggle_overlay":
            self._refresh_toggle_overlay_hotkey_label()
        self._save_later()

    def _apply_hotkeys_from_ui(self) -> None:
        for key in self.hotkey_entries:
            self._set_hotkey(key)
        self._register_hotkeys()
        self._refresh_force_update_labels()

    def _force_update_button_text(self) -> str:
        hotkey = self.config.hotkeys.force_update_map.strip()
        return f"Force Update Map ({hotkey.upper()})" if hotkey else "Force Update Map"

    def _refresh_force_update_labels(self) -> None:
        if hasattr(self, "force_update_button"):
            self.force_update_button.setText(self._force_update_button_text())

    def _toggle_overlay_hotkey_text(self) -> str:
        hotkey = self.config.hotkeys.toggle_overlay.strip()
        return f"Toggle Overlay: {hotkey.upper()}" if hotkey else "Toggle Overlay: Not set"

    def _refresh_toggle_overlay_hotkey_label(self) -> None:
        if hasattr(self, "preview_toggle_hotkey_label"):
            self.preview_toggle_hotkey_label.setText(self._toggle_overlay_hotkey_text())

    def _add_map(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add map images", "", "Map images (*.png *.webp *.gif);;All files (*.*)")
        for path in paths:
            source = Path(path)
            if source.suffix.lower() in {".png", ".webp", ".gif"}:
                shutil.copy2(source, self.library.maps_path / source.name)
        if paths:
            self.reload_maps()

    def _open_maps_folder(self) -> None:
        try:
            os.startfile(self.library.maps_path)
        except Exception as exc:
            self.logger.warning("Could not open maps folder: %s", exc)

    def _open_hens_callouts_site(self) -> None:
        try:
            os.startfile(CALLOUTS_URL)
        except Exception as exc:
            self.logger.warning("Could not open Hens333 callouts website: %s", exc)

    def _import_hens_maps(self) -> None:
        self.logger.info("Checking Hens callout map cache")

        def progress(message: str) -> None:
            self.bus.run_on_main.emit(lambda text=message: self.logger.info(text))

        def worker() -> None:
            try:
                summary = import_hens_callouts(self.library.maps_path, self.logger, progress)
            except Exception as exc:
                self.bus.run_on_main.emit(lambda error=exc: self.logger.error("Hens map update failed: %s", error))
                return
            self.bus.run_on_main.emit(lambda result=summary: self._finish_hens_import(result))

        threading.Thread(target=worker, name="HensCalloutsImporter", daemon=True).start()

    def _update_hens_maps_on_startup(self) -> None:
        if not self.config.updates.auto_update_hens_maps:
            self.logger.info("Automatic Hens map startup update is disabled")
            return
        self.logger.info("Hands-free startup: checking Hens map cache")
        threading.Thread(target=self._startup_hens_worker, name="HensCalloutsStartupUpdate", daemon=True).start()

    def _startup_hens_worker(self) -> None:
        try:
            summary = import_hens_callouts(self.library.maps_path, self.logger)
        except Exception as exc:
            self.bus.run_on_main.emit(lambda error=exc: self.logger.warning("Hens startup cache update skipped: %s", error))
            return
        if summary.downloaded:
            self.bus.run_on_main.emit(lambda result=summary: self._finish_hens_import(result))
        else:
            self.bus.run_on_main.emit(lambda result=summary: self._finish_hens_startup_check(result))

    def _finish_hens_startup_check(self, summary) -> None:
        self.logger.info("Hens maps cache is current: %s cached, %s total", summary.skipped, summary.total)
        if not self.current_map_name:
            self.library.reload()
            self._refresh_map_list()
            self._select_initial_map()

    def _finish_hens_import(self, summary) -> None:
        self.logger.info(
            "Hens map cache update complete: %s downloaded, %s cached, %s total",
            summary.downloaded,
            summary.skipped,
            summary.total,
        )
        self.reload_maps()

    def _check_for_app_updates(self) -> None:
        self.app_update_button.setEnabled(False)
        self.app_update_button.setText("Checking...")
        self.app_update_status_label.setText("Checking GitHub...")

        def worker() -> None:
            try:
                status = check_for_app_update(self.root_path, __version__)
            except Exception as exc:
                self.bus.run_on_main.emit(lambda error=exc: self._show_app_update_error(error))
                return
            self.bus.run_on_main.emit(lambda result=status: self._show_app_update_status(result))

        threading.Thread(target=worker, name="AppUpdateStatusCheck", daemon=True).start()

    def _show_app_update_status(self, status: AppUpdateStatus) -> None:
        self.app_update_button.setEnabled(True)
        self.app_update_button.setText("Check for Updates")
        if status.update_available:
            self.app_update_status_label.setText(f"Update available: {status.latest_version}")
            self._show_app_update_dialog(status)
            return
        self.app_update_status_label.setText(f"Up to date: {status.current_version}")

    def _show_app_update_error(self, error: Exception) -> None:
        self.app_update_button.setEnabled(True)
        self.app_update_button.setText("Check for Updates")
        self.app_update_status_label.setText("Could not check for updates")
        self.logger.warning("Could not check for app updates: %s", error)

    def _show_app_update_dialog(self, status: AppUpdateStatus) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Update Available - {status.latest_version}")
        dialog.setMinimumSize(620, 470)
        dialog.setStyleSheet(APP_STYLESHEET)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(f"{status.latest_version} is available", "sectionTitle"))
        layout.addWidget(label(f"You are currently running {status.current_version}. Review the changes before updating.", "muted"))
        changelog = QTextEdit(status.changelog)
        changelog.setReadOnly(True)
        layout.addWidget(changelog, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Not Now", dialog.reject, secondary=True))
        buttons.addWidget(make_button("Update", lambda: self._install_app_update(status, dialog)))
        layout.addLayout(buttons)
        dialog.exec()

    def _install_app_update(self, status: AppUpdateStatus, dialog: QDialog) -> None:
        dialog.accept()
        self.app_update_button.setEnabled(False)
        self.app_update_button.setText("Downloading...")
        self.app_update_status_label.setText(f"Downloading {status.latest_version}...")

        def worker() -> None:
            try:
                stage_app_update(self.root_path, status, os.getpid())
            except Exception as exc:
                self.bus.run_on_main.emit(lambda error=exc: self._show_app_update_error(error))
                return
            self.bus.run_on_main.emit(lambda: self._finish_app_update_install(status))

        threading.Thread(target=worker, name="AppUpdateDownload", daemon=True).start()

    def _finish_app_update_install(self, status: AppUpdateStatus) -> None:
        self.app_update_status_label.setText(f"Installing {status.latest_version}...")
        QMessageBox.information(
            self,
            "Update Ready",
            f"{status.latest_version} has been downloaded.\n\nThe app will close to finish installing the update. Reopen it after a few seconds.",
        )
        QTimer.singleShot(300, self.close)

    def _watch_game_lifetime(self) -> None:
        try:
            if is_dead_by_daylight_running():
                self._game_absent_checks = 0
            else:
                self._game_absent_checks += 1
                if self._game_absent_checks >= 1:
                    self.logger.info("Dead by Daylight is no longer running. Closing overlay app.")
                    self.close()
                    return
        except Exception as exc:
            self.logger.warning("Could not check Dead by Daylight lifetime: %s", exc)

    def _save_later(self) -> None:
        self._save_timer.start(0)

    def _save_now(self) -> None:
        try:
            self.config.overlay.border_width = 0
            self.store.save(self.config)
        except Exception as exc:
            self.logger.error("Could not save settings to %s: %s", self.store.path, exc)

    def _sync_text_settings_to_config(self) -> None:
        if hasattr(self, "tesseract_entry"):
            self.config.detection.tesseract_cmd = self.tesseract_entry.text().strip()
        if hasattr(self, "hotkey_entries"):
            for key, entry in self.hotkey_entries.items():
                setattr(self.config.hotkeys, key, entry.text().strip())
        if hasattr(self, "region_entries") and not self.config.detection.auto_ocr_region:
            try:
                self.config.detection.ocr_region = [max(0, int(entry.text())) for entry in self.region_entries]
            except ValueError:
                self.logger.warning("OCR region must contain whole numbers")
        if hasattr(self, "lobby_code_entry"):
            self._sync_escape_streak_settings()

    def _copy_license_key(self) -> None:
        key = LicenseStore(self.root_path).load_key()
        if key:
            QApplication.clipboard().setText(key)

    @staticmethod
    def _devices_text(details: dict) -> str:
        max_devices = details.get("max_devices", 0)
        used_devices = details.get("used_devices", 0)
        return f"{used_devices} out of {max_devices} used" if max_devices else "Unavailable"

    @staticmethod
    def _license_time_text(details: dict) -> tuple[str, str, str]:
        plan = str(details.get("plan", "")).replace("_", " ").title() or "Unavailable"
        expires_at = details.get("expires_at")
        if not expires_at:
            return plan, "Never", "Lifetime access"
        try:
            expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            remaining = expires - datetime.now(timezone.utc)
        except ValueError:
            return plan, str(expires_at), "Unavailable"
        if remaining.total_seconds() <= 0:
            remaining_text = "Expired"
        elif remaining.days:
            remaining_text = f"{remaining.days} day{'s' if remaining.days != 1 else ''}"
        else:
            hours = max(1, int(remaining.total_seconds() // 3600))
            remaining_text = f"{hours} hour{'s' if hours != 1 else ''}"
        return plan, expires.astimezone().strftime("%Y-%m-%d %H:%M"), remaining_text
