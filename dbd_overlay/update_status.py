from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile


DEFAULT_CONFIG = {
    "repository": "Werdtuy/DBD_MAP_Overlay",
    "release_tag": "latest-beta",
    "package_asset": "DBDCompanionOverlay.zip",
    "manifest_asset": "update_manifest.json",
    "github_token": "",
}
APP_EXE = "DBDCompanionOverlay.exe"
UPDATE_SIDECAR_FILES = ("license_config.json", "updater_config.json", "version.json")
BETA_VERSION_PATTERN = re.compile(r"^\s*beta\s+(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def parse_beta_version(version: str) -> Decimal | None:
    match = BETA_VERSION_PATTERN.match(version)
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


@dataclass(frozen=True)
class AppUpdateStatus:
    current_version: str
    latest_version: str
    changelog: str
    package_url: str
    github_token: str

    @property
    def update_available(self) -> bool:
        current = parse_beta_version(self.current_version)
        latest = parse_beta_version(self.latest_version)
        if current is None or latest is None:
            return False
        return latest > current


def load_updater_config(app_dir: Path) -> dict[str, str]:
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


def _request_json(url: str, token: str = "", accept: str = "application/vnd.github+json") -> dict:
    headers = {
        "Accept": accept,
        "User-Agent": "DBDCompanionOverlay",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _download(url: str, destination: Path, token: str = "") -> None:
    headers = {
        "Accept": "application/octet-stream",
        "User-Agent": "DBDCompanionOverlay",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=60) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)


def _release_asset(release: dict, name: str) -> dict:
    asset = next((item for item in release.get("assets", []) if item.get("name") == name), None)
    if not asset or not asset.get("url"):
        raise RuntimeError(f"GitHub release asset is missing: {name}")
    return asset


def _public_asset_url(repository: str, tag: str, name: str) -> str:
    return (
        f"https://github.com/{quote(repository, safe='/')}/releases/download/"
        f"{quote(tag, safe='')}/{quote(name, safe='')}"
    )


def check_for_app_update(app_dir: Path, current_version: str) -> AppUpdateStatus:
    config = load_updater_config(app_dir)
    repository = config["repository"].strip()
    tag = config["release_tag"].strip()
    token = config["github_token"].strip()
    if not repository or not tag:
        raise RuntimeError("Updater repository settings are missing")

    manifest_name = config["manifest_asset"]
    package_name = config["package_asset"]
    if token:
        release = _request_json(f"https://api.github.com/repos/{repository}/releases/tags/{tag}", token)
        manifest_asset = _release_asset(release, manifest_name)
        manifest = _request_json(manifest_asset["url"], token, "application/octet-stream")
        package_url = _release_asset(release, package_name)["url"]
    else:
        manifest_url = _public_asset_url(repository, tag, manifest_name)
        package_url = _public_asset_url(repository, tag, package_name)
        try:
            manifest = _request_json(manifest_url, accept="application/json")
        except HTTPError as exc:
            if exc.code in {403, 404}:
                raise RuntimeError("GitHub release is private or unavailable. Public release access is required.") from exc
            raise
    latest_version = str(manifest.get("version", "")).strip()
    if not latest_version:
        raise RuntimeError("Update manifest does not contain a version")
    changelog = str(manifest.get("changelog", "")).strip() or "No release notes were provided."
    return AppUpdateStatus(
        current_version=current_version,
        latest_version=latest_version,
        changelog=changelog,
        package_url=package_url,
        github_token=token,
    )


def _safe_extract(archive_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if destination not in target.parents and target != destination:
                raise RuntimeError("Update archive contains an unsafe path")
        archive.extractall(destination)


def _package_root(extracted_dir: Path) -> Path:
    direct = extracted_dir / "DBDCompanionOverlay"
    if (direct / APP_EXE).exists():
        return direct
    if (extracted_dir / APP_EXE).exists():
        return extracted_dir
    for exe in extracted_dir.rglob(APP_EXE):
        return exe.parent
    raise RuntimeError(f"Update package is missing {APP_EXE}")


def stage_app_update(app_dir: Path, status: AppUpdateStatus, app_pid: int) -> None:
    app_path = app_dir / APP_EXE
    if not app_path.exists():
        raise RuntimeError("Updates can only be installed from the packaged app")

    staging_dir = Path(tempfile.mkdtemp(prefix="dbd-overlay-update-"))
    try:
        archive_path = staging_dir / "update.zip"
        extracted_dir = staging_dir / "extracted"
        extracted_dir.mkdir()
        _download(status.package_url, archive_path, status.github_token)
        _safe_extract(archive_path, extracted_dir)
        package_dir = _package_root(extracted_dir)
        source_exe = package_dir / APP_EXE
        for name in UPDATE_SIDECAR_FILES:
            if not (package_dir / name).exists():
                raise RuntimeError(f"Update package is missing {name}")

        script_path = staging_dir / "install_update.cmd"
        sidecar_commands = []
        for name in UPDATE_SIDECAR_FILES:
            source = package_dir / name
            destination = app_dir / name
            sidecar_commands.extend(
                [
                    f'copy /Y "{source}" "{destination}.update" >NUL',
                    f'move /Y "{destination}.update" "{destination}" >NUL',
                ]
            )
        script_path.write_text(
            "\n".join(
                [
                    "@echo off",
                    "setlocal",
                    ":wait_for_app",
                    f'tasklist /FI "PID eq {app_pid}" 2>NUL | find "{app_pid}" >NUL',
                    "if not errorlevel 1 (",
                    "  ping 127.0.0.1 -n 2 >NUL",
                    "  goto wait_for_app",
                    ")",
                    "ping 127.0.0.1 -n 3 >NUL",
                    ":replace_app",
                    f'copy /Y "{source_exe}" "{app_path}.update" >NUL',
                    f'move /Y "{app_path}.update" "{app_path}" >NUL',
                    "if errorlevel 1 (",
                    "  ping 127.0.0.1 -n 2 >NUL",
                    "  goto replace_app",
                    ")",
                    *sidecar_commands,
                    f'start "" /B cmd.exe /D /S /C "ping 127.0.0.1 -n 3 >NUL & rmdir /S /Q ""{staging_dir}"" 2>NUL"',
                ]
            ),
            encoding="utf-8",
        )
        subprocess.Popen(
            ["cmd.exe", "/d", "/s", "/c", str(script_path)],
            cwd=str(app_dir),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
