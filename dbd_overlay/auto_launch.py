from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

import psutil

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only feature
    winreg = None


APP_EXE = "DBDCompanionOverlay.exe"
DBD_PROCESS_NAMES = {
    "dead by daylight",
    "deadbydaylight",
    "deadbydaylight.exe",
    "deadbydaylight-win64-shipping.exe",
}
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "DBD Companion Overlay Watcher"
WATCH_INTERVAL_SECONDS = 4.0
WATCHER_MUTEX = "Local\\DBDCompanionOverlayWatcher"
GUI_MUTEX = "Local\\DBDCompanionOverlayGui"
ERROR_ALREADY_EXISTS = 183


def _is_windows() -> bool:
    return sys.platform == "win32"


def _app_exe_path(root: Path) -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return (root / APP_EXE).resolve()


def acquire_mutex(name: str) -> int | None:
    if not _is_windows():
        return 1
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    if not handle:
        return None
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        ctypes.windll.kernel32.CloseHandle(handle)
        return None
    return handle


def _close_mutex(handle: int | None) -> None:
    if handle and _is_windows():
        try:
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass


def _command_for_watcher(app_exe: Path) -> str:
    return f'"{app_exe}" --watch-dbd'


def _clean_pyinstaller_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        upper_key = key.upper()
        if upper_key.startswith("_PYI") or upper_key == "_MEIPASS2":
            env.pop(key, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _spawn_app(app_exe: Path, args: list[str], root: Path) -> None:
    subprocess.Popen(
        [str(app_exe), *args],
        cwd=str(root),
        env=_clean_pyinstaller_env(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )


def ensure_auto_launcher(root: Path, logger: logging.Logger) -> None:
    if not _is_windows() or not getattr(sys, "frozen", False) or winreg is None:
        return
    app_exe = _app_exe_path(root)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _command_for_watcher(app_exe))
        logger.info("Auto-launch watcher registered for Windows startup.")
    except Exception as exc:
        logger.warning("Could not register auto-launch watcher: %s", exc)


def start_watcher_if_needed(root: Path, logger: logging.Logger) -> None:
    if not _is_windows() or not getattr(sys, "frozen", False):
        return
    app_exe = _app_exe_path(root)
    try:
        _spawn_app(app_exe, ["--watch-dbd"], root)
        logger.info("Auto-launch watcher started.")
    except Exception as exc:
        logger.warning("Could not start auto-launch watcher: %s", exc)


def _processes() -> Iterable[psutil.Process]:
    return psutil.process_iter(["pid", "name", "exe", "cmdline"])


def _is_dead_by_daylight_running() -> bool:
    for proc in _processes():
        try:
            name = (proc.info.get("name") or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name in DBD_PROCESS_NAMES:
            return True
    return False


def _same_path(left: str | None, right: Path) -> bool:
    if not left:
        return False
    try:
        return Path(left).resolve() == right.resolve()
    except Exception:
        return str(left).lower() == str(right).lower()


def _is_overlay_gui_running(app_exe: Path, own_pid: int) -> bool:
    for proc in _processes():
        try:
            if proc.info.get("pid") == own_pid:
                continue
            if not _same_path(proc.info.get("exe"), app_exe):
                continue
            cmdline = [str(part).lower() for part in (proc.info.get("cmdline") or [])]
            if "--watch-dbd" not in cmdline:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def run_dbd_watcher(root: Path) -> int:
    mutex = acquire_mutex(WATCHER_MUTEX)
    if mutex is None:
        return 0
    app_exe = _app_exe_path(root)
    launched_for_session = False
    try:
        while True:
            dbd_running = _is_dead_by_daylight_running()
            if not dbd_running:
                launched_for_session = False
            elif not launched_for_session:
                if not _is_overlay_gui_running(app_exe, own_pid=os.getpid()):
                    _spawn_app(app_exe, ["--minimized"], root)
                launched_for_session = True
            time.sleep(WATCH_INTERVAL_SECONDS)
    finally:
        _close_mutex(mutex)
