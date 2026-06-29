from __future__ import annotations

import argparse
import ctypes
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dbd_overlay.secure_config import decrypt_server_url, encrypt_server_url

APP_NAME = "DBDCompanionOverlay"
WINDOWS_RUNTIME_DLLS = ("vcruntime140.dll", "vcruntime140_1.dll")
DEFAULT_UPDATER_CONFIG = {
    "repository": "Werdtuy/DBD_MAP_Overlay",
    "release_tag": "latest-beta",
    "package_asset": "DBDCompanionOverlay.zip",
    "manifest_asset": "update_manifest.json",
    "github_token": "",
}
LICENSE_CONFIG_FILE = "license_config.json"


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


def windows_runtime_dlls() -> list[Path]:
    if sys.platform != "win32":
        return []
    search_dirs = [
        Path(sys.executable).resolve().parent,
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32",
    ]
    found = []
    for name in WINDOWS_RUNTIME_DLLS:
        path = next((directory / name for directory in search_dirs if (directory / name).exists()), None)
        if path:
            found.append(path)
        else:
            print(f"Letting PyInstaller resolve Windows runtime dependency: {name}", flush=True)
    return found


def verify_exe_payload(exe_path: Path) -> None:
    from PyInstaller.archive.readers import CArchiveReader

    names = {Path(name).name.lower() for name in CArchiveReader(str(exe_path)).toc}
    expected = {"python312.dll", LICENSE_CONFIG_FILE, *WINDOWS_RUNTIME_DLLS}
    missing = sorted(name for name in expected if name not in names)
    if missing:
        raise RuntimeError(f"Built executable is missing required runtime files: {', '.join(missing)}")
    print(f"Verified bundled runtime files: {', '.join(sorted(expected))}", flush=True)


def release_notes(changelog_path: Path, version: str) -> str:
    heading = f"## {version}"
    lines = changelog_path.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(heading) + 1
    except ValueError:
        return "No release notes were provided."
    notes = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        notes.append(line)
    return "\n".join(notes).strip() or "No release notes were provided."


def load_license_config(root: Path, prompt_if_missing: bool = False) -> str:
    server_url = os.environ.get("DBD_OVERLAY_LICENSE_SERVER_URL", "").strip()
    config_path = root / LICENSE_CONFIG_FILE
    prompted = False
    if not server_url:
        try:
            encrypted_config = config_path.read_text(encoding="utf-8")
            decrypt_server_url(encrypted_config)
            return encrypted_config
        except Exception as exc:
            if not prompt_if_missing:
                raise RuntimeError(
                    "Encrypted license server configuration is missing. Set DBD_OVERLAY_LICENSE_SERVER_URL or add license_config.json."
                ) from exc
            print("", flush=True)
            print("Private build configuration is missing.", flush=True)
            server_url = input("Enter the HTTPS activation service URL: ").strip()
            prompted = True
    server_url = server_url.rstrip("/")
    if not server_url.startswith("https://"):
        raise RuntimeError("License server URL must use HTTPS.")
    encrypted_config = encrypt_server_url(server_url)
    if prompted:
        config_path.write_text(encrypted_config, encoding="utf-8")
        print(f"Saved private ignored build configuration: {config_path}", flush=True)
    return encrypted_config


def create_release_zip(
    root: Path,
    exe_path: Path,
    manifest: dict[str, str],
    encrypted_license_config: str,
) -> Path:
    release_dir = root / "release"
    package_dir = release_dir / APP_NAME
    zip_path = release_dir / f"{APP_NAME}.zip"

    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(exe_path, package_dir / exe_path.name)
    (package_dir / "updater_config.json").write_text(
        json.dumps(DEFAULT_UPDATER_CONFIG, indent=2),
        encoding="utf-8",
    )
    (package_dir / LICENSE_CONFIG_FILE).write_text(encrypted_license_config, encoding="utf-8")
    (package_dir / "version.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

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
    parser.add_argument("-PromptLicenseConfig", action="store_true", dest="prompt_license_config")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    build_dir = root / "build"
    exe_path = root / f"{APP_NAME}.exe"
    run_py = root / "scripts" / "run.py"
    assets_dir = root / "assets"
    icon_path = assets_dir / "app_icon.ico"
    app_version = str(runpy.run_path(str(root / "dbd_overlay" / "__init__.py"))["__version__"])
    manifest = {
        "version": app_version,
        "changelog": release_notes(root / "CHANGELOG.md", app_version),
    }
    encrypted_license_config = load_license_config(root, prompt_if_missing=args.prompt_license_config)

    if not run_py.exists():
        raise FileNotFoundError(f"Could not find {run_py}")
    if not icon_path.exists():
        raise FileNotFoundError(f"Could not find app icon: {icon_path}")

    remove_existing_exe(exe_path)
    remove_existing_exe(root / "DBDCompanionUpdater.exe")

    print(f"Using Python: {sys.executable}", flush=True)
    print("Installing/updating PyInstaller and app requirements...", flush=True)
    run([sys.executable, "-m", "pip", "install", "pyinstaller"])
    run([sys.executable, "-m", "pip", "install", "-r", str(root / "requirements.txt")])

    window_mode = "--console" if args.console else "--windowed"
    bundled_config_dir = Path(tempfile.mkdtemp(prefix="dbd-overlay-build-input-"))
    bundled_license_config = bundled_config_dir / LICENSE_CONFIG_FILE
    bundled_license_config.write_text(encrypted_license_config, encoding="utf-8")
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
        "PySide6",
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
    pyinstaller_args.extend(["--add-data", f"{bundled_license_config}{os.pathsep}."])
    for runtime_dll in windows_runtime_dlls():
        pyinstaller_args.extend(["--add-binary", f"{runtime_dll}{os.pathsep}."])
    print(f"Using icon: {icon_path}", flush=True)
    pyinstaller_args.append(f"--icon={icon_path}")
    pyinstaller_args.append(str(run_py))

    print(f"Building {APP_NAME}.exe...", flush=True)
    try:
        run(pyinstaller_args)
    finally:
        shutil.rmtree(bundled_config_dir, ignore_errors=True)
    verify_exe_payload(exe_path)
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
    zip_path = create_release_zip(root, exe_path, manifest, encrypted_license_config)
    print("", flush=True)
    print("Shareable zip created:", flush=True)
    print(zip_path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
