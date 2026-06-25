from __future__ import annotations

import sys
import traceback
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def write_startup_error(exc: BaseException) -> None:
    root = app_root()
    log_path = root / "startup_error.log"
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        log_path.write_text(details, encoding="utf-8")
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        try:
            input("Press Enter to close...")
        except Exception:
            pass


def main() -> int:
    try:
        from .auto_launch import GUI_MUTEX, acquire_mutex, run_dbd_watcher

        args = {arg.lower() for arg in sys.argv[1:]}
        root = app_root()
        if "--watch-dbd" in args:
            return run_dbd_watcher(root)

        gui_mutex = acquire_mutex(GUI_MUTEX)
        if gui_mutex is None:
            return 0

        from . import __version__
        from .license_gate import require_valid_license

        if not require_valid_license(root, __version__):
            return 0

        from .app import OverlayApp

        start_minimized = "--show" not in args and (getattr(sys, "frozen", False) or "--minimized" in args)
        close_when_dbd_exits = "--close-when-dbd-exits" in args
        app = OverlayApp(root, start_minimized=start_minimized, close_when_dbd_exits=close_when_dbd_exits)
        app.run()
    except Exception as exc:  # pragma: no cover - startup safety net
        write_startup_error(exc)
        print(f"Unable to start DBD Companion Overlay: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
