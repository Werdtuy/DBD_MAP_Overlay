from __future__ import annotations

import logging
from pathlib import Path
import sys
import tkinter as tk

from PIL import Image, ImageTk

from .config import AppConfig
from .focus import FocusGate, get_monitors
from .maps import MapAsset
from .rendering import AnimatedImage, render_frame


TRANSPARENT_COLOR = "#010203"
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020


class OverlayWindow:
    def __init__(self, root: tk.Tk, config: AppConfig, focus_gate: FocusGate, logger: logging.Logger) -> None:
        self.root = root
        self.config = config
        self.focus_gate = focus_gate
        self.logger = logger
        self.visible = config.overlay.enabled
        self.asset: MapAsset | None = None
        self.animator: AnimatedImage | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.readout_var = tk.StringVar(value="")
        self.transition_alpha = config.overlay.opacity
        self._tick_id: str | None = None
        self._visibility_id: str | None = None
        self._fade_id: str | None = None
        self._readout_clear_id: str | None = None
        self._last_hidden_reason = ""

        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=TRANSPARENT_COLOR, highlightthickness=0)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", config.overlay.opacity)
        try:
            self.window.attributes("-transparentcolor", TRANSPARENT_COLOR)
        except tk.TclError:
            pass
        self.window.after_idle(self._make_click_through)

        self.label = tk.Label(self.window, bg=TRANSPARENT_COLOR, borderwidth=0, highlightthickness=0)
        self.label.pack(fill="both", expand=True)
        self.readout = tk.Label(
            self.window,
            textvariable=self.readout_var,
            bg=TRANSPARENT_COLOR,
            fg="#FFFFFF",
            borderwidth=0,
            highlightthickness=0,
            font=("Segoe UI", 10, "bold"),
            justify="center",
            wraplength=max(120, int(config.overlay.size) - 10),
        )
        self.readout.pack(fill="x", pady=(2, 0))

    def start(self) -> None:
        self._apply_visibility()
        if not self.config.detection.performance_mode:
            self._schedule_visibility()

    def _make_click_through(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes

            self.window.update_idletasks()
            user32 = ctypes.windll.user32
            get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            get_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
            get_window_long.restype = ctypes.c_ssize_t
            set_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            set_window_long.restype = ctypes.c_ssize_t
            user32.GetParent.argtypes = [ctypes.c_void_p]
            user32.GetParent.restype = ctypes.c_void_p
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            user32.SetWindowPos.restype = ctypes.c_bool

            hwnd = ctypes.c_void_p(self.window.winfo_id())
            wrapper_hwnd = user32.GetParent(hwnd) or hwnd.value
            style = get_window_long(wrapper_hwnd, GWL_EXSTYLE)
            set_window_long(wrapper_hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT)
            user32.SetWindowPos(
                wrapper_hwnd,
                None,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
        except Exception as exc:
            self.logger.warning("Could not make overlay click-through: %s", exc)

    def stop(self) -> None:
        if self._tick_id:
            self.root.after_cancel(self._tick_id)
        if self._visibility_id:
            self.root.after_cancel(self._visibility_id)
        if self._fade_id:
            self.root.after_cancel(self._fade_id)
        if self._readout_clear_id:
            self.root.after_cancel(self._readout_clear_id)
        self.window.destroy()

    def set_asset(self, asset: MapAsset | None) -> None:
        self.asset = asset
        self.animator = None
        if asset:
            try:
                self.animator = AnimatedImage(asset.path)
                self.logger.info("Overlay map set to %s", asset.name)
                self._start_fade()
            except Exception as exc:
                self.logger.error("Could not load map image %s: %s", asset.path, exc)
        self._render_next_frame()
        self._apply_visibility()

    def toggle(self) -> None:
        self.visible = not self.visible
        self.config.overlay.enabled = self.visible
        self._apply_visibility()

    def show_for_setup(self, seconds: int = 15) -> None:
        if not self.asset:
            self.logger.warning("Cannot show test overlay: no map is selected")
            return
        self.visible = True
        self.config.overlay.enabled = True
        self.logger.info("Showing overlay")
        self._apply_visibility()

    def status(self) -> str:
        if self._last_hidden_reason:
            return f"Hidden: {self._last_hidden_reason}"
        return "Visible"

    def refresh_settings(self) -> None:
        size = int(self.config.overlay.size)
        self.window.geometry(f"{size}x{self._window_height()}+{self._position()[0]}+{self._position()[1]}")
        self.readout.configure(wraplength=max(120, size - 10))
        self.window.attributes("-alpha", self.config.overlay.opacity)
        self._render_next_frame()
        self._apply_visibility()

    def set_ocr_readout(self, map_name: str, confidence: float, hotkey: str = "") -> None:
        if self._readout_clear_id:
            self.root.after_cancel(self._readout_clear_id)
            self._readout_clear_id = None
        hotkey_text = f" [{hotkey.upper()}]" if hotkey else ""
        self.readout_var.set(f"Map Detected: {map_name}{hotkey_text}\nAccuracy: {confidence:.0%}")
        self._readout_clear_id = self.root.after(2000, self.clear_ocr_readout)

    def clear_ocr_readout(self) -> None:
        self._readout_clear_id = None
        self.readout_var.set("")

    def _window_height(self) -> int:
        return int(self.config.overlay.size) + 48

    def _start_fade(self) -> None:
        if self._fade_id:
            self.root.after_cancel(self._fade_id)
            self._fade_id = None
        if self.config.detection.performance_mode:
            self.transition_alpha = self.config.overlay.opacity
            self.window.attributes("-alpha", self.config.overlay.opacity)
            return
        self.transition_alpha = 0.0
        self.window.attributes("-alpha", 0.0)
        self._fade_step()

    def _fade_step(self) -> None:
        target = self.config.overlay.opacity
        duration = max(0, self.config.overlay.transition_ms)
        if duration == 0:
            self.window.attributes("-alpha", target)
            return
        increment = target / max(1, duration // 16)
        self.transition_alpha = min(target, self.transition_alpha + increment)
        self.window.attributes("-alpha", self.transition_alpha)
        if self.transition_alpha < target:
            self._fade_id = self.root.after(16, self._fade_step)

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
        legacy_positions = {
            "top_center": (0, 1),
            "middle_left": (1, 0),
            "middle_right": (1, 3),
            "bottom_center": (3, 1),
            "top_left": (0, 0),
            "top_right": (0, 3),
            "bottom_left": (3, 0),
            "bottom_right": (3, 3),
        }
        row_col = legacy_positions.get(overlay.corner)
        if row_col is None and overlay.corner.startswith("grid_"):
            parts = overlay.corner.split("_")
            if len(parts) == 3:
                try:
                    row_col = (int(parts[1]), int(parts[2]))
                except ValueError:
                    row_col = None
        row, col = row_col if row_col else (1, 3)
        row = min(max(row, 0), 3)
        col = min(max(col, 0), 3)
        if row not in {0, 3} and col not in {0, 3}:
            edge_points = [
                (edge_row, edge_col)
                for edge_row in range(4)
                for edge_col in range(4)
                if edge_row in {0, 3} or edge_col in {0, 3}
            ]
            row, col = min(edge_points, key=lambda point: (point[0] - row) ** 2 + (point[1] - col) ** 2)
        return x_points[col], y_points[row]

    def _schedule_visibility(self) -> None:
        self._apply_visibility()
        self._visibility_id = self.root.after(250, self._schedule_visibility)

    def _apply_visibility(self) -> None:
        should_show = self.visible and self.config.overlay.enabled and self.asset is not None
        if should_show:
            x, y = self._position()
            size = int(self.config.overlay.size)
            self.window.geometry(f"{size}x{self._window_height()}+{x}+{y}")
            self.window.deiconify()
            self.window.lift()
            self.window.attributes("-topmost", True)
            self._make_click_through()
            self._last_hidden_reason = ""
        else:
            self._last_hidden_reason = self._hidden_reason()
            self.window.withdraw()

    def _hidden_reason(self) -> str:
        if not self.visible or not self.config.overlay.enabled:
            return "overlay disabled"
        if self.asset is None:
            return "no map selected"
        return "unknown"

    def _render_next_frame(self) -> None:
        if self._tick_id:
            self.root.after_cancel(self._tick_id)
            self._tick_id = None
        if not self.animator:
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
        self.photo = ImageTk.PhotoImage(rendered)
        self.label.configure(image=self.photo)
        if self.config.detection.performance_mode and not self._allow_performance_animation():
            return
        speed = max(0.1, overlay.animation_speed)
        self._tick_id = self.root.after(max(20, int(duration / speed)), self._render_next_frame)

    def _allow_performance_animation(self) -> bool:
        return bool(self.asset and self.asset.path.suffix.lower() == ".gif")


class PreviewRenderer:
    def __init__(self, label: tk.Label, config: AppConfig) -> None:
        self.label = label
        self.config = config
        self.asset: MapAsset | None = None
        self.animator: AnimatedImage | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self._tick_id: str | None = None

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
        if self._tick_id:
            self.label.after_cancel(self._tick_id)
            self._tick_id = None
        if not self.animator:
            self.label.configure(image="", text="No map selected")
            return
        frame, duration = self.animator.next_frame()
        overlay = self.config.overlay
        rendered = render_frame(
            frame,
            min(280, overlay.size),
            overlay.zoom,
            overlay.border_width,
            overlay.border_color,
            overlay.corner_radius,
            0,
        )
        self.photo = ImageTk.PhotoImage(rendered)
        self.label.configure(image=self.photo, text="")
        if self.config.detection.performance_mode:
            return
        self._tick_id = self.label.after(max(20, int(duration / max(0.1, overlay.animation_speed))), self._render)


class OcrRegionWindow:
    def __init__(self, root: tk.Tk, logger: logging.Logger) -> None:
        self.root = root
        self.logger = logger
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=TRANSPARENT_COLOR, highlightthickness=0)
        self.window.attributes("-topmost", True)
        try:
            self.window.attributes("-transparentcolor", TRANSPARENT_COLOR)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self.window, bg=TRANSPARENT_COLOR, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._hide_id: str | None = None

    def show(self, region: list[int], seconds: int = 8) -> None:
        if self._hide_id:
            self.root.after_cancel(self._hide_id)
            self._hide_id = None
        left, top, width, height = [max(0, int(value)) for value in region]
        if width <= 0 or height <= 0:
            self.logger.warning("Cannot show OCR region: width and height must be greater than zero")
            return

        self.window.geometry(f"{width}x{height}+{left}+{top}")
        self.canvas.configure(width=width, height=height)
        self.canvas.delete("all")
        inset = 3
        self.canvas.create_rectangle(
            inset,
            inset,
            max(inset, width - inset - 1),
            max(inset, height - inset - 1),
            outline="#00E5FF",
            width=4,
        )
        self.canvas.create_rectangle(
            inset + 5,
            inset + 5,
            max(inset + 5, width - inset - 6),
            max(inset + 5, height - inset - 6),
            outline="#111827",
            width=1,
        )
        self.window.deiconify()
        self.window.lift()
        self.window.attributes("-topmost", True)
        self._hide_id = self.root.after(max(1, seconds) * 1000, self.hide)
        self.logger.info("Showing OCR scan region at left=%s top=%s width=%s height=%s", left, top, width, height)

    def hide(self) -> None:
        self._hide_id = None
        self.window.withdraw()

    def stop(self) -> None:
        if self._hide_id:
            self.root.after_cancel(self._hide_id)
            self._hide_id = None
        self.window.destroy()
