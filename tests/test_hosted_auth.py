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
    assert r.json()["resource"] == "https://mcp.example.test/mcp"
    path_specific = client.get("/.well-known/oauth-protected-resource/mcp")
    assert path_specific.status_code == 200
    assert path_specific.json() == r.json()
    challenge = rpc(client, token=None)
    assert 'resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource/mcp"' in challenge.headers["www-authenticate"]


def test_discovery_and_challenge_advertise_scopes(auth_app):
    # Claude Code / Desktop only request advertised scopes; with none listed
    # they fall back to the authorization server's read-only defaults.
    from qcdatabase_mcp.auth import DEFAULT_SCOPES
    client, _, _ = auth_app
    expected = DEFAULT_SCOPES.split()
    for path in ("/.well-known/oauth-protected-resource",
                 "/.well-known/oauth-protected-resource/mcp"):
        assert client.get(path).json()["scopes_supported"] == expected
    assert "projects:write" in expected and "trainings:write" in expected
    challenge = rpc(client, token=None).headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert f'scope="{DEFAULT_SCOPES}"' in challenge
    assert challenge.count("scope=") == 1


def test_advertised_scopes_are_not_required(auth_app):
    # A token approved with read access only must still connect: the API
    # enforces per-endpoint scopes, the resource server must not.
    client, state, _ = auth_app
    state["data"]["scopes"] = ["projects:read"]
    assert rpc(client, token="read-only-token").status_code == 200


def test_advertised_scopes_env_override(monkeypatch):
    monkeypatch.setenv("QCDB_MCP_SCOPES", "projects:read, projects:write  bad\"x projects:read")
    assert hosted.advertised_scopes() == ["projects:read", "projects:write"]
    monkeypatch.setenv("QCDB_MCP_SCOPES", "  ")
    from qcdatabase_mcp.auth import DEFAULT_SCOPES
    assert hosted.advertised_scopes() == DEFAULT_SCOPES.split()


def test_resource_identity_matches_endpoint_without_changing_base(monkeypatch):
    monkeypatch.setenv("QCDB_MCP_RESOURCE_URL", "https://mcp.example.test/")
    assert hosted.resource_url() == "https://mcp.example.test"
    assert hosted.mcp_resource_url() == "https://mcp.example.test/mcp"
    access = hosted._access_token_from_whoami("synthetic", {"id": "test-user"})
    assert access.resource == str(hosted.auth_settings().resource_server_url)


def test_hosted_login_does_not_suggest_reconnecting(monkeypatch):
    monkeypatch.setattr(hosted, "hosted_enabled", lambda: True)
    monkeypatch.setattr(server, "client", lambda: object())
    result = server.login()
    assert "You are authenticated" in result
    assert "reconnect" not in result


def test_metadata_authorization_server_matches_issuer_exactly(auth_app):
    # The AS publishes issuer "https://qcdatabase.ai"; pydantic would add a "/".
    client, _, _ = auth_app
    for path in ("/.well-known/oauth-protected-resource",
                 "/.well-known/oauth-protected-resource/mcp"):
        assert client.get(path).json()["authorization_servers"] == [hosted.issuer_url()]
    assert not hosted.issuer_url().endswith("/")


def test_challenge_without_token_has_no_error(auth_app):
    client, state, _ = auth_app
    challenge = rpc(client, token=None).headers["www-authenticate"]
    assert "error=" not in challenge
    assert "resource_metadata=" in challenge and "scope=" in challenge
    state["status"] = 401
    rejected = rpc(client, token="bad-token").headers["www-authenticate"]
    assert 'error="invalid_token"' in rejected
    assert rejected.count("scope=") == 1


def test_cors_preflight_and_401_for_allowed_origin(auth_app):
    client, _, _ = auth_app
    origin = "https://mcp.example.test"
    pre = client.options("/mcp", headers={"Origin": origin,
                                          "Access-Control-Request-Method": "POST",
                                          "Access-Control-Request-Headers": "authorization"})
    assert pre.status_code == 204
    assert pre.headers["access-control-allow-origin"] == origin
    assert "Authorization" in pre.headers["access-control-allow-headers"]
    r = client.post("/mcp", headers={"Origin": origin, "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == origin
    assert "WWW-Authenticate" in r.headers["access-control-expose-headers"]


def test_cors_withheld_from_unlisted_origin(auth_app):
    client, _, _ = auth_app
    pre = client.options("/mcp", headers={"Origin": "https://evil.example",
                                          "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 204
    assert "access-control-allow-origin" not in pre.headers
    r = client.post("/mcp", headers={"Origin": "https://evil.example"}, json={})
    assert "access-control-allow-origin" not in r.headers
