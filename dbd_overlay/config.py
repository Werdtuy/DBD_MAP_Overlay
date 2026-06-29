from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
import json
from pathlib import Path
from typing import Any, get_type_hints


CONFIG_VERSION = 1


@dataclass
class OverlaySettings:
    enabled: bool = True
    monitor_index: int = 0
    corner: str = "grid_1_3"
    margin_x: int = 32
    margin_y: int = 32
    size: int = 340
    opacity: float = 0.86
    zoom: float = 1.0
    border_width: int = 0
    border_color: str = "#8BE9FD"
    corner_radius: int = 18
    animation_speed: float = 1.0
    transition_ms: int = 180
    rotate_with_minimap: bool = False


@dataclass
class DetectionSettings:
    enabled: bool = False
    scan_interval_ms: int = 750
    performance_mode: bool = True
    confidence_threshold: float = 0.72
    auto_ocr_region: bool = True
    ocr_region: list[int] = field(default_factory=lambda: [696, 956, 655, 104])
    auto_region_width_ratio: float = 0.32
    auto_region_height_ratio: float = 0.09
    auto_region_top_ratio: float = 0.83
    tesseract_cmd: str = ""
    fallback_template_matching: bool = False
    template_threshold: float = 0.82


@dataclass
class HotkeySettings:
    toggle_overlay: str = "ctrl+shift+o"
    reload_maps: str = "ctrl+shift+r"
    cycle_variant: str = "ctrl+shift+v"
    force_select: str = "ctrl+shift+m"
    force_update_map: str = "k"


@dataclass
class GameSettings:
    window_title_keywords: list[str] = field(default_factory=lambda: ["Dead by Daylight"])
    process_names: list[str] = field(
        default_factory=lambda: [
            "DeadByDaylight.exe",
            "DeadByDaylight",
            "DeadByDaylight-Win64-Shipping.exe",
            "DeadByDaylight-Win64-Shipping",
        ]
    )
    require_focus: bool = True
    allow_overlay_when_visible: bool = True
    allow_overlay_when_running: bool = True


@dataclass
class UpdateSettings:
    check_for_map_updates: bool = False
    update_manifest_url: str = ""
    auto_update_hens_maps: bool = True


@dataclass
class EscapeStreakPlayer:
    name: str = ""
    status: str = "Ready"


@dataclass
class EscapeStreakSettings:
    enabled: bool = False
    lobby_code: str = ""
    streak: int = 0
    sync_enabled: bool = False
    sync_server_url: str = ""
    sync_lobby_code: str = ""
    sync_player_tag: str = ""
    sync_player_id: str = ""
    sync_player_name: str = ""
    sync_revision: int = 0
    players: list[EscapeStreakPlayer] = field(
        default_factory=lambda: [EscapeStreakPlayer() for _ in range(4)]
    )


@dataclass
class Profile:
    name: str = "Default"
    overlay: OverlaySettings = field(default_factory=OverlaySettings)


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    maps_dir: str = "Maps"
    map_library_visible: bool = False
    active_profile: str = "Default"
    profiles: list[Profile] = field(default_factory=lambda: [Profile()])
    detection: DetectionSettings = field(default_factory=DetectionSettings)
    hotkeys: HotkeySettings = field(default_factory=HotkeySettings)
    game: GameSettings = field(default_factory=GameSettings)
    updates: UpdateSettings = field(default_factory=UpdateSettings)
    escape_streak: EscapeStreakSettings = field(default_factory=EscapeStreakSettings)
    last_selected_map: str = ""

    @property
    def overlay(self) -> OverlaySettings:
        for profile in self.profiles:
            if profile.name == self.active_profile:
                return profile.overlay
        self.profiles.append(Profile(name=self.active_profile))
        return self.profiles[-1].overlay


def _coerce_dataclass(cls: type, data: dict[str, Any]):
    kwargs = {}
    type_hints = get_type_hints(cls)
    for f in fields(cls):
        value = data.get(f.name)
        if value is None:
            continue
        target = type_hints.get(f.name, f.type)
        if is_dataclass(target) and isinstance(value, dict):
            kwargs[f.name] = _coerce_dataclass(target, value)
        elif f.name == "profiles" and isinstance(value, list):
            kwargs[f.name] = [
                Profile(
                    name=item.get("name", "Default"),
                    overlay=_coerce_dataclass(OverlaySettings, item.get("overlay", {})),
                )
                for item in value
                if isinstance(item, dict)
            ]
        elif f.name == "players" and isinstance(value, list):
            players = [
                _coerce_dataclass(EscapeStreakPlayer, item)
                for item in value
                if isinstance(item, dict)
            ]
            kwargs[f.name] = (players + [EscapeStreakPlayer() for _ in range(4)])[:4]
        else:
            kwargs[f.name] = value
    return cls(**kwargs)


class ConfigStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config_dir = root / "config"
        self.path = self.config_dir / "settings.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> AppConfig:
        if not self.path.exists():
            config = AppConfig()
            self.save(config)
            return config
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return _coerce_dataclass(AppConfig, data)
        except Exception:
            backup = self.path.with_suffix(".broken.json")
            self.path.replace(backup)
            config = AppConfig()
            self.save(config)
            return config

    def save(self, config: AppConfig) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(config), indent=2, ensure_ascii=False)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)
