from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .config import EscapeStreakSettings
from .secure_config import decrypt_server_url


STREAK_CONFIG_FILE = "streak_config.json"


class StreakSyncError(RuntimeError):
    pass


class StreakSyncClient:
    def __init__(self, server_url: str, timeout: float = 4.0) -> None:
        self.server_url = server_url.strip().rstrip("/")
        self.timeout = timeout
        if not self.server_url:
            raise StreakSyncError("Enter a streak sync server URL first.")

    def create_lobby(self, player_id: str, player_tag: str, state: EscapeStreakSettings) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/lobbies",
            {
                "player_id": player_id,
                "player_tag": self._clean_tag(player_tag),
                "state": self._state_payload(state),
            },
        )

    def check_player_tag(self, tag: str) -> dict[str, Any]:
        return self._request("POST", "/api/players/check", {"tag": self._clean_tag(tag)})

    def register_player_tag(self, tag: str, player_id: str, state: EscapeStreakSettings) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/players/register",
            {
                "tag": self._clean_tag(tag),
                "player_id": player_id,
                "state": self._state_payload(state),
            },
        )

    def fetch_player_tag(self, tag: str) -> dict[str, Any]:
        return self._request("GET", f"/api/players/{quote(self._clean_tag(tag), safe='')}")

    def push_player_state(self, tag: str, player_id: str, state: EscapeStreakSettings) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/api/players/{quote(self._clean_tag(tag), safe='')}",
            {
                "player_id": player_id,
                "state": self._state_payload(state),
            },
        )

    def join_lobby(self, code: str, player_id: str, player_tag: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/lobbies/{self._clean_code(code)}/join",
            {"player_id": player_id, "player_tag": self._clean_tag(player_tag)},
        )

    def leave_lobby(self, code: str, player_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/lobbies/{self._clean_code(code)}/leave",
            {"player_id": player_id},
        )

    def fetch_lobby(self, code: str) -> dict[str, Any]:
        return self._request("GET", f"/api/lobbies/{self._clean_code(code)}")

    def push_state(self, code: str, player_id: str, state: EscapeStreakSettings) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/api/lobbies/{self._clean_code(code)}",
            {
                "player_id": player_id,
                "state": self._state_payload(state),
            },
        )

    @staticmethod
    def _clean_code(code: str) -> str:
        cleaned = "".join(ch for ch in code.upper().strip() if ch.isalnum() or ch == "-")
        if not cleaned:
            raise StreakSyncError("Enter a lobby code first.")
        return cleaned

    @staticmethod
    def _clean_tag(tag: str) -> str:
        cleaned = tag.strip()
        if "#" not in cleaned:
            raise StreakSyncError("Could not create the hidden player identity. Restart the app and try again.")
        return cleaned

    @staticmethod
    def _state_payload(state: EscapeStreakSettings) -> dict[str, Any]:
        payload = asdict(state)
        payload["players"] = payload.get("players", [])[:4]
        return payload

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = requests.request(
                method,
                f"{self.server_url}{path}",
                json=payload,
                timeout=self.timeout,
                headers={"Accept": "application/json"},
            )
        except requests.RequestException as exc:
            raise StreakSyncError(f"Could not reach streak sync server: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise StreakSyncError("Streak sync server returned an invalid response.") from exc

        if response.status_code >= 400:
            raise StreakSyncError(str(data.get("error", f"Sync request failed ({response.status_code}).")))
        return data


def load_packaged_streak_server_url(root: Path, bundle_root: Path | None = None) -> str:
    paths = [root / STREAK_CONFIG_FILE]
    if bundle_root and bundle_root != root:
        paths.append(bundle_root / STREAK_CONFIG_FILE)
    for path in paths:
        if not path.exists():
            continue
        return decrypt_server_url(path.read_text(encoding="utf-8"))
    return ""
