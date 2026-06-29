from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import requests


class StreakManagerApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("DBD Streak Manager")
        self.root.geometry("980x640")
        self.root.minsize(860, 560)
        self.root.configure(bg="#111318")
        self.lobbies: dict[str, dict] = {}
        self.selected_code = ""

        self.server_var = tk.StringVar(value=os.environ.get("DBD_STREAK_SYNC_URL", ""))
        self.token_var = tk.StringVar(value=os.environ.get("DBD_STREAK_ADMIN_TOKEN", ""))
        self.status_var = tk.StringVar(value="Ready.")
        self.streak_var = tk.IntVar(value=0)

        self._build_ui()

    def run(self) -> None:
        self.root.mainloop()

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#1d2128", foreground="#f7f2ea", fieldbackground="#1d2128", rowheight=28)
        style.configure("Treeview.Heading", background="#2a2f38", foreground="#f7f2ea")

        header = tk.Frame(self.root, bg="#111318")
        header.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(header, text="DBD Streak Manager", font=("Segoe UI Semibold", 22), fg="#f7f2ea", bg="#111318").pack(anchor="w")
        tk.Label(header, text="Private admin tool for shared Escape Streak lobbies", fg="#c72435", bg="#111318").pack(anchor="w")

        connection = tk.Frame(self.root, bg="#111318")
        connection.pack(fill="x", padx=18, pady=8)
        tk.Label(connection, text="Worker URL", fg="#c7c0b7", bg="#111318").grid(row=0, column=0, sticky="w")
        tk.Entry(connection, textvariable=self.server_var, bg="#2a2f38", fg="#f7f2ea", insertbackground="#f7f2ea", relief="flat").grid(
            row=1, column=0, sticky="ew", ipady=7, padx=(0, 10)
        )
        tk.Label(connection, text="Admin token", fg="#c7c0b7", bg="#111318").grid(row=0, column=1, sticky="w")
        tk.Entry(
            connection,
            textvariable=self.token_var,
            show="*",
            bg="#2a2f38",
            fg="#f7f2ea",
            insertbackground="#f7f2ea",
            relief="flat",
        ).grid(row=1, column=1, sticky="ew", ipady=7, padx=(0, 10))
        tk.Button(connection, text="Refresh", command=self.refresh, bg="#b51f2c", fg="#f7f2ea", relief="flat", padx=18, pady=7).grid(
            row=1, column=2, sticky="ew"
        )
        connection.grid_columnconfigure(0, weight=2)
        connection.grid_columnconfigure(1, weight=1)

        content = tk.PanedWindow(self.root, orient="horizontal", bg="#111318", sashwidth=6)
        content.pack(fill="both", expand=True, padx=18, pady=8)

        left = tk.Frame(content, bg="#15181f")
        right = tk.Frame(content, bg="#15181f")
        content.add(left, minsize=420)
        content.add(right, minsize=360)

        tk.Label(left, text="Lobbies", font=("Segoe UI Semibold", 14), fg="#f7f2ea", bg="#15181f").pack(anchor="w", padx=12, pady=(12, 6))
        self.lobby_tree = ttk.Treeview(left, columns=("streak", "players", "updated"), show="headings")
        self.lobby_tree.heading("streak", text="Streak")
        self.lobby_tree.heading("players", text="Players")
        self.lobby_tree.heading("updated", text="Updated")
        self.lobby_tree.column("streak", width=80, anchor="center")
        self.lobby_tree.column("players", width=80, anchor="center")
        self.lobby_tree.column("updated", width=180)
        self.lobby_tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.lobby_tree.bind("<<TreeviewSelect>>", lambda _event: self._select_lobby())

        tk.Label(right, text="Selected Lobby", font=("Segoe UI Semibold", 14), fg="#f7f2ea", bg="#15181f").pack(anchor="w", padx=12, pady=(12, 6))
        self.detail_text = tk.Text(right, bg="#0e1015", fg="#f7f2ea", insertbackground="#f7f2ea", relief="flat", height=16)
        self.detail_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        controls = tk.Frame(right, bg="#15181f")
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Label(controls, text="Set streak", fg="#c7c0b7", bg="#15181f").grid(row=0, column=0, sticky="w")
        tk.Spinbox(controls, from_=0, to=999, textvariable=self.streak_var, width=8, bg="#2a2f38", fg="#f7f2ea").grid(
            row=1, column=0, sticky="ew", padx=(0, 8)
        )
        tk.Button(controls, text="Apply", command=self.set_streak, bg="#b51f2c", fg="#f7f2ea", relief="flat", padx=16, pady=7).grid(
            row=1, column=1, padx=(0, 8)
        )
        tk.Button(controls, text="Reset", command=self.reset_lobby, bg="#2a2f38", fg="#f7f2ea", relief="flat", padx=16, pady=7).grid(
            row=1, column=2, padx=(0, 8)
        )
        tk.Button(controls, text="Delete", command=self.delete_lobby, bg="#68151c", fg="#f7f2ea", relief="flat", padx=16, pady=7).grid(row=1, column=3)
        controls.grid_columnconfigure(0, weight=1)

        tk.Label(self.root, textvariable=self.status_var, fg="#c7c0b7", bg="#111318").pack(fill="x", padx=18, pady=(0, 12), anchor="w")

    def refresh(self) -> None:
        self._run("Loading lobbies...", self._fetch_lobbies)

    def set_streak(self) -> None:
        if not self._require_selection():
            return
        self._run(
            "Updating streak...",
            lambda: self._request("PUT", f"/api/admin/lobbies/{self.selected_code}", {"streak": self.streak_var.get()}),
            refresh_after=True,
        )

    def reset_lobby(self) -> None:
        if not self._require_selection():
            return
        self._run(
            "Resetting lobby...",
            lambda: self._request("PUT", f"/api/admin/lobbies/{self.selected_code}", {"action": "reset"}),
            refresh_after=True,
        )

    def delete_lobby(self) -> None:
        if not self._require_selection():
            return
        if not messagebox.askyesno("Delete lobby", f"Delete {self.selected_code}?"):
            return
        self._run("Deleting lobby...", lambda: self._request("DELETE", f"/api/admin/lobbies/{self.selected_code}"), refresh_after=True)

    def _fetch_lobbies(self) -> None:
        data = self._request("GET", "/api/admin/lobbies")
        self.lobbies = {item["code"]: item for item in data.get("lobbies", [])}
        self.root.after(0, self._render_lobbies)

    def _render_lobbies(self) -> None:
        self.lobby_tree.delete(*self.lobby_tree.get_children())
        for code, lobby in sorted(self.lobbies.items()):
            state = lobby.get("state", {})
            self.lobby_tree.insert(
                "",
                "end",
                iid=code,
                text=code,
                values=(state.get("streak", 0), len(lobby.get("members", [])), lobby.get("updated_at", "")),
            )
        self.status_var.set(f"Loaded {len(self.lobbies)} lobby/lobbies.")

    def _select_lobby(self) -> None:
        selection = self.lobby_tree.selection()
        if not selection:
            return
        self.selected_code = selection[0]
        lobby = self.lobbies.get(self.selected_code, {})
        self.streak_var.set(int(lobby.get("state", {}).get("streak", 0)))
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", self._format_lobby(lobby))

    def _format_lobby(self, lobby: dict) -> str:
        state = lobby.get("state", {})
        lines = [
            f"Code: {lobby.get('code', '')}",
            f"Streak: {state.get('streak', 0)}",
            f"Revision: {state.get('sync_revision', 0)}",
            f"Created: {lobby.get('created_at', '')}",
            f"Updated: {lobby.get('updated_at', '')}",
            "",
            "Players:",
        ]
        for index, player in enumerate(state.get("players", []), start=1):
            lines.append(f"  {index}. {player.get('name', '') or 'Unnamed'} - {player.get('status', 'Ready')}")
        lines.append("")
        lines.append("Members:")
        for member in lobby.get("members", []):
            lines.append(f"  {member.get('name', '') or member.get('player_id', '')} - {member.get('last_seen', '')}")
        return "\n".join(lines)

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        server = self.server_var.get().strip().rstrip("/")
        token = self.token_var.get().strip()
        if not server or not token:
            raise RuntimeError("Enter both Worker URL and admin token.")
        response = requests.request(
            method,
            f"{server}{path}",
            json=payload,
            timeout=8,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("Worker returned a non-JSON response.") from exc
        if response.status_code >= 400:
            raise RuntimeError(data.get("error", f"Request failed ({response.status_code})."))
        return data

    def _run(self, message: str, action, refresh_after: bool = False) -> None:
        self.status_var.set(message)

        def worker() -> None:
            try:
                action()
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._show_error(error))
                return
            if refresh_after:
                self.root.after(0, self.refresh)

        threading.Thread(target=worker, daemon=True).start()

    def _show_error(self, error: Exception) -> None:
        self.status_var.set(str(error))
        messagebox.showerror("Streak manager", str(error))

    def _require_selection(self) -> bool:
        if self.selected_code:
            return True
        messagebox.showwarning("Streak manager", "Select a lobby first.")
        return False


if __name__ == "__main__":
    StreakManagerApp().run()
