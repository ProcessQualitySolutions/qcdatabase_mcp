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
import threading
import time
import uuid
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, Callable, Iterator

if sys.platform.startswith("win"):
    import msvcrt
else:
    import fcntl


APP_DIR_NAME = "qcdatabase-mcp"
STORE_FILENAME = "store.json"


class SessionChangedError(RuntimeError):
    """A different login invalidated this process's active project."""


def config_dir(create: bool = True) -> Path:
    """Return the per-user directory we store state in.

    Honours ``QCDB_CONFIG_DIR`` for advanced users / testing, otherwise uses the
    conventional location for the platform. This directory is intentionally
    outside any project/repository checkout so tokens can never be committed.

    Pass ``create=False`` to only compute the path (e.g. for a safety check that
    must not have the side effect of creating the directory).
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

    if create:
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

    Several stdio servers (one per AI session) share this file, so nothing is
    cached in memory: every read goes to disk, and every write re-reads the file
    under a cross-process lock and changes only its own key. The one exception is
    the active project, which is per process (see :meth:`set_active_project`).
    """

    def __init__(self) -> None:
        self.path = _store_path().resolve()
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        # A new session starts on the last project chosen in any session; after
        # that, only this process's own choice applies.
        data = self._read()
        self._session_id = data.get("session_id")
        self._active_project = data.get("settings", {}).get("active_project")

    def _sync_session(self, data: dict[str, Any]) -> bool:
        if self._session_id == data.get("session_id"):
            return False
        self._session_id = data.get("session_id")
        self._active_project = None
        return True

    # ----- locking ------------------------------------------------------
    @contextmanager
    def lock(self) -> Iterator[None]:
        """Hold the store exclusively, across processes and threads.

        Re-entrant within a thread, so a caller can wrap a read-modify-write
        sequence (e.g. a token refresh) around the store's own locked methods.
        """
        state = _lock_state(self._lock_path)
        with state.thread_lock:
            if state.depth == 0:
                state.fh = _acquire_file_lock(self._lock_path)
            state.depth += 1
            try:
                yield
            finally:
                state.depth -= 1
                if state.depth == 0:
                    _release_file_lock(state.fh)
                    state.fh = None

    # ----- low level -----------------------------------------------------
    def _read(self) -> dict[str, Any]:
        with self.lock():
            try:
                with self.path.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
            except FileNotFoundError:
                return {}

    def _update(self, change: Callable[[dict[str, Any]], None]) -> None:
        with self.lock():
            data = self._read()
            change(data)
            self._write(data)

    def _write(self, data: dict[str, Any]) -> None:
        # Write atomically so a crash mid-write can never corrupt credentials.
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
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
        return self._read().get("clients", {}).get(redirect_uri)

    def set_client(self, redirect_uri: str, registration: dict[str, Any]) -> None:
        def change(d: dict[str, Any]) -> None:
            d.setdefault("clients", {})[redirect_uri] = registration
        self._update(change)

    # ----- token --------------------------------------------------------
    def get_token(self) -> dict[str, Any] | None:
        with self.lock():
            data = self._read()
            if self._sync_session(data):
                raise SessionChangedError(
                    "The saved login changed in another process. The active project "
                    "was cleared; select a project again before retrying."
                )
            return data.get("token")

    def set_token(self, token: dict[str, Any], *, new_session: bool = False) -> None:
        def change(d: dict[str, Any]) -> None:
            if new_session:
                d["session_id"] = uuid.uuid4().hex
                d.setdefault("settings", {}).pop("active_project", None)
                self._sync_session(d)
            d["token"] = token
        self._update(change)

    def clear_token(self, if_access_token: str | None = None) -> None:
        """Forget the stored token.

        With ``if_access_token``, only forget it if it is still that token: a
        session whose token was rejected must not wipe a newer one that another
        session has stored since.
        """
        def change(d: dict[str, Any]) -> None:
            current = d.get("token") or {}
            if if_access_token is None or current.get("access_token") == if_access_token:
                d.pop("token", None)
                d["session_id"] = uuid.uuid4().hex
                d.setdefault("settings", {}).pop("active_project", None)
                self._sync_session(d)
        self._update(change)

    # ----- settings (active project) ------------------------------------
    def get_active_project(self) -> dict[str, Any] | None:
        with self.lock():
            self._sync_session(self._read())
            return deepcopy(self._active_project)

    def set_active_project(self, project: dict[str, Any] | None) -> None:
        """Switch this process's project and remember it for new sessions.

        Other running sessions keep their own project: writes go to whatever
        project is active, so one session must never retarget another.
        """
        def change(d: dict[str, Any]) -> None:
            self._sync_session(d)
            self._active_project = deepcopy(project)
            settings = d.setdefault("settings", {})
            if project is None:
                settings.pop("active_project", None)
            else:
                settings["active_project"] = project
        self._update(change)


# ---------------------------------------------------------------------------
# Cross-process file lock
# ---------------------------------------------------------------------------
_LOCK_TIMEOUT = 60.0


class _LockState:
    def __init__(self) -> None:
        self.thread_lock = threading.RLock()
        self.depth = 0
        self.fh: IO[bytes] | None = None


_LOCK_STATES: dict[tuple[int, str], _LockState] = {}
_LOCK_STATES_GUARD = threading.Lock()


def _lock_state(path: Path) -> _LockState:
    with _LOCK_STATES_GUARD:
        return _LOCK_STATES.setdefault((os.getpid(), str(path.resolve())), _LockState())


def _acquire_file_lock(path: Path) -> IO[bytes]:
    fh = open(path, "a+b")
    if sys.platform.startswith("win") and os.fstat(fh.fileno()).st_size == 0:
        fh.write(b"\0")
        fh.flush()
    deadline = time.monotonic() + _LOCK_TIMEOUT
    while True:
        try:
            if sys.platform.startswith("win"):
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError:
            if time.monotonic() >= deadline:
                fh.close()
                raise TimeoutError(f"Timed out waiting for the lock on {path}") from None
            time.sleep(0.05)


def _release_file_lock(fh: IO[bytes] | None) -> None:
    if fh is None:
        return
    try:
        if sys.platform.startswith("win"):
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()
