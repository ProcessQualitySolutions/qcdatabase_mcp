"""Exercise actual MCP HTTP auth middleware without live credentials."""

import logging

import httpx
import pytest
from starlette.testclient import TestClient

from qcdatabase_mcp import hosted, server
from qcdatabase_mcp.hosted_transport import HostedFastMCP


@pytest.fixture
def auth_app(monkeypatch):
    monkeypatch.setenv("QCDB_MCP_RESOURCE_URL", "https://mcp.example.test")
    state = {"status": 200, "data": {"id": "user-test", "tenant": {"id": "tenant-test"}},
             "calls": 0}

    def respond(request):
        state["calls"] += 1
        if state.get("timeout"):
            raise httpx.ReadTimeout("SENSITIVE", request=request)
        if state.get("invalid_json"):
            return httpx.Response(200, text="SENSITIVE")
        return httpx.Response(state["status"], json=state["data"])

    verifier = hosted.QCDBTokenVerifier(cache_ttl=0, neg_cache_ttl=5)
    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(respond),
                                base_url="https://api.example.test")
    verifier._http = mock_http
    app = HostedFastMCP("auth-test", token_verifier=verifier,
                        auth=hosted.auth_settings(), transport_security=hosted.transport_security(),
                        stateless_http=True,
                        json_response=True)

    @app.tool()
    def identity():
        from mcp.server.auth.middleware.auth_context import get_access_token
        return get_access_token().subject

    with TestClient(app.streamable_http_app(), base_url="https://mcp.example.test") as client:
        yield client, state, verifier


def rpc(client, method="tools/list", params=None, token="test-token"):
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    return client.post("/mcp", headers=headers,
                       json={"jsonrpc": "2.0", "id": 1, "method": method,
                             "params": params or {}})


def test_valid_token_initialize_discover_read(auth_app):
    client, state, _ = auth_app
    r = rpc(client, "initialize", {"protocolVersion": "2025-03-26",
             "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
    assert r.status_code == 200
    assert "serverInfo" in r.json()["result"]
    assert rpc(client).json()["result"]["tools"][0]["name"] == "identity"
    r = rpc(client, "tools/call", {"name": "identity", "arguments": {}})
    assert "tenant-test:user-test" in r.text
    # A fresh credential can preserve identity across client-side token refresh.
    assert rpc(client, token="refreshed-token").status_code == 200
    state["data"]["tenant"]["id"] = "other-tenant"
    assert "other-tenant:user-test" in rpc(
        client, "tools/call", {"name": "identity"}, token="other-token").text


def test_missing_and_rejected_tokens(auth_app):
    client, state, verifier = auth_app
    assert rpc(client, token=None).status_code == 401
    assert state["calls"] == 0
    state["status"] = 401
    for token in ("invalid-token", "expired-token", "revoked-token"):
        r = rpc(client, token=token)
        assert r.status_code == 401
        assert "invalid_token" in r.headers["www-authenticate"]
    count = state["calls"]
    assert rpc(client, token="revoked-token").status_code == 401
    assert state["calls"] == count
    key = verifier._cache_key("revoked-token")
    verifier._cache[key] = (0, None)
    state["status"] = 200
    assert rpc(client, token="revoked-token").status_code == 200


@pytest.mark.parametrize("status", [400, 403, 404, 429, 500, 502, 503])
def test_upstream_failure_is_not_reauth_and_recovers(auth_app, status):
    client, state, verifier = auth_app
    state["status"] = status
    r = rpc(client)
    assert r.status_code == (403 if status == 403 else 503)
    assert "www-authenticate" not in r.headers
    assert not verifier._cache
    state["status"] = 200
    assert rpc(client).status_code == 200


@pytest.mark.parametrize("failure", ["timeout", "invalid_json", "identity"])
def test_malformed_or_unavailable_identity_fails_closed(auth_app, failure, caplog):
    client, state, verifier = auth_app
    caplog.set_level(logging.INFO)
    if failure == "identity":
        state["data"] = {"email": "SENSITIVE"}
    else:
        state[failure] = True
    r = rpc(client, "tools/call", {"name": "identity"}, token="SECRET-CREDENTIAL")
    assert r.status_code == 503
    assert "www-authenticate" not in r.headers
    assert not verifier._cache
    assert "SENSITIVE" not in caplog.text
    assert "SECRET-CREDENTIAL" not in caplog.text
    assert "user-test" not in r.text


def test_discovery_resource(auth_app):
    client, _, _ = auth_app
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    assert r.json()["resource"] == "https://mcp.example.test/"


def test_hosted_login_does_not_suggest_reconnecting(monkeypatch):
    monkeypatch.setattr(hosted, "hosted_enabled", lambda: True)
    monkeypatch.setattr(server, "client", lambda: object())
    result = server.login()
    assert "You are authenticated" in result
    assert "reconnect" not in result
