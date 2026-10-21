"""Several stdio servers (one per AI session) share one store.json.

These cover the ways they used to trip over each other: a stale in-memory copy
overwriting a newer token, two sessions spending the same rotating refresh
token (logging both out), a rejected token wiping a newer one, and one
session's project switch retargeting another's writes.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from qcdatabase_mcp import auth
from qcdatabase_mcp import client as clientmod
from qcdatabase_mcp.client import QCClient
from qcdatabase_mcp.config import Store


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("QCDB_CONFIG_DIR", str(tmp_path))
    return tmp_path


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


class _RotatingTokenServer:
    """Token endpoint that rotates refresh tokens: each one works exactly once."""

    def __init__(self, live="rt0"):
        self.live = live
        self.calls = 0
        self._n = 0
        self._guard = threading.Lock()

    def post(self, url, data=None, json=None, timeout=None):
        time.sleep(0.05)  # widen the race window
        with self._guard:
            self.calls += 1
            if data.get("refresh_token") != self.live:
                return _Resp(400, {"error": "invalid_grant"})
            self._n += 1
            self.live = f"rt{self._n}"
            return _Resp(200, {"access_token": f"at{self._n}", "refresh_token": self.live,
                               "expires_in": 3600})


def _seed(store, access="at0", refresh="rt0", expires_at=0.0):
    store.set_client(auth._redirect_uri(8765), {"client_id": "cid"})
    store.set_token({"access_token": access, "refresh_token": refresh,
                     "expires_at": expires_at, "client_id": "cid"})


def test_a_stale_session_never_overwrites_a_newer_token(store_dir):
    a, b = Store(), Store()  # b opened before a's token existed
    a.set_token({"access_token": "fresh", "refresh_token": "rt"})
    b.set_active_project({"id": "P2", "name": "Two"})  # used to dump b's stale copy
    assert Store().get_token()["access_token"] == "fresh"


def test_active_project_is_per_process(store_dir):
    a = Store()
    a.set_active_project({"id": "P1", "name": "One"})
    b = Store()
    assert b.get_active_project()["id"] == "P1"  # a new session starts on the last choice
    b.set_active_project({"id": "P2", "name": "Two"})
    assert a.get_active_project()["id"] == "P1"  # a running session is never retargeted
    assert Store().get_active_project()["id"] == "P2"


def test_conditional_clear_spares_a_newer_token(store_dir):
    s = Store()
    s.set_token({"access_token": "new", "refresh_token": "rt1"})
    s.clear_token(if_access_token="old")
    assert s.get_token()["access_token"] == "new"
    s.clear_token(if_access_token="new")
    assert s.get_token() is None


def test_refresh_reuses_a_token_another_session_already_refreshed(store_dir, monkeypatch):
    _seed(Store(), access="at1", refresh="rt1", expires_at=time.time() + 600)

    def no_network(*_a, **_k):
        raise AssertionError("must not refresh: a newer token is already stored")
    monkeypatch.setattr(auth.httpx, "post", no_network)

    # This session sent at0 and got a 401; the store already holds at1.
    assert auth.refresh(Store(), 8765, rejected="at0")["access_token"] == "at1"


def test_refresh_replaces_a_rejected_token_even_if_unexpired(store_dir, monkeypatch):
    s = Store()
    _seed(s, expires_at=time.time() + 600)
    server = _RotatingTokenServer(live="rt0")
    monkeypatch.setattr(auth.httpx, "post", server.post)
    assert auth.refresh(s, 8765, rejected="at0")["access_token"] == "at1"
    assert server.calls == 1


def test_concurrent_refresh_refreshes_once_and_nobody_is_logged_out(store_dir, monkeypatch):
    _seed(Store())  # expired access token, live refresh token rt0
    server = _RotatingTokenServer(live="rt0")
    monkeypatch.setattr(auth.httpx, "post", server.post)

    stores = [Store() for _ in range(4)]
    results, errors = [], []

    def worker(s):
        try:
            results.append(auth.ensure_access_token(s, 8765))
        except Exception as exc:  # pragma: no cover - the failure being guarded
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert server.calls == 1
    assert set(results) == {"at1"}
    assert Store().get_token()["refresh_token"] == "rt1"


def test_refresh_uses_the_client_that_issued_the_token(store_dir, monkeypatch):
    s = Store()
    _seed(s)
    # A re-registration whose login never finished replaced the registration.
    s.set_client(auth._redirect_uri(8765), {"client_id": "new-cid"})
    seen = []

    def fake_post(url, data=None, timeout=None):
        seen.append(data["client_id"])
        return _Resp(200, {"access_token": "at1", "refresh_token": "rt1"})
    monkeypatch.setattr(auth.httpx, "post", fake_post)

    auth.refresh(s, 8765)
    assert seen == ["cid"]
    assert s.get_token()["client_id"] == "cid"


def test_a_401_does_not_clear_a_token_another_session_stored(store_dir, monkeypatch):
    s = Store()
    _seed(s, expires_at=time.time() + 600)

    def other_session_refreshes(store, port, rejected=None):
        store.set_token({"access_token": "at1", "refresh_token": "rt1",
                         "expires_at": time.time() + 600})

    monkeypatch.setattr(clientmod, "refresh", other_session_refreshes)
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)),
                        base_url="https://qcdatabase.ai")
    c = QCClient(store=s, http=http)
    with pytest.raises(auth.AuthError):
        c.get("/api/x/")  # at0 -> 401, refresh, at1 -> 401 -> clear only at1
    assert s.get_token() is None

    _seed(s, expires_at=time.time() + 600)
    sends = []

    def handler(request):
        sends.append(request.headers["Authorization"])
        if len(sends) == 2:  # another session swaps the token mid-retry
            s.set_token({"access_token": "at2", "refresh_token": "rt2",
                         "expires_at": time.time() + 600})
        return httpx.Response(401)

    monkeypatch.setattr(clientmod, "refresh", lambda store, port, **kw: None)
    c = QCClient(store=s, http=httpx.Client(transport=httpx.MockTransport(handler),
                                            base_url="https://qcdatabase.ai"))
    with pytest.raises(auth.AuthError):
        c.get("/api/x/")
    assert s.get_token()["access_token"] == "at2"  # the rejected at0 was not it


def test_registration_is_replaced_once_when_scopes_change(store_dir, monkeypatch):
    s = Store()
    uri = auth._redirect_uri(8765)
    s.set_client(uri, {"client_id": "old", "scope": "lists:read"})  # before lists:write
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        # The server may trim what it grants; that must not cause re-registration.
        return _Resp(201, {"client_id": "new", "scope": "lists:read"})
    monkeypatch.setattr(auth.httpx, "post", fake_post)

    assert auth._register_client(s, uri)["client_id"] == "new"
    assert "lists:write" in calls[0]["scope"].split()
    assert auth._register_client(s, uri)["client_id"] == "new"
    assert len(calls) == 1


_WRITER = """
import sys
from qcdatabase_mcp.config import Store
s = Store()
for i in range(40):
    s.set_client(f"uri-{sys.argv[1]}-{i}", {"client_id": str(i)})
"""


def test_separate_processes_writing_at_once_lose_nothing(store_dir):
    src = str(Path(auth.__file__).resolve().parents[1])
    procs = [
        subprocess.Popen([sys.executable, "-c", _WRITER, str(n)],
                         env={**os.environ, "PYTHONPATH": src},
                         stderr=subprocess.PIPE, text=True)
        for n in range(4)
    ]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, err  # e.g. PermissionError from os.replace on Windows
    clients = Store()._read()["clients"]
    assert len(clients) == 4 * 40
