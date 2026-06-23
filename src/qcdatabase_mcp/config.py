"""On-disk configuration: where we keep the login tokens, the registered OAuth
client, and the project the user is currently working in.

Everything lives in one small JSON file in a per-user config directory. Nothing
sensitive is ever written into the project repository.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


APP_DIR_NAME = "qcdatabase-mcp"
STORE_FILENAME = "store.json"


def config_dir() -> Path:
    """Return (and create) the per-user directory we store state in.

    Honours ``QCDB_CONFIG_DIR`` for advanced users / testing, otherwise uses the
    conventional location for the platform.
    """
    override = os.environ.get("QCDB_CONFIG_DIR")
    if override:
        base = Path(override)
    elif sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        base = Path(root) / APP_DIR_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    else:
        root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        base = Path(root) / APP_DIR_NAME

    base.mkdir(parents=True, exist_ok=True)
    return base


def _store_path() -> Path:
    return config_dir() / STORE_FILENAME


class Store:
    """Tiny JSON-backed key/value store with restrictive file permissions.

    Layout::

        {
            "clients":   { "<redirect_uri>": { "client_id": ... } },
            "token":     { "access_token": ..., "refresh_token": ..., ... },
            "settings":  { "active_project": { "id": ..., "name": ... } }
        }
    """

    def __init__(self) -> None:
        self.path = _store_path()
        self._data: dict[str, Any] = self._read()

    # ----- low level -----------------------------------------------------
    def _read(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self) -> None:
        # Write atomically so a crash mid-write can never corrupt credentials.
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        self._harden_permissions()

    def _harden_permissions(self) -> None:
        if not sys.platform.startswith("win"):
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    # ----- OAuth client registration ------------------------------------
    def get_client(self, redirect_uri: str) -> dict[str, Any] | None:
        return self._data.get("clients", {}).get(redirect_uri)

    def set_client(self, redirect_uri: str, registration: dict[str, Any]) -> None:
        self._data.setdefault("clients", {})[redirect_uri] = registration
        self._write()

    # ----- token --------------------------------------------------------
    def get_token(self) -> dict[str, Any] | None:
        return self._data.get("token")

    def set_token(self, token: dict[str, Any]) -> None:
        self._data["token"] = token
        self._write()

    def clear_token(self) -> None:
        self._data.pop("token", None)
        self._write()

    # ----- settings (active project) ------------------------------------
    def get_active_project(self) -> dict[str, Any] | None:
        return self._data.get("settings", {}).get("active_project")

    def set_active_project(self, project: dict[str, Any] | None) -> None:
        settings = self._data.setdefault("settings", {})
        if project is None:
            settings.pop("active_project", None)
        else:
            settings["active_project"] = project
        self._write()
