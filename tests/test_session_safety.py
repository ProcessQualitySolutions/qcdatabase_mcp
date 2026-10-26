"""Missing local session safeguards, adapted to the upstream store interface."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest

from qcdatabase_mcp import auth
from qcdatabase_mcp.config import SessionChangedError, Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("QCDB_CONFIG_DIR", str(tmp_path))
    s = Store()
    s.set_token({"access_token": "old", "refresh_token": "rt", "client_id": "cid",
                 "expires_at": 0, "scope": "projects:read", "tenant": "tenant"})
    return s


def test_new_login_clears_project_even_for_same_tenant(store):
    stale = Store()
    stale.set_active_project({"id": "old-project"})
    store.set_token({"access_token": "other-user"}, new_session=True)
    with pytest.raises(SessionChangedError):
        stale.get_token()
    assert stale.get_active_project() is None
    assert Store().get_active_project() is None


def test_token_access_wraps_login_change_as_auth_error(store):
    stale = Store()
    store.set_token({"access_token": "other-user"}, new_session=True)
    with pytest.raises(auth.AuthError, match="login changed"):
        auth.ensure_access_token(stale, 8765)


def test_logout_invalidates_other_project_pins(store):
    stale = Store()
    stale.set_active_project({"id": "old-project"})
    store.clear_token()
    assert stale.get_active_project() is None
    assert Store().get_active_project() is None


def test_project_values_cannot_be_mutated_outside_store(store):
    project = {"id": "p"}
    store.set_active_project(project)
    project["id"] = "changed"
    returned = store.get_active_project()
    returned["id"] = "also-changed"
    assert store.get_active_project() == {"id": "p"}


def test_corrupt_credentials_are_not_silently_overwritten(store):
    store.path.write_text("not json")
    with pytest.raises(json.JSONDecodeError):
        store.set_active_project({"id": "p"})
    assert store.path.read_text() == "not json"


def test_refresh_preserves_omitted_scope_and_tenant(store, monkeypatch):
    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: httpx.Response(
        200, json={"access_token": "new", "expires_in": 3600}))
    result = auth.refresh(store, 8765)
    assert result["scope"] == "projects:read"
    assert result["tenant"] == "tenant"
    assert result["refresh_token"] == "rt"


def test_changed_but_expired_token_is_refreshed(store, monkeypatch):
    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: httpx.Response(
        200, json={"access_token": "new", "expires_in": 3600}))
    assert auth.refresh(store, 8765, rejected="older")["access_token"] == "new"


def test_registration_serialized_across_store_instances(store, monkeypatch):
    start = Barrier(4)
    calls = []
    def post(*a, **k):
        calls.append(k["json"])
        time.sleep(0.02)
        return httpx.Response(201, json={"client_id": "registered"})
    monkeypatch.setattr(auth.httpx, "post", post)
    def register(_):
        s = Store()
        start.wait(timeout=5)
        return auth._register_client(s, auth._redirect_uri(8765))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(register, range(4)))
    assert len(calls) == 1
    assert all(r["client_id"] == "registered" for r in results)
