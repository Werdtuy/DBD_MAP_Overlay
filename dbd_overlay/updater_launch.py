from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from . import __version__


UPDATER_NAME = "DBDCompanionUpdater.exe"


def launch_packaged_updater(app_root: Path) -> None:
    if not getattr(sys, "frozen", False):
        return

    updater_path = app_root / UPDATER_NAME
    if not updater_path.exists():
        return

    try:
        temp_dir = Path(tempfile.mkdtemp(prefix="dbd-overlay-updater-"))
        temp_updater = temp_dir / UPDATER_NAME
        shutil.copy2(updater_path, temp_updater)
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [
                str(temp_updater),
                "--app-dir",
                str(app_root),
                "--app-pid",
                str(os.getpid()),
                "--current-version",
                __version__,
            ],
            cwd=str(app_root),
            creationflags=creation_flags,
            close_fds=True,
        )
    except Exception:
        # Updating must never stop the overlay from opening.
        return
