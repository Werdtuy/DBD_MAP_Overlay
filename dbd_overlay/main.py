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
        from .app import OverlayApp

        root = app_root()
        app = OverlayApp(root)
        app.run()
    except Exception as exc:  # pragma: no cover - startup safety net
        write_startup_error(exc)
        print(f"Unable to start DBD Companion Overlay: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
