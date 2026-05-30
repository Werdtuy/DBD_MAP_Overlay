from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile


DEFAULT_CONFIG = {
    "repository": "Werdtuy/DBD_MAPoverlay",
    "release_tag": "latest-beta",
    "package_asset": "DBDCompanionOverlay.zip",
    "manifest_asset": "update_manifest.json",
    "github_token": "",
}
APP_EXE = "DBDCompanionOverlay.exe"
UPDATER_EXE = "DBDCompanionUpdater.exe"
LOCK_FILE = ".dbd-overlay-updater.lock"


def configure_logging(app_dir: Path) -> logging.Logger:
    logger = logging.getLogger("DBDCompanionUpdater")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(app_dir / "updater.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def load_config(app_dir: Path) -> dict[str, str]:
    config = dict(DEFAULT_CONFIG)
    path = app_dir / "updater_config.json"
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                config.update({key: str(value) for key, value in saved.items() if key in config})
        except Exception:
            pass
    token = os.environ.get("DBD_OVERLAY_GITHUB_TOKEN", "").strip()
    if token:
        config["github_token"] = token
    return config


def api_json(url: str, token: str = "") -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "DBDCompanionUpdater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def find_asset(release: dict, name: str) -> dict:
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            return asset
    raise RuntimeError(f"GitHub release asset is missing: {name}")


def download_asset(asset: dict, destination: Path, token: str = "") -> None:
    url = asset.get("url") or asset.get("browser_download_url")
    if not url:
        raise RuntimeError("GitHub release asset has no download URL")
    headers = {
        "Accept": "application/octet-stream",
        "User-Agent": "DBDCompanionUpdater",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=60) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)


def safe_extract(archive_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if destination not in target.parents and target != destination:
                raise RuntimeError("Update archive contains an unsafe path")
        archive.extractall(destination)


def wait_for_process(pid: int, logger: logging.Logger) -> None:
    if pid <= 0:
        return
    if sys.platform != "win32":
        while True:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(1)

    synchronize = 0x00100000
    wait_infinite = 0xFFFFFFFF
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return
    logger.info("Update downloaded. Waiting for the overlay to close before applying it.")
    try:
        ctypes.windll.kernel32.WaitForSingleObject(handle, wait_infinite)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def package_root(extracted_dir: Path) -> Path:
    direct = extracted_dir / "DBDCompanionOverlay"
    if (direct / APP_EXE).exists():
        return direct
    if (extracted_dir / APP_EXE).exists():
        return extracted_dir
    for exe in extracted_dir.rglob(APP_EXE):
        return exe.parent
    raise RuntimeError(f"Update package is missing {APP_EXE}")


def copy_update(package_dir: Path, app_dir: Path) -> None:
    for source in package_dir.rglob("*"):
        relative = source.relative_to(package_dir)
        if relative.parts and relative.parts[0].lower() in {"maps", "config"}:
            continue
        if relative.as_posix().lower() == "updater_config.json":
            continue
        destination = app_dir / relative
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_destination = destination.with_suffix(destination.suffix + ".update")
        shutil.copy2(source, temp_destination)
        os.replace(temp_destination, destination)


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock(app_dir: Path) -> tuple[int, Path] | None:
    path = app_dir / LOCK_FILE
    for _attempt in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                existing_pid = int(path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                existing_pid = 0
            if process_is_running(existing_pid):
                return None
            try:
                path.unlink()
            except OSError:
                return None
            continue
        os.write(descriptor, str(os.getpid()).encode("ascii", errors="ignore"))
        return descriptor, path
    return None


def release_lock(lock: tuple[int, Path] | None) -> None:
    if not lock:
        return
    descriptor, path = lock
    try:
        os.close(descriptor)
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def schedule_temp_cleanup() -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    temp_dir = Path(sys.executable).resolve().parent
    if not temp_dir.name.startswith("dbd-overlay-updater-"):
        return
    command = f'ping 127.0.0.1 -n 3 >nul & rmdir /s /q "{temp_dir}"'
    subprocess.Popen(
        ["cmd.exe", "/d", "/s", "/c", command],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )


def check_and_update(app_dir: Path, app_pid: int, current_version: str, logger: logging.Logger) -> None:
    config = load_config(app_dir)
    repository = config["repository"].strip()
    tag = config["release_tag"].strip()
    token = config["github_token"].strip()
    if not repository or not tag:
        logger.info("Updater is disabled because repository or release_tag is empty.")
        return

    release_url = f"https://api.github.com/repos/{repository}/releases/tags/{tag}"
    release = api_json(release_url, token)
    manifest_asset = find_asset(release, config["manifest_asset"])

    with tempfile.TemporaryDirectory(prefix="dbd-overlay-download-") as temp_name:
        temp_dir = Path(temp_name)
        manifest_path = temp_dir / "update_manifest.json"
        download_asset(manifest_asset, manifest_path, token)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        remote_version = str(manifest.get("version", "")).strip()
        if not remote_version:
            raise RuntimeError("Update manifest does not contain a version")
        if remote_version == current_version:
            logger.info("Already running the newest version: %s", current_version)
            return

        logger.info("Downloading update %s (current version: %s)", remote_version, current_version)
        package_asset = find_asset(release, config["package_asset"])
        archive_path = temp_dir / config["package_asset"]
        extracted_dir = temp_dir / "extracted"
        extracted_dir.mkdir()
        download_asset(package_asset, archive_path, token)
        safe_extract(archive_path, extracted_dir)
        update_root = package_root(extracted_dir)
        wait_for_process(app_pid, logger)
        copy_update(update_root, app_dir)
        logger.info("Installed update %s", remote_version)

    app_path = app_dir / APP_EXE
    if app_path.exists():
        subprocess.Popen([str(app_path)], cwd=str(app_dir), close_fds=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Update DBD Companion Overlay from GitHub releases.")
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--app-pid", type=int, default=0)
    parser.add_argument("--current-version", default="")
    args = parser.parse_args()

    app_dir = args.app_dir.resolve()
    app_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(app_dir)
    lock = acquire_lock(app_dir)
    if not lock:
        logger.info("Another updater check is already running.")
        return 0
    try:
        check_and_update(app_dir, args.app_pid, args.current_version, logger)
    except (HTTPError, URLError) as exc:
        logger.warning("Could not check GitHub for updates: %s", exc)
    except Exception:
        logger.exception("Automatic update failed")
    finally:
        release_lock(lock)
        schedule_temp_cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
