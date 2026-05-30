from __future__ import annotations

import argparse
import ctypes
import json
import os
import runpy
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path


APP_NAME = "DBDCompanionOverlay"
UPDATER_NAME = "DBDCompanionUpdater"
DEFAULT_UPDATER_CONFIG = {
    "repository": "Werdtuy/DBD_MAPoverlay",
    "release_tag": "latest-beta",
    "package_asset": "DBDCompanionOverlay.zip",
    "manifest_asset": "update_manifest.json",
    "github_token": "",
}


def run(command: list[str]) -> None:
    print(" ".join(f'"{part}"' if " " in part else part for part in command), flush=True)
    subprocess.check_call(command)


def remove_existing_exe(path: Path) -> None:
    if not path.exists():
        return
    for attempt in range(5):
        try:
            path.unlink()
            print(f"Removed old executable: {path}", flush=True)
            return
        except PermissionError:
            if attempt == 4:
                raise
            print("Waiting for old executable to unlock...", flush=True)
            time.sleep(1)


def refresh_shell_icons() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
        ie4uinit = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "ie4uinit.exe"
        if ie4uinit.exists():
            subprocess.run([str(ie4uinit), "-show"], check=False)
        print("Requested Windows Explorer icon refresh.", flush=True)
    except Exception as exc:
        print(f"Could not refresh Explorer icon cache: {exc}", flush=True)


def create_release_zip(root: Path, exe_path: Path, updater_path: Path, manifest: dict[str, str]) -> Path:
    release_dir = root / "release"
    package_dir = release_dir / APP_NAME
    zip_path = release_dir / f"{APP_NAME}.zip"

    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(exe_path, package_dir / exe_path.name)
    shutil.copy2(updater_path, package_dir / updater_path.name)
    (package_dir / "updater_config.json").write_text(
        json.dumps(DEFAULT_UPDATER_CONFIG, indent=2),
        encoding="utf-8",
    )
    (package_dir / "version.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (package_dir / "Maps").mkdir(exist_ok=True)
    (package_dir / "config").mkdir(exist_ok=True)

    readme = package_dir / "README.txt"
    readme.write_text(
        "\n".join(
            [
                "DBD Companion Overlay",
                "",
                "1. Run DBDCompanionOverlay.exe.",
                "2. The app will create settings and download missing Hens maps on first startup.",
                "3. DBDCompanionUpdater.exe checks GitHub for updates automatically when the app starts.",
                "4. Install Tesseract OCR separately if OCR does not work.",
                "5. Windows may warn about unsigned apps. Allow the app if you trust the sender.",
                "",
                "Default force-update hotkey: K",
            ]
        ),
        encoding="utf-8",
    )

    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package_dir.rglob("*"):
            archive.write(path, path.relative_to(release_dir))

    return zip_path


def main() -> int:
    argv = [
        arg.replace("/Windowed", "-Windowed").replace("/Console", "-Console")
        for arg in sys.argv[1:]
    ]
    parser = argparse.ArgumentParser(description="Build the DBD Companion Overlay executable.")
    parser.add_argument("-Windowed", action="store_true", dest="windowed")
    parser.add_argument("-Console", action="store_true", dest="console")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    build_dir = root / "build"
    exe_path = root / f"{APP_NAME}.exe"
    updater_path = root / f"{UPDATER_NAME}.exe"
    run_py = root / "scripts" / "run.py"
    updater_py = root / "scripts" / "updater.py"
    assets_dir = root / "assets"
    icon_path = assets_dir / "app_icon.ico"
    app_version = str(runpy.run_path(str(root / "dbd_overlay" / "__init__.py"))["__version__"])
    manifest = {"version": app_version}

    if not run_py.exists():
        raise FileNotFoundError(f"Could not find {run_py}")
    if not updater_py.exists():
        raise FileNotFoundError(f"Could not find {updater_py}")
    if not icon_path.exists():
        raise FileNotFoundError(f"Could not find app icon: {icon_path}")

    remove_existing_exe(exe_path)
    remove_existing_exe(updater_path)

    print(f"Using Python: {sys.executable}", flush=True)
    print("Installing/updating PyInstaller and app requirements...", flush=True)
    run([sys.executable, "-m", "pip", "install", "pyinstaller"])
    run([sys.executable, "-m", "pip", "install", "-r", str(root / "requirements.txt")])

    window_mode = "--console" if args.console else "--windowed"
    pyinstaller_args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        window_mode,
        "--onefile",
        "--name",
        APP_NAME,
        "--distpath",
        str(root),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(build_dir),
        "--collect-all",
        "customtkinter",
        "--collect-all",
        "charset_normalizer",
        "--collect-all",
        "chardet",
        "--hidden-import",
        "charset_normalizer",
        "--hidden-import",
        "chardet",
        "--hidden-import",
        "PIL._tkinter_finder",
    ]
    if assets_dir.exists():
        pyinstaller_args.extend(["--add-data", f"{assets_dir}{os.pathsep}assets"])
    print(f"Using icon: {icon_path}", flush=True)
    pyinstaller_args.append(f"--icon={icon_path}")
    pyinstaller_args.append(str(run_py))

    print(f"Building {APP_NAME}.exe...", flush=True)
    run(pyinstaller_args)

    updater_pyinstaller_args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        window_mode,
        "--onefile",
        "--name",
        UPDATER_NAME,
        "--distpath",
        str(root),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(build_dir),
        f"--icon={icon_path}",
        str(updater_py),
    ]
    print(f"Building {UPDATER_NAME}.exe...", flush=True)
    run(updater_pyinstaller_args)
    refresh_shell_icons()

    (root / "Maps").mkdir(exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    updater_config_path = root / "updater_config.json"
    if not updater_config_path.exists():
        updater_config_path.write_text(json.dumps(DEFAULT_UPDATER_CONFIG, indent=2), encoding="utf-8")
    (root / "version.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (root / "update_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("", flush=True)
    print("Build complete:", flush=True)
    print(exe_path, flush=True)
    print(updater_path, flush=True)
    zip_path = create_release_zip(root, exe_path, updater_path, manifest)
    print("", flush=True)
    print("Shareable zip created:", flush=True)
    print(zip_path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
