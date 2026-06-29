from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import platform
import sys
import tkinter as tk
from tkinter import messagebox
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import winreg

from .secure_config import decrypt_server_url

LICENSE_CONFIG_FILE = "license_config.json"
LICENSE_STATE_FILE = "license.json"


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def protect_secret(value: str) -> str:
    raw = value.encode("utf-8")
    raw_buffer = ctypes.create_string_buffer(raw)
    input_blob = DataBlob(len(raw), ctypes.cast(raw_buffer, ctypes.POINTER(ctypes.c_byte)))
    output_blob = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "DBD Companion Overlay License",
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)


def unprotect_secret(value: str) -> str:
    encrypted = base64.b64decode(value)
    encrypted_buffer = ctypes.create_string_buffer(encrypted)
    input_blob = DataBlob(len(encrypted), ctypes.cast(encrypted_buffer, ctypes.POINTER(ctypes.c_byte)))
    output_blob = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)


def _device_id() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            machine_id = str(winreg.QueryValueEx(key, "MachineGuid")[0])
    except OSError:
        machine_id = platform.node()
    return hashlib.sha256(f"dbd-companion-overlay:{machine_id}".encode("utf-8")).hexdigest()


class LicenseStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_path = root / "config" / LICENSE_STATE_FILE
        self.server_config_path = root / LICENSE_CONFIG_FILE

    def server_url(self) -> str:
        config_paths = [self.server_config_path]
        bundled_dir = getattr(sys, "_MEIPASS", "")
        if bundled_dir:
            config_paths.append(Path(bundled_dir) / LICENSE_CONFIG_FILE)
        for config_path in config_paths:
            try:
                url = decrypt_server_url(config_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            return url
        raise RuntimeError("The activation service configuration is missing. Reinstall the app package.")

    def load_key(self) -> str:
        try:
            payload = self._load_state()
            return unprotect_secret(str(payload["license_key_dpapi"]))
        except Exception:
            return ""

    def load_details(self) -> dict:
        payload = self._load_state()
        return {
            "license_key": self.load_key(),
            "plan": str(payload.get("plan", "")),
            "expires_at": payload.get("expires_at"),
            "max_devices": int(payload.get("max_devices", 0) or 0),
            "used_devices": int(payload.get("used_devices", 0) or 0),
        }

    def save_activation(self, license_key: str, payload: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "license_key_dpapi": protect_secret(license_key),
                    "plan": str(payload.get("plan", "")),
                    "expires_at": payload.get("expires_at"),
                    "max_devices": int(payload.get("max_devices", 0) or 0),
                    "used_devices": int(payload.get("used_devices", 0) or 0),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _load_state(self) -> dict:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}


def activate_license(store: LicenseStore, license_key: str, app_version: str) -> dict:
    request = Request(
        f"{store.server_url()}/activate",
        data=json.dumps(
            {
                "license_key": license_key.strip(),
                "device_id": _device_id(),
                "app_version": app_version,
            }
        ).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "DBDCompanionOverlay"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            message = payload.get("error", str(exc))
        except Exception:
            message = str(exc)
        raise RuntimeError(message) from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach the activation service: {exc.reason}") from exc
    if not payload.get("valid"):
        raise RuntimeError(payload.get("error", "This license key is not valid."))
    return payload


class LicenseDialog:
    def __init__(self, store: LicenseStore, app_version: str, initial_key: str, initial_message: str) -> None:
        self.store = store
        self.app_version = app_version
        self.valid = False
        self.root = tk.Tk()
        self.root.title("DBD Companion Overlay Activation")
        self.root.geometry("560x350")
        self.root.minsize(520, 330)
        self.root.resizable(False, False)
        self.root.configure(bg="#111318")
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        panel = tk.Frame(self.root, bg="#111318")
        panel.pack(fill="both", expand=True)
        tk.Label(
            panel,
            text="DBD Companion Overlay",
            font=("Segoe UI Semibold", 24),
            fg="#f7f2ea",
            bg="#111318",
        ).pack(anchor="w", padx=28, pady=(28, 2))
        tk.Label(
            panel,
            text="License activation",
            font=("Segoe UI", 14),
            fg="#c72435",
            bg="#111318",
        ).pack(anchor="w", padx=28)
        tk.Label(
            panel,
            text="Enter a valid license key to start the overlay.",
            font=("Segoe UI", 13),
            fg="#c7c0b7",
            bg="#111318",
        ).pack(anchor="w", padx=28, pady=(24, 8))
        self.entry = tk.Entry(
            panel,
            font=("Segoe UI", 13),
            bg="#2a2f38",
            fg="#f7f2ea",
            insertbackground="#f7f2ea",
            relief="flat",
        )
        self.entry.pack(fill="x", padx=28, ipady=9)
        self.entry.insert(0, initial_key)
        self.status = tk.Label(
            panel,
            text=initial_message,
            font=("Segoe UI", 12),
            fg="#d46a73",
            bg="#111318",
            wraplength=490,
            justify="left",
        )
        self.status.pack(anchor="w", padx=28, pady=(10, 0))
        controls = tk.Frame(panel, bg="#111318")
        controls.pack(fill="x", padx=28, pady=(24, 22), side="bottom")
        tk.Button(
            controls,
            text="Activate",
            command=self._activate,
            bg="#b51f2c",
            fg="#f7f2ea",
            activebackground="#d12a39",
            activeforeground="#ffffff",
            relief="flat",
            padx=28,
            pady=8,
        ).pack(side="left")
        tk.Button(
            controls,
            text="Close",
            command=self.root.destroy,
            bg="#2a2f38",
            fg="#f7f2ea",
            activebackground="#3a424f",
            activeforeground="#ffffff",
            relief="flat",
            padx=28,
            pady=8,
        ).pack(side="right")
        self.root.bind("<Return>", lambda _event: self._activate())

    def _activate(self) -> None:
        license_key = self.entry.get().strip()
        if not license_key:
            self.status.configure(text="Enter your license key first.")
            return
        self.status.configure(text="Checking license...")
        self.root.update_idletasks()
        try:
            payload = activate_license(self.store, license_key, self.app_version)
            self.store.save_activation(license_key, payload)
        except Exception as exc:
            self.status.configure(text=str(exc))
            return
        plan = str(payload.get("plan", "active")).replace("_", " ").title()
        messagebox.showinfo("License activated", f"License accepted: {plan}")
        self.valid = True
        self.root.destroy()

    def run(self) -> bool:
        self.entry.focus_set()
        self.root.mainloop()
        return self.valid


def require_valid_license(root: Path, app_version: str) -> bool:
    store = LicenseStore(root)
    stored_key = store.load_key()
    initial_message = ""
    if stored_key:
        try:
            payload = activate_license(store, stored_key, app_version)
            store.save_activation(stored_key, payload)
            return True
        except Exception as exc:
            initial_message = str(exc)
    return LicenseDialog(store, app_version, stored_key, initial_message).run()
