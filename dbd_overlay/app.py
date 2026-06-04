from __future__ import annotations

import logging
import os
from pathlib import Path
from queue import Queue
from copy import deepcopy
from datetime import datetime, timezone
import shutil
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk

from . import __version__
from .app_logging import configure_logging
from .auto_launch import ensure_auto_launcher, start_watcher_if_needed
from .config import AppConfig, ConfigStore, Profile
from .detector import DetectionResult, DetectionWorker
from .focus import FocusGate, get_monitors
from .hotkeys import HotkeyManager
from .hens_callouts import CALLOUTS_URL, import_hens_callouts
from .license_gate import LicenseStore
from .maps import MapEntry, MapLibrary
from .ocr_region import active_ocr_region, compute_auto_ocr_region
from .overlay import OcrRegionWindow, OverlayWindow, PreviewRenderer
from .plugins import PluginManager
from .tesseract import is_tesseract_path, tesseract_search_report
from .update_status import AppUpdateStatus, check_for_app_update, stage_app_update
from .updates import MapUpdateChecker


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


COLORS = {
    "bg": "#08090B",
    "sidebar": "#111316",
    "surface": "#181A1F",
    "panel": "#202329",
    "panel_dark": "#12151A",
    "input": "#242832",
    "input_hover": "#303642",
    "accent": "#B81F2D",
    "accent_hover": "#D53641",
    "accent_dark": "#74131B",
    "text": "#EFE7DA",
    "muted": "#A99F91",
    "border": "#3B3030",
}


class OverlayApp:
    def __init__(self, root_path: Path, start_minimized: bool = False) -> None:
        self.root_path = root_path
        self.start_minimized = start_minimized
        self.store = ConfigStore(root_path)
        self.config = self.store.load()
        self.config.map_library_visible = False
        self.logger, self.log_queue = configure_logging(root_path)
        self.logger.info("Settings imported automatically from %s", self.store.path)
        ensure_auto_launcher(root_path, self.logger)
        start_watcher_if_needed(root_path, self.logger)
        self._save_after: str | None = None

        self.library = MapLibrary(root_path, self.config.maps_dir)
        self.library.reload()
        self.focus_gate = FocusGate(self.config, self.logger)
        self.plugins = PluginManager(root_path)
        self.plugins.load()
        self.update_checker = MapUpdateChecker(self.config, self.library, self.logger)

        self.root = ctk.CTk()
        self.root.title("DBD Companion Overlay")
        self.root.geometry("1120x760")
        self.root.minsize(980, 660)
        self.root.configure(fg_color=COLORS["bg"])
        self._set_app_icon()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.config.overlay.border_width = 0
        self.overlay = OverlayWindow(self.root, self.config, self.focus_gate, self.logger)
        self.ocr_region_overlay = OcrRegionWindow(self.root, self.logger)
        self.preview: PreviewRenderer | None = None
        self.hotkeys = HotkeyManager(self.config, self.focus_gate, self.logger)
        self.detector = DetectionWorker(self.config, self.library, self.focus_gate, self.logger, self._detected_from_thread)

        self.current_map_name = ""
        self.current_variant_index = 0
        self._log_after: str | None = None
        self._status_after: str | None = None
        self._monitor_names: list[str] = []
        self._map_buttons: dict[str, ctk.CTkButton] = {}
        self.sidebar: ctk.CTkFrame | None = None
        self.map_header_label: ctk.CTkLabel | None = None
        self.map_toggle_button: ctk.CTkButton | None = None
        self.sidebar_show_button: ctk.CTkButton | None = None
        self.map_actions: ctk.CTkFrame | None = None
        self.map_settings_frame: ctk.CTkFrame | None = None
        self.map_settings_button: ctk.CTkButton | None = None
        self.map_settings_visible = False
        self.app_update_dialog: ctk.CTkToplevel | None = None

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
        if not self.config.detection.performance_mode:
            self._pump_logs()
            self._update_overlay_status()
        if self.start_minimized:
            self.root.after(300, self._minimize_gui)
        self.logger.info("App ready. Loaded %s map(s).", len(self.library.entries))

    def run(self) -> None:
        self.root.mainloop()

    def _resource_path(self, *parts: str) -> Path:
        if getattr(sys, "frozen", False):
            return Path(getattr(sys, "_MEIPASS", self.root_path)).joinpath(*parts)
        return self.root_path.joinpath(*parts)

    def _set_app_icon(self) -> None:
        ico_path = self._resource_path("assets", "app_icon.ico")
        png_path = self._resource_path("assets", "app_icon.png")
        try:
            if ico_path.exists():
                self.root.iconbitmap(str(ico_path))
            if png_path.exists():
                icon = ImageTk.PhotoImage(Image.open(png_path))
                self.root.iconphoto(True, icon)
                self._app_icon_photo = icon
        except Exception as exc:
            self.logger.warning("Could not set app icon: %s", exc)

    def _minimize_gui(self) -> None:
        try:
            self.root.iconify()
            self.logger.info("Settings window opened minimized.")
        except Exception as exc:
            self.logger.warning("Could not minimize settings window: %s", exc)

    def _button_style(self, secondary: bool = False) -> dict:
        if secondary:
            return {
                "fg_color": COLORS["input"],
                "hover_color": COLORS["input_hover"],
                "text_color": COLORS["text"],
                "border_width": 1,
                "border_color": COLORS["border"],
            }
        return {
            "fg_color": COLORS["accent"],
            "hover_color": COLORS["accent_hover"],
            "text_color": COLORS["text"],
        }

    def _switch_style(self) -> dict:
        return {
            "progress_color": COLORS["accent"],
            "button_color": COLORS["text"],
            "button_hover_color": "#FFFFFF",
            "text_color": COLORS["text"],
        }

    def _option_style(self) -> dict:
        return {
            "fg_color": COLORS["input"],
            "button_color": COLORS["accent_dark"],
            "button_hover_color": COLORS["accent"],
            "dropdown_fg_color": COLORS["panel_dark"],
            "dropdown_hover_color": COLORS["accent_dark"],
            "text_color": COLORS["text"],
            "dropdown_text_color": COLORS["text"],
        }

    def close(self) -> None:
        self._sync_text_settings_to_config()
        if self._save_after:
            self.root.after_cancel(self._save_after)
            self._save_after = None
        self._save_now()
        self.hotkeys.unregister()
        if self._log_after:
            self.root.after_cancel(self._log_after)
        if self._status_after:
            self.root.after_cancel(self._status_after)
        self.ocr_region_overlay.stop()
        self.overlay.stop()
        self.root.destroy()

    def _build_ui(self) -> None:
        self.root.grid_columnconfigure(0, weight=0)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        app_header = ctk.CTkFrame(self.root, height=52, corner_radius=0, fg_color=COLORS["sidebar"])
        app_header.grid(row=0, column=0, columnspan=2, sticky="ew")
        app_header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            app_header,
            text=f"DBD Companion Overlay  |  {__version__}",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, padx=(18, 12), pady=12, sticky="w")
        self.app_update_status_label = ctk.CTkLabel(app_header, text="", text_color=COLORS["muted"])
        self.app_update_status_label.grid(row=0, column=1, padx=12, pady=12, sticky="e")
        self.app_update_button = ctk.CTkButton(
            app_header,
            text="Check for Updates",
            width=138,
            command=self._check_for_app_updates,
            **self._button_style(secondary=True),
        )
        self.app_update_button.grid(row=0, column=2, padx=(0, 18), pady=10, sticky="e")

        self.sidebar_show_button = ctk.CTkButton(
            self.root,
            text="Maps",
            width=62,
            command=self._toggle_map_library,
            **self._button_style(secondary=True),
        )

        self.sidebar = ctk.CTkFrame(self.root, width=270, corner_radius=0, fg_color=COLORS["sidebar"])
        self.sidebar.grid(row=1, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self.sidebar,
            text="DBD Overlay",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COLORS["text"],
        ).grid(
            row=0, column=0, padx=18, pady=(22, 4), sticky="w"
        )
        header = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        header.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        self.map_header_label = ctk.CTkLabel(header, text="Map Library", text_color=COLORS["muted"])
        self.map_header_label.grid(row=0, column=0, padx=6, sticky="w")
        self.map_toggle_button = ctk.CTkButton(
            header, text="Hide", width=64, command=self._toggle_map_library, **self._button_style(secondary=True)
        )
        self.map_toggle_button.grid(row=0, column=1, sticky="e")

        self.map_list = ctk.CTkScrollableFrame(self.sidebar, fg_color="transparent", scrollbar_button_color=COLORS["input_hover"])
        self.map_list.grid(row=2, column=0, padx=12, pady=0, sticky="nsew")

        self.map_actions = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.map_actions.grid(row=3, column=0, padx=12, pady=16, sticky="ew")
        self.map_actions.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(self.map_actions, text="Add", command=self._add_map, **self._button_style()).grid(row=0, column=0, padx=(0, 6), sticky="ew")
        ctk.CTkButton(self.map_actions, text="Reload", command=self.reload_maps, **self._button_style()).grid(row=0, column=1, padx=(6, 0), sticky="ew")
        ctk.CTkButton(self.map_actions, text="Open Folder", command=self._open_maps_folder, **self._button_style(secondary=True)).grid(row=1, column=0, columnspan=2, pady=(10, 0), sticky="ew")
        ctk.CTkButton(self.map_actions, text="Update Hens Maps", command=self._import_hens_maps, **self._button_style()).grid(
            row=2, column=0, columnspan=2, pady=(10, 0), sticky="ew"
        )
        ctk.CTkLabel(
            self.map_actions,
            text="Callout maps: Hens333 website\nImages credited to Lethia",
            justify="left",
            text_color=COLORS["muted"],
        ).grid(row=3, column=0, columnspan=2, pady=(14, 4), sticky="w")
        ctk.CTkButton(
            self.map_actions,
            text="Open Hens333 Callouts",
            command=self._open_hens_callouts_site,
            **self._button_style(secondary=True),
        ).grid(row=4, column=0, columnspan=2, sticky="ew")

        self.tabs = ctk.CTkTabview(
            self.root,
            fg_color=COLORS["surface"],
            segmented_button_fg_color=COLORS["panel_dark"],
            segmented_button_selected_color=COLORS["accent_dark"],
            segmented_button_selected_hover_color=COLORS["accent"],
            segmented_button_unselected_color=COLORS["panel_dark"],
            segmented_button_unselected_hover_color=COLORS["input_hover"],
        )
        self.tabs.grid(row=1, column=1, padx=18, pady=18, sticky="nsew")
        for name in ("Overlay", "Detection", "Hotkeys", "Settings", "Logs"):
            self.tabs.add(name)

        self._build_overlay_tab(self.tabs.tab("Overlay"))
        self._build_detection_tab(self.tabs.tab("Detection"))
        self._build_hotkeys_tab(self.tabs.tab("Hotkeys"))
        self._build_settings_tab(self.tabs.tab("Settings"))
        self._build_logs_tab(self.tabs.tab("Logs"))
        self._refresh_map_list()
        self._apply_map_library_visibility()

    def _build_overlay_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        preview_card = ctk.CTkFrame(parent, fg_color=COLORS["panel"], border_width=1, border_color=COLORS["border"])
        preview_card.grid(row=0, column=0, rowspan=2, padx=(0, 12), pady=12, sticky="nsew")
        preview_card.grid_columnconfigure(0, weight=1)
        preview_card.grid_rowconfigure(1, weight=1)
        preview_header = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_header.grid(row=0, column=0, padx=18, pady=(18, 8), sticky="ew")
        preview_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            preview_header,
            text="Live Preview",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text"],
        ).grid(
            row=0, column=0, sticky="w"
        )
        self.preview_toggle_hotkey_label = ctk.CTkLabel(
            preview_header,
            text=self._toggle_overlay_hotkey_text(),
            text_color=COLORS["muted"],
        )
        self.preview_toggle_hotkey_label.grid(row=0, column=1, padx=(12, 0), sticky="e")
        self.preview_label = tk.Label(preview_card, text="No map selected", bg=COLORS["panel_dark"], fg=COLORS["muted"], bd=0)
        self.preview_label.grid(row=1, column=0, padx=18, pady=(0, 18), sticky="nsew")
        self.preview = PreviewRenderer(self.preview_label, self.config)

        controls = ctk.CTkScrollableFrame(parent, fg_color=COLORS["panel"], scrollbar_button_color=COLORS["input_hover"])
        controls.grid(row=0, column=1, rowspan=2, padx=(12, 0), pady=12, sticky="nsew")
        controls.grid_columnconfigure(0, weight=1)

        self.enabled_var = tk.BooleanVar(value=self.config.overlay.enabled)
        ctk.CTkSwitch(controls, text="Overlay enabled", variable=self.enabled_var, command=self._toggle_enabled, **self._switch_style()).grid(
            row=0, column=0, padx=14, pady=(16, 12), sticky="w"
        )
        ctk.CTkButton(controls, text="Show Test Overlay", command=self._show_test_overlay, **self._button_style()).grid(
            row=1, column=0, padx=14, pady=(0, 10), sticky="ew"
        )
        self.overlay_status_label = ctk.CTkLabel(controls, text="Overlay status: starting", text_color=COLORS["muted"])
        self.overlay_status_label.grid(row=2, column=0, padx=14, pady=(0, 8), sticky="w")

        self._build_position_picker(controls, 3)
        self.map_settings_button = ctk.CTkButton(
            controls,
            text="Show Map Settings",
            command=self._toggle_map_settings,
            **self._button_style(secondary=True),
        )
        self.map_settings_button.grid(row=4, column=0, padx=14, pady=(2, 12), sticky="ew")

        self.map_settings_frame = ctk.CTkFrame(controls, fg_color="transparent")
        self.map_settings_frame.grid_columnconfigure(0, weight=1)
        self._build_monitor_picker(self.map_settings_frame, 0)
        self._slider(self.map_settings_frame, 2, "Opacity", self.config.overlay.opacity, 0.2, 1.0, self._set_opacity)
        self._slider(self.map_settings_frame, 3, "Size", self.config.overlay.size, 120, 720, self._set_size)
        self._slider(self.map_settings_frame, 4, "Zoom", self.config.overlay.zoom, 0.4, 2.4, self._set_zoom)
        self._slider(self.map_settings_frame, 5, "Corner radius", self.config.overlay.corner_radius, 0, 80, self._set_radius)
        self._slider(self.map_settings_frame, 6, "Animation speed", self.config.overlay.animation_speed, 0.25, 3.0, self._set_animation_speed)
        self.rotate_var = tk.BooleanVar(value=self.config.overlay.rotate_with_minimap)
        ctk.CTkSwitch(self.map_settings_frame, text="Minimap rotation ready", variable=self.rotate_var, command=self._set_rotation, **self._switch_style()).grid(
            row=7, column=0, padx=14, pady=16, sticky="w"
        )
        self._build_profile_picker(self.map_settings_frame, 8)
        self._apply_map_settings_visibility()

    def _build_profile_picker(self, parent: ctk.CTkFrame, row: int) -> None:
        box = ctk.CTkFrame(parent, fg_color=COLORS["panel_dark"], border_width=1, border_color=COLORS["border"])
        box.grid(row=row, column=0, padx=14, pady=10, sticky="ew")
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text="Overlay Profile", font=ctk.CTkFont(weight="bold"), text_color=COLORS["text"]).grid(row=0, column=0, padx=12, pady=(12, 6), sticky="w")
        self.profile_menu = ctk.CTkOptionMenu(box, values=[profile.name for profile in self.config.profiles], command=self._set_profile, **self._option_style())
        self.profile_menu.set(self.config.active_profile)
        self.profile_menu.grid(row=1, column=0, padx=12, pady=(0, 8), sticky="ew")
        ctk.CTkButton(box, text="New From Current", command=self._new_profile, **self._button_style(secondary=True)).grid(row=2, column=0, padx=12, pady=(0, 12), sticky="ew")

    def _build_position_picker(self, parent: ctk.CTkFrame, row: int) -> None:
        box = ctk.CTkFrame(parent, fg_color=COLORS["panel_dark"], border_width=1, border_color=COLORS["border"])
        box.grid(row=row, column=0, padx=14, pady=10, sticky="ew")
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text="Position", font=ctk.CTkFont(weight="bold"), text_color=COLORS["text"]).grid(row=0, column=0, padx=12, pady=(12, 6), sticky="w")
        self.position_canvas = tk.Canvas(box, height=320, bg=COLORS["bg"], highlightthickness=0, cursor="hand2")
        self.position_canvas.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="ew")
        self.position_canvas.bind("<Button-1>", self._position_canvas_click)
        self.position_canvas.bind("<Configure>", lambda _event: self._draw_position_canvas())
        self.position_label = ctk.CTkLabel(box, text="", text_color=COLORS["muted"])
        self.position_label.grid(row=2, column=0, padx=12, pady=(0, 12), sticky="w")
        self._draw_position_canvas()

    def _build_monitor_picker(self, parent: ctk.CTkFrame, row: int) -> None:
        monitors = get_monitors()
        self._monitor_names = [f"{idx + 1}: {m.name} ({m.width}x{m.height})" for idx, m in enumerate(monitors)]
        current = self._monitor_names[min(self.config.overlay.monitor_index, len(self._monitor_names) - 1)]
        ctk.CTkLabel(parent, text="Monitor", font=ctk.CTkFont(weight="bold"), text_color=COLORS["text"]).grid(row=row, column=0, padx=14, pady=(14, 4), sticky="w")
        ctk.CTkOptionMenu(parent, values=self._monitor_names, command=self._set_monitor, **self._option_style()).grid(
            row=row + 1, column=0, padx=14, pady=(0, 8), sticky="ew"
        )
        parent.grid_slaves(row=row + 1, column=0)[0].set(current)

    def _build_detection_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(parent, fg_color=COLORS["panel"], border_width=1, border_color=COLORS["border"])
        left.grid(row=0, column=0, padx=(0, 12), pady=12, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)

        self.performance_var = tk.BooleanVar(value=self.config.detection.performance_mode)
        ctk.CTkSwitch(left, text="Performance mode", variable=self.performance_var, command=self._set_performance_mode, **self._switch_style()).grid(
            row=0, column=0, padx=16, pady=(18, 8), sticky="w"
        )
        self.template_var = tk.BooleanVar(value=self.config.detection.fallback_template_matching)
        ctk.CTkSwitch(left, text="Fallback template matching", variable=self.template_var, command=self._set_template_mode, **self._switch_style()).grid(
            row=1, column=0, padx=16, pady=8, sticky="w"
        )
        self._slider(left, 2, "OCR confidence", self.config.detection.confidence_threshold, 0.4, 0.98, self._set_confidence)

        right = ctk.CTkFrame(parent, fg_color=COLORS["panel"], border_width=1, border_color=COLORS["border"])
        right.grid(row=0, column=1, padx=(12, 0), pady=12, sticky="nsew")
        right.grid_columnconfigure((0, 1, 2, 3), weight=1)
        ctk.CTkLabel(right, text="OCR Scan Region", font=ctk.CTkFont(size=18, weight="bold"), text_color=COLORS["text"]).grid(
            row=0, column=0, columnspan=4, padx=16, pady=(18, 8), sticky="w"
        )
        self.auto_region_var = tk.BooleanVar(value=self.config.detection.auto_ocr_region)
        ctk.CTkSwitch(
            right,
            text="Auto position from screen resolution",
            variable=self.auto_region_var,
            command=self._toggle_auto_region,
            **self._switch_style(),
        ).grid(row=1, column=0, columnspan=4, padx=16, pady=(0, 8), sticky="w")
        self.region_entries: list[ctk.CTkEntry] = []
        displayed_region = active_ocr_region(self.config)
        for idx, label in enumerate(("Left", "Top", "Width", "Height")):
            ctk.CTkLabel(right, text=label, text_color=COLORS["muted"]).grid(row=2, column=idx, padx=8, sticky="w")
            entry = ctk.CTkEntry(right, fg_color=COLORS["input"], border_color=COLORS["border"], text_color=COLORS["text"])
            entry.insert(0, str(displayed_region[idx]))
            entry.grid(row=3, column=idx, padx=8, pady=(0, 12), sticky="ew")
            entry.bind("<FocusOut>", lambda _event: self._save_region())
            entry.bind("<KeyRelease>", lambda _event: self._save_region())
            self.region_entries.append(entry)
        self._sync_region_entry_state()
        ctk.CTkButton(right, text="Auto Calculate Region", command=self._auto_calculate_region, **self._button_style(secondary=True)).grid(
            row=4, column=0, columnspan=4, padx=16, pady=(0, 8), sticky="ew"
        )
        ctk.CTkButton(right, text="Show OCR Scan Box", command=self._show_ocr_region, **self._button_style()).grid(
            row=5, column=0, columnspan=4, padx=16, pady=(0, 12), sticky="ew"
        )

        ctk.CTkLabel(right, text="Tesseract executable", text_color=COLORS["muted"]).grid(row=6, column=0, columnspan=4, padx=16, pady=(8, 4), sticky="w")
        self.tesseract_entry = ctk.CTkEntry(right, fg_color=COLORS["input"], border_color=COLORS["border"], text_color=COLORS["text"])
        self.tesseract_entry.insert(0, self.config.detection.tesseract_cmd)
        self.tesseract_entry.grid(row=7, column=0, columnspan=2, padx=16, sticky="ew")
        self.tesseract_entry.bind("<FocusOut>", lambda _event: self._set_tesseract())
        self.tesseract_entry.bind("<KeyRelease>", lambda _event: self._set_tesseract())
        ctk.CTkButton(right, text="Find", command=self._find_tesseract_clicked, **self._button_style(secondary=True)).grid(row=7, column=2, padx=(0, 8), sticky="ew")
        ctk.CTkButton(right, text="Browse", command=self._browse_tesseract, **self._button_style(secondary=True)).grid(row=7, column=3, padx=(0, 16), sticky="ew")

        ctk.CTkLabel(right, text="Tesseract search output", text_color=COLORS["muted"]).grid(
            row=8, column=0, columnspan=4, padx=16, pady=(12, 4), sticky="w"
        )
        self.tesseract_output = ctk.CTkTextbox(right, height=130, fg_color=COLORS["panel_dark"], border_color=COLORS["border"], text_color=COLORS["text"])
        self.tesseract_output.grid(row=9, column=0, columnspan=4, padx=16, pady=(0, 12), sticky="ew")
        self._show_tesseract_search_output("Ready. Press Find to search common Tesseract install locations.")

        self.ocr_result = ctk.CTkTextbox(right, height=150, fg_color=COLORS["panel_dark"], border_color=COLORS["border"], text_color=COLORS["text"])
        self.ocr_result.grid(row=10, column=0, columnspan=4, padx=16, pady=16, sticky="ew")
        self.ocr_result.insert("1.0", "Run a live OCR test while the map name is visible in game.")
        ctk.CTkButton(right, text="Test OCR Now", command=self._test_ocr, **self._button_style(secondary=True)).grid(row=11, column=0, columnspan=2, padx=(16, 8), pady=(0, 16), sticky="ew")
        self.force_update_button = ctk.CTkButton(right, text=self._force_update_button_text(), command=self.force_update_map, **self._button_style())
        self.force_update_button.grid(
            row=11, column=2, columnspan=2, padx=(8, 16), pady=(0, 16), sticky="ew"
        )

    def _build_hotkeys_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        frame = ctk.CTkFrame(parent, fg_color=COLORS["panel"], border_width=1, border_color=COLORS["border"])
        frame.grid(row=0, column=0, padx=12, pady=12, sticky="nsew")
        frame.grid_columnconfigure(1, weight=1)
        self.hotkey_entries: dict[str, ctk.CTkEntry] = {}
        rows = [
            ("toggle_overlay", "Toggle overlay"),
            ("reload_maps", "Reload maps"),
            ("cycle_variant", "Cycle variant"),
            ("force_select", "Force map menu"),
            ("force_update_map", "Force OCR map update"),
        ]
        for row, (key, label) in enumerate(rows):
            ctk.CTkLabel(frame, text=label, text_color=COLORS["text"]).grid(row=row, column=0, padx=16, pady=12, sticky="w")
            entry = ctk.CTkEntry(frame, fg_color=COLORS["input"], border_color=COLORS["border"], text_color=COLORS["text"])
            entry.insert(0, getattr(self.config.hotkeys, key))
            entry.grid(row=row, column=1, padx=16, pady=12, sticky="ew")
            entry.bind("<FocusOut>", lambda _event, item=key: self._set_hotkey(item))
            entry.bind("<KeyRelease>", lambda _event, item=key: self._set_hotkey(item))
            self.hotkey_entries[key] = entry
        ctk.CTkButton(frame, text="Apply Hotkeys", command=self._apply_hotkeys_from_ui, **self._button_style()).grid(
            row=len(rows), column=0, columnspan=2, padx=16, pady=16, sticky="ew"
        )

    def _build_logs_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        self.log_text = ctk.CTkTextbox(
            parent,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=COLORS["panel_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text"],
        )
        self.log_text.grid(row=0, column=0, padx=12, pady=12, sticky="nsew")

    def _build_settings_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        details = LicenseStore(self.root_path).load_details()
        card = ctk.CTkFrame(parent, fg_color=COLORS["panel"], border_width=1, border_color=COLORS["border"])
        card.grid(row=0, column=0, padx=12, pady=12, sticky="ew")
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            card,
            text="License",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, columnspan=2, padx=16, pady=(18, 10), sticky="w")
        ctk.CTkLabel(card, text="License key", text_color=COLORS["muted"]).grid(
            row=1, column=0, columnspan=2, padx=16, sticky="w"
        )
        self.license_key_entry = ctk.CTkEntry(
            card,
            fg_color=COLORS["input"],
            border_color=COLORS["border"],
            text_color=COLORS["text"],
        )
        self.license_key_entry.insert(0, details.get("license_key", ""))
        self.license_key_entry.configure(state="readonly")
        self.license_key_entry.grid(row=2, column=0, padx=(16, 8), pady=(4, 16), sticky="ew")
        ctk.CTkButton(
            card,
            text="Copy",
            width=82,
            command=self._copy_license_key,
            **self._button_style(secondary=True),
        ).grid(row=2, column=1, padx=(0, 16), pady=(4, 16), sticky="e")
        access, expires, remaining = self._license_time_text(details)
        self._license_detail_row(card, 3, "Access", access)
        self._license_detail_row(card, 4, "Expiration", expires)
        self._license_detail_row(card, 5, "Remaining", remaining)
        max_devices = details.get("max_devices", 0)
        used_devices = details.get("used_devices", 0)
        devices = f"{used_devices} out of {max_devices} used" if max_devices else "Unavailable"
        self._license_detail_row(card, 6, "Devices", devices, bottom_padding=18)

    def _license_detail_row(
        self,
        parent: ctk.CTkFrame,
        row: int,
        label: str,
        value: str,
        bottom_padding: int = 8,
    ) -> None:
        ctk.CTkLabel(parent, text=label, text_color=COLORS["muted"]).grid(
            row=row, column=0, padx=16, pady=(0, bottom_padding), sticky="w"
        )
        ctk.CTkLabel(parent, text=value, text_color=COLORS["text"]).grid(
            row=row, column=1, padx=16, pady=(0, bottom_padding), sticky="e"
        )

    def _copy_license_key(self) -> None:
        key = LicenseStore(self.root_path).load_key()
        if not key:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(key)

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

    def _slider(self, parent: ctk.CTkFrame, row: int, label: str, value: float, minimum: float, maximum: float, command) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, padx=14, pady=10, sticky="ew")
        box.grid_columnconfigure(0, weight=1)
        value_label = ctk.CTkLabel(box, text=f"{label}: {value:g}", text_color=COLORS["text"])
        value_label.grid(row=0, column=0, sticky="w")

        def on_change(raw: float) -> None:
            rounded = round(raw, 2)
            value_label.configure(text=f"{label}: {rounded:g}")
            command(rounded)

        slider = ctk.CTkSlider(
            box,
            from_=minimum,
            to=maximum,
            command=on_change,
            progress_color=COLORS["accent"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        slider.set(value)
        slider.grid(row=1, column=0, pady=(8, 0), sticky="ew")

    def _refresh_map_list(self) -> None:
        for child in self.map_list.winfo_children():
            child.destroy()
        self._map_buttons.clear()
        for row, name in enumerate(self.library.names()):
            button = ctk.CTkButton(
                self.map_list,
                text=name,
                anchor="w",
                command=lambda item=name: self.select_map(item, "manual"),
                **self._button_style(secondary=True),
            )
            button.grid(row=row, column=0, padx=4, pady=4, sticky="ew")
            self._map_buttons[name] = button
        self.map_list.grid_columnconfigure(0, weight=1)
        self._highlight_current_map()

    def _toggle_map_library(self) -> None:
        self.config.map_library_visible = not self.config.map_library_visible
        self._apply_map_library_visibility()
        self._save_later()

    def _toggle_map_settings(self) -> None:
        self.map_settings_visible = not self.map_settings_visible
        self._apply_map_settings_visibility()

    def _apply_map_settings_visibility(self) -> None:
        if self.map_settings_frame:
            if self.map_settings_visible:
                self.map_settings_frame.grid(row=5, column=0, sticky="ew")
            else:
                self.map_settings_frame.grid_remove()
        if self.map_settings_button:
            self.map_settings_button.configure(
                text="Hide Map Settings" if self.map_settings_visible else "Show Map Settings"
            )

    def _apply_map_library_visibility(self) -> None:
        visible = self.config.map_library_visible
        if self.sidebar:
            if visible:
                self.sidebar.grid(row=1, column=0, sticky="nsew")
            else:
                self.sidebar.grid_remove()
        if self.sidebar_show_button:
            if visible:
                self.sidebar_show_button.grid_remove()
            else:
                self.sidebar_show_button.grid(row=1, column=0, padx=(14, 0), pady=18, sticky="nw")
        if self.map_list and visible:
            self.map_list.grid(row=2, column=0, padx=12, pady=0, sticky="nsew")
        if self.map_actions and visible:
            self.map_actions.grid(row=3, column=0, padx=12, pady=16, sticky="ew")
        if self.map_toggle_button:
            self.map_toggle_button.configure(text="Hide" if visible else "Show")
        if self.map_header_label:
            self.map_header_label.configure(text="Map Library" if visible else "Map Library Hidden")

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
                "toggle_overlay": lambda: self.root.after(0, self._toggle_overlay_hotkey),
                "reload_maps": lambda: self.root.after(0, self.reload_maps),
                "cycle_variant": lambda: self.root.after(0, self.cycle_variant),
                "force_select": lambda: self.root.after(0, self._show_window),
                "force_update_map": lambda: self.root.after(0, self.force_update_map),
            }
        )

    def _detected_from_thread(self, result: DetectionResult) -> None:
        self.root.after(0, lambda: self._handle_detection(result))

    def _handle_detection(self, result: DetectionResult) -> None:
        if result.map_name in self.library.entries:
            self.select_map(result.map_name, result.source)

    def _pump_logs(self) -> None:
        self._pump_logs_once()
        if not self.config.detection.performance_mode:
            self._log_after = self.root.after(1000, self._pump_logs)

    def _pump_logs_once(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
        except Exception:
            pass

    def _update_overlay_status(self) -> None:
        self._update_overlay_status_once()
        if not self.config.detection.performance_mode:
            self._status_after = self.root.after(2000, self._update_overlay_status)

    def _update_overlay_status_once(self) -> None:
        if hasattr(self, "overlay_status_label"):
            self.overlay_status_label.configure(text=f"Overlay status: {self.overlay.status()}")

    def _highlight_current_map(self) -> None:
        for name, button in self._map_buttons.items():
            button.configure(
                fg_color=(COLORS["accent_dark"] if name == self.current_map_name else COLORS["input"]),
                hover_color=(COLORS["accent"] if name == self.current_map_name else COLORS["input_hover"]),
                text_color=COLORS["text"],
            )

    def _draw_position_canvas(self) -> None:
        canvas = self.position_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 220)
        height = max(canvas.winfo_height(), 140)
        margin = 18
        canvas.create_rectangle(margin, margin, width - margin, height - margin, outline=COLORS["border"], width=2)
        selected_row, selected_col = self._selected_position_grid()
        box_width = 48
        box_height = 36
        left = margin + box_width / 2
        top = margin + box_height / 2
        right = width - margin - box_width / 2
        bottom = height - margin - box_height / 2
        for row, col in self._edge_position_points():
            cx = left + (right - left) * col / 3
            cy = top + (bottom - top) * row / 3
            selected = row == selected_row and col == selected_col
            px = cx - box_width / 2
            py = cy - box_height / 2
            canvas.create_rectangle(
                px,
                py,
                px + box_width,
                py + box_height,
                fill=(COLORS["accent_dark"] if selected else COLORS["input"]),
                outline=COLORS["accent"] if selected else COLORS["border"],
                width=3 if selected else 2,
            )
            canvas.create_text(
                cx,
                cy,
                text=f"{row + 1},{col + 1}",
                fill=COLORS["text"] if selected else COLORS["muted"],
                font=("Segoe UI", 9, "bold"),
            )
        if hasattr(self, "position_label"):
            self.position_label.configure(text=f"Selected: Row {selected_row + 1}, Column {selected_col + 1}")

    def _edge_position_points(self) -> list[tuple[int, int]]:
        return [
            (row, col)
            for row in range(4)
            for col in range(4)
            if row in {0, 3} or col in {0, 3}
        ]

    def _nearest_edge_position(self, row: int, col: int) -> tuple[int, int]:
        points = self._edge_position_points()
        return min(points, key=lambda point: (point[0] - row) ** 2 + (point[1] - col) ** 2)

    def _selected_position_grid(self) -> tuple[int, int]:
        corner = self.config.overlay.corner
        legacy_positions = {
            "top_left": (0, 0),
            "top_center": (0, 1),
            "top_right": (0, 3),
            "middle_left": (1, 0),
            "middle_right": (1, 3),
            "bottom_left": (3, 0),
            "bottom_center": (3, 1),
            "bottom_right": (3, 3),
        }
        if corner in legacy_positions:
            return legacy_positions[corner]
        if corner.startswith("grid_"):
            parts = corner.split("_")
            if len(parts) == 3:
                try:
                    row = min(max(int(parts[1]), 0), 3)
                    col = min(max(int(parts[2]), 0), 3)
                    if row not in {0, 3} and col not in {0, 3}:
                        return self._nearest_edge_position(row, col)
                    return row, col
                except ValueError:
                    pass
        return 1, 3

    def _position_canvas_click(self, event) -> None:
        width = max(self.position_canvas.winfo_width(), 1)
        height = max(self.position_canvas.winfo_height(), 1)
        margin = 18
        box_width = 48
        box_height = 36
        left = margin + box_width / 2
        top = margin + box_height / 2
        right = width - margin - box_width / 2
        bottom = height - margin - box_height / 2
        points = {}
        for row, col in self._edge_position_points():
            x = left + (right - left) * col / 3
            y = top + (bottom - top) * row / 3
            points[(row, col)] = (event.x - x) ** 2 + (event.y - y) ** 2
        row, col = min(points, key=points.get)
        self.config.overlay.corner = f"grid_{row}_{col}"
        self._draw_position_canvas()
        self.overlay.refresh_settings()
        self._save_later()

    def _toggle_enabled(self) -> None:
        self.config.overlay.enabled = bool(self.enabled_var.get())
        self.overlay.visible = self.config.overlay.enabled
        self.overlay.refresh_settings()
        self._save_later()

    def _toggle_overlay_hotkey(self) -> None:
        self.overlay.toggle()
        if hasattr(self, "enabled_var"):
            self.enabled_var.set(self.config.overlay.enabled)
        self._update_overlay_status_once()
        self._save_later()

    def _show_test_overlay(self) -> None:
        self.overlay.show_for_setup(15)

    def _set_monitor(self, value: str) -> None:
        self.config.overlay.monitor_index = max(0, self._monitor_names.index(value))
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

    def _set_border(self, value: float) -> None:
        self.config.overlay.border_width = int(value)
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

    def _set_rotation(self) -> None:
        self.config.overlay.rotate_with_minimap = bool(self.rotate_var.get())
        self._save_later()

    def _set_profile(self, value: str) -> None:
        self.config.active_profile = value
        self.enabled_var.set(self.config.overlay.enabled)
        self.rotate_var.set(self.config.overlay.rotate_with_minimap)
        self._draw_position_canvas()
        self.overlay.refresh_settings()
        if self.preview:
            self.preview.refresh()
        self._save_later()

    def _new_profile(self) -> None:
        dialog = ctk.CTkInputDialog(text="Profile name", title="New Overlay Profile")
        name = dialog.get_input()
        if not name:
            return
        existing = {profile.name for profile in self.config.profiles}
        if name in existing:
            self.logger.warning("Profile already exists: %s", name)
            return
        self.config.profiles.append(Profile(name=name, overlay=deepcopy(self.config.overlay)))
        self.config.active_profile = name
        self.profile_menu.configure(values=[profile.name for profile in self.config.profiles])
        self.profile_menu.set(name)
        self._save_later()

    def reload_settings(self) -> None:
        previous_maps_dir = self.config.maps_dir
        self.config = self.store.load()
        self.config.overlay.border_width = 0

        self.focus_gate.config = self.config
        self.overlay.config = self.config
        self.detector.config = self.config
        self.hotkeys.config = self.config
        if self.preview:
            self.preview.config = self.config

        if previous_maps_dir != self.config.maps_dir:
            self.library = MapLibrary(self.root_path, self.config.maps_dir)
            self.library.reload()
            self.detector.library = self.library
            self._refresh_map_list()

        self.enabled_var.set(self.config.overlay.enabled)
        self.overlay.visible = self.config.overlay.enabled
        self.rotate_var.set(self.config.overlay.rotate_with_minimap)
        self.performance_var.set(self.config.detection.performance_mode)
        self.template_var.set(self.config.detection.fallback_template_matching)
        if hasattr(self, "auto_region_var"):
            self.auto_region_var.set(self.config.detection.auto_ocr_region)
            self._set_region_entries(active_ocr_region(self.config))
        if hasattr(self, "tesseract_entry"):
            self.tesseract_entry.delete(0, "end")
            self.tesseract_entry.insert(0, self.config.detection.tesseract_cmd)
        if hasattr(self, "profile_menu"):
            self.profile_menu.configure(values=[profile.name for profile in self.config.profiles])
            self.profile_menu.set(self.config.active_profile)
        self._refresh_force_update_labels()
        self._refresh_toggle_overlay_hotkey_label()

        self._apply_performance_timer_state()
        self._apply_map_library_visibility()
        self._register_hotkeys()
        self.overlay.refresh_settings()
        if self.preview:
            self.preview.refresh()
        self._update_overlay_status_once()
        self.logger.info("Settings reloaded from %s", self.store.path)
        self._pump_logs_once()

    def _set_performance_mode(self) -> None:
        self.config.detection.performance_mode = bool(self.performance_var.get())
        self._apply_performance_timer_state()
        self._save_later()

    def _apply_performance_timer_state(self) -> None:
        if self.config.detection.performance_mode:
            if self._log_after:
                self.root.after_cancel(self._log_after)
                self._log_after = None
            if self._status_after:
                self.root.after_cancel(self._status_after)
                self._status_after = None
            self.logger.info("Performance mode enabled: background activity disabled")
            self._pump_logs_once()
            self._update_overlay_status_once()
        else:
            self.logger.info("Performance mode disabled: UI polling and startup checks can run")
            if not self._log_after:
                self._pump_logs()
            if not self._status_after:
                self._update_overlay_status()

    def _set_template_mode(self) -> None:
        self.config.detection.fallback_template_matching = bool(self.template_var.get())
        self._save_later()

    def _set_scan_interval(self, value: float) -> None:
        self.config.detection.scan_interval_ms = int(value)
        self._save_later()

    def _set_confidence(self, value: float) -> None:
        self.config.detection.confidence_threshold = float(value)
        self._save_later()

    def _save_region(self) -> None:
        if self.config.detection.auto_ocr_region:
            self._set_region_entries(active_ocr_region(self.config))
            return
        try:
            self.config.detection.ocr_region = [max(0, int(entry.get())) for entry in self.region_entries]
            self._save_later()
        except ValueError:
            self.logger.warning("OCR region must contain whole numbers")

    def _show_ocr_region(self) -> None:
        region = self._current_ocr_region()
        self.ocr_region_overlay.show(region, seconds=8)

    def _current_ocr_region(self) -> list[int]:
        if self.config.detection.auto_ocr_region:
            region = active_ocr_region(self.config)
            self.config.detection.ocr_region = region
            self._set_region_entries(region)
            self._save_later()
            return region
        self._save_region()
        return self.config.detection.ocr_region

    def _set_region_entries(self, region: list[int]) -> None:
        for entry, value in zip(self.region_entries, region):
            entry.configure(state="normal")
            entry.delete(0, "end")
            entry.insert(0, str(value))
        self._sync_region_entry_state()

    def _sync_region_entry_state(self) -> None:
        if not hasattr(self, "region_entries"):
            return
        state = "disabled" if self.config.detection.auto_ocr_region else "normal"
        for entry in self.region_entries:
            entry.configure(state=state)

    def _toggle_auto_region(self) -> None:
        self.config.detection.auto_ocr_region = bool(self.auto_region_var.get())
        if self.config.detection.auto_ocr_region:
            self._auto_calculate_region()
        else:
            self._sync_region_entry_state()
            self._save_later()

    def _auto_calculate_region(self) -> None:
        region = compute_auto_ocr_region(self.config)
        self.config.detection.auto_ocr_region = True
        if hasattr(self, "auto_region_var"):
            self.auto_region_var.set(True)
        self.config.detection.ocr_region = region
        self._set_region_entries(region)
        self._save_later()
        self.logger.info("Auto OCR region set to left=%s top=%s width=%s height=%s", *region)

    def _set_tesseract(self) -> None:
        self.config.detection.tesseract_cmd = self.tesseract_entry.get().strip()
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
        if hasattr(self, "tesseract_entry"):
            self.tesseract_entry.delete(0, "end")
            self.tesseract_entry.insert(0, str(path))
        self._show_tesseract_search_output(f"Found and saved Tesseract:\n{path}", searched)
        self.store.save(self.config)
        self.logger.info("Found Tesseract at %s", path)

    def _find_tesseract_clicked(self) -> None:
        path, searched = tesseract_search_report()
        if not path:
            self._show_tesseract_search_output("Tesseract not found.", searched)
            self.logger.warning("Could not find tesseract.exe automatically. Use Browse to select it manually.")
            return
        self.tesseract_entry.delete(0, "end")
        self.tesseract_entry.insert(0, str(path))
        self._set_tesseract()
        self.store.save(self.config)
        self._show_tesseract_search_output(f"Found and saved Tesseract:\n{path}", searched)
        self.logger.info("Tesseract path saved: %s", path)

    def _show_tesseract_search_output(self, message: str, searched=None) -> None:
        if not hasattr(self, "tesseract_output"):
            return
        self.tesseract_output.delete("1.0", "end")
        self.tesseract_output.insert("1.0", message)
        if searched:
            self.tesseract_output.insert("end", "\n\nSearched:\n")
            for path in searched:
                marker = "FOUND" if path.exists() else "missing"
                self.tesseract_output.insert("end", f"- [{marker}] {path}\n")

    def _browse_tesseract(self) -> None:
        path = filedialog.askopenfilename(title="Select tesseract.exe", filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
        if path:
            self.tesseract_entry.delete(0, "end")
            self.tesseract_entry.insert(0, path)
            self._set_tesseract()
            self._show_tesseract_search_output(f"Manually selected and saved Tesseract:\n{path}")

    def _test_ocr(self) -> None:
        region = self._current_ocr_region()
        self.ocr_region_overlay.show(region, seconds=8)
        self.ocr_result.delete("1.0", "end")
        self.ocr_result.insert("1.0", "Testing OCR...")

        def run_test() -> None:
            result = self.detector.test_once()
            self.root.after(0, lambda: self._show_ocr_result(result))

        threading.Thread(target=run_test, daemon=True).start()

    def force_update_map(self) -> None:
        self._current_ocr_region()
        self.logger.info("Force OCR map update requested")
        if hasattr(self, "ocr_result"):
            self.ocr_result.delete("1.0", "end")
            self.ocr_result.insert("1.0", "Force updating map from OCR...")

        def run_update() -> None:
            result = self.detector.test_once()
            self.root.after(0, lambda: self._apply_forced_ocr_result(result))

        threading.Thread(target=run_update, daemon=True).start()

    def _apply_forced_ocr_result(self, result: DetectionResult | None) -> None:
        if not result:
            if hasattr(self, "ocr_result"):
                self.ocr_result.delete("1.0", "end")
                self.ocr_result.insert("1.0", "Force update found no confident map match.")
            self.logger.info("Force OCR map update found no confident map match")
            return
        if result.map_name not in self.library.entries:
            if hasattr(self, "ocr_result"):
                self.ocr_result.delete("1.0", "end")
                self.ocr_result.insert("1.0", f"Force update matched {result.map_name}, but that map is not loaded.")
            self.logger.warning("Force OCR matched %s, but that map is not loaded", result.map_name)
            return
        self._handle_detection(result)
        self.overlay.set_ocr_readout(result.map_name, result.confidence, self.config.hotkeys.force_update_map)
        if hasattr(self, "ocr_result"):
            self.ocr_result.delete("1.0", "end")
            self.ocr_result.insert(
                "1.0",
                (
                    f"Force update applied: {result.map_name}\n"
                    f"Confidence: {result.confidence:.0%}\n"
                    f"Source: {result.source}\n"
                    f"Raw text: {result.raw_text}"
                ),
            )
        self.logger.info("Force OCR map update applied: %s (%.0f%%)", result.map_name, result.confidence * 100)

    def _show_ocr_result(self, result: DetectionResult | None) -> None:
        self.ocr_result.delete("1.0", "end")
        if not result:
            self.ocr_result.insert("1.0", "No confident map match detected.")
            return
        applied = result.map_name in self.library.entries
        if applied:
            self._handle_detection(result)
            self.overlay.set_ocr_readout(result.map_name, result.confidence, self.config.hotkeys.force_update_map)
        self.ocr_result.insert(
            "1.0",
            (
                f"Matched: {result.map_name}\n"
                f"Confidence: {result.confidence:.0%}\n"
                f"Source: {result.source}\n"
                f"Overlay updated: {'yes' if applied else 'no - map is not loaded'}\n"
                f"Raw text: {result.raw_text}"
            ),
        )

    def _set_hotkey(self, key: str) -> None:
        setattr(self.config.hotkeys, key, self.hotkey_entries[key].get().strip())
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
            self.force_update_button.configure(text=self._force_update_button_text())

    def _toggle_overlay_hotkey_text(self) -> str:
        hotkey = self.config.hotkeys.toggle_overlay.strip()
        return f"Toggle Overlay: {hotkey.upper()}" if hotkey else "Toggle Overlay: Not set"

    def _refresh_toggle_overlay_hotkey_label(self) -> None:
        if hasattr(self, "preview_toggle_hotkey_label"):
            self.preview_toggle_hotkey_label.configure(text=self._toggle_overlay_hotkey_text())

    def _add_map(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Add map images",
            filetypes=[("Map images", "*.png *.webp *.gif"), ("All files", "*.*")],
        )
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
            self.root.after(0, lambda: self.logger.info(message))

        def worker() -> None:
            try:
                summary = import_hens_callouts(self.library.maps_path, self.logger, progress)
            except Exception as exc:
                self.root.after(0, lambda error=exc: self.logger.error("Hens map update failed: %s", error))
                return
            self.root.after(0, lambda result=summary: self._finish_hens_import(result))

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
            self.root.after(0, lambda error=exc: self.logger.warning("Hens startup cache update skipped: %s", error))
            return
        if summary.downloaded:
            self.root.after(0, lambda result=summary: self._finish_hens_import(result))
        else:
            self.root.after(
                0,
                lambda result=summary: self._finish_hens_startup_check(result),
            )

    def _finish_hens_startup_check(self, summary) -> None:
        self.logger.info(
            "Hens maps cache is current: %s cached, %s total",
            summary.skipped,
            summary.total,
        )
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

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _check_for_app_updates(self) -> None:
        self.app_update_button.configure(state="disabled", text="Checking...")
        self.app_update_status_label.configure(text="Checking GitHub...", text_color=COLORS["muted"])

        def worker() -> None:
            try:
                status = check_for_app_update(self.root_path, __version__)
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._show_app_update_error(error))
                return
            self.root.after(0, lambda result=status: self._show_app_update_status(result))

        threading.Thread(target=worker, name="AppUpdateStatusCheck", daemon=True).start()

    def _show_app_update_status(self, status: AppUpdateStatus) -> None:
        self.app_update_button.configure(state="normal", text="Check for Updates")
        if status.update_available:
            self.app_update_status_label.configure(
                text=f"Update available: {status.latest_version}",
                text_color=COLORS["accent_hover"],
            )
            self.logger.info("App update available: %s", status.latest_version)
            self._show_app_update_dialog(status)
            return
        self.app_update_status_label.configure(text=f"Up to date: {status.current_version}", text_color=COLORS["muted"])
        self.logger.info("App is up to date: %s", status.current_version)

    def _show_app_update_dialog(self, status: AppUpdateStatus) -> None:
        if self.app_update_dialog and self.app_update_dialog.winfo_exists():
            self.app_update_dialog.destroy()

        dialog = ctk.CTkToplevel(self.root)
        self.app_update_dialog = dialog
        dialog.title(f"Update Available - {status.latest_version}")
        dialog.geometry("620x470")
        dialog.minsize(520, 380)
        dialog.configure(fg_color=COLORS["bg"])
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            dialog,
            text=f"{status.latest_version} is available",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, padx=20, pady=(20, 4), sticky="w")
        ctk.CTkLabel(
            dialog,
            text=f"You are currently running {status.current_version}. Review the changes before updating.",
            text_color=COLORS["muted"],
        ).grid(row=1, column=0, padx=20, pady=(0, 12), sticky="w")

        changelog = ctk.CTkTextbox(
            dialog,
            fg_color=COLORS["panel_dark"],
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text"],
            wrap="word",
        )
        changelog.grid(row=2, column=0, padx=20, pady=(0, 14), sticky="nsew")
        changelog.insert("1.0", status.changelog)
        changelog.configure(state="disabled")

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(
            buttons,
            text="Not Now",
            width=110,
            command=dialog.destroy,
            **self._button_style(secondary=True),
        ).grid(row=0, column=0, padx=(0, 10))
        ctk.CTkButton(
            buttons,
            text="Update",
            width=110,
            command=lambda: self._install_app_update(status, dialog),
            **self._button_style(),
        ).grid(row=0, column=1)

    def _install_app_update(self, status: AppUpdateStatus, dialog: ctk.CTkToplevel) -> None:
        dialog.destroy()
        self.app_update_button.configure(state="disabled", text="Downloading...")
        self.app_update_status_label.configure(text=f"Downloading {status.latest_version}...", text_color=COLORS["muted"])

        def worker() -> None:
            try:
                stage_app_update(self.root_path, status, os.getpid())
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._show_app_update_error(error))
                return
            self.root.after(0, lambda: self._finish_app_update_install(status))

        threading.Thread(target=worker, name="AppUpdateDownload", daemon=True).start()

    def _finish_app_update_install(self, status: AppUpdateStatus) -> None:
        self.app_update_status_label.configure(text=f"Installing {status.latest_version}...", text_color=COLORS["muted"])
        self.logger.info("Installing app update: %s", status.latest_version)
        messagebox.showinfo(
            "Update Ready",
            f"{status.latest_version} has been downloaded.\n\n"
            "The app will close to finish installing the update. Reopen it after a few seconds.",
            parent=self.root,
        )
        self.root.after(300, self.close)

    def _show_app_update_error(self, error: Exception) -> None:
        self.app_update_button.configure(state="normal", text="Check for Updates")
        status_text = (
            "Private GitHub release: token required"
            if "public release access is required" in str(error).lower()
            else "Could not check for updates"
        )
        self.app_update_status_label.configure(text=status_text, text_color=COLORS["accent_hover"])
        self.logger.warning("Could not check for app updates: %s", error)

    def _save_later(self) -> None:
        if self._save_after:
            self.root.after_cancel(self._save_after)
        self._save_after = self.root.after_idle(self._save_now)

    def _save_now(self) -> None:
        self._save_after = None
        try:
            self.config.overlay.border_width = 0
            self.store.save(self.config)
        except Exception as exc:
            self.logger.error("Could not save settings to %s: %s", self.store.path, exc)

    def _sync_text_settings_to_config(self) -> None:
        if hasattr(self, "tesseract_entry"):
            self.config.detection.tesseract_cmd = self.tesseract_entry.get().strip()
        if hasattr(self, "hotkey_entries"):
            for key, entry in self.hotkey_entries.items():
                setattr(self.config.hotkeys, key, entry.get().strip())
        if (
            hasattr(self, "region_entries")
            and not self.config.detection.auto_ocr_region
        ):
            try:
                self.config.detection.ocr_region = [max(0, int(entry.get())) for entry in self.region_entries]
            except ValueError:
                self.logger.warning("OCR region must contain whole numbers")
