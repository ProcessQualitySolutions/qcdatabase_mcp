"""Security-critical unit tests.

Covers the code the 2026-07-07 review flagged as exactly what regresses quietly:
the filesystem guard, subject extraction / session keying, the token-verifier
cache (positive + negative), the request path-safety check, pagination, the
upload-retry rewind, and refresh error handling.

Run: ``pytest`` (from the repo root, with ``pip install -e .`` or PYTHONPATH=src).
"""

from __future__ import annotations

import asyncio
import io
import os

import httpx
import pytest

from mcp.server.auth.provider import AccessToken

from qcdatabase_mcp import hosted
from qcdatabase_mcp import client as clientmod
from qcdatabase_mcp.client import (
    APIError,
    QCClient,
    _check_api_path,
    _rewind_files,
)


# ---------------------------------------------------------------------------
# H1 / L1 — filesystem guard
# ---------------------------------------------------------------------------
@pytest.fixture()
def stdio_env(monkeypatch):
    monkeypatch.delenv("QCDB_MCP_HTTP", raising=False)


def _guard():
    # Import lazily so the module picks up a stdio env.
    from qcdatabase_mcp import server
    return server


def test_guard_allows_fresh_temp_path(stdio_env, tmp_path):
    server = _guard()
    target = tmp_path / "sub" / "out.pdf"
    safe = server._guard_local_path(target, write=True)
    assert safe == target.resolve()


def test_guard_refuses_overwrite_existing(stdio_env, tmp_path):
    server = _guard()
    existing = tmp_path / "already.pdf"
    existing.write_bytes(b"x")
    with pytest.raises(ValueError):
        server._guard_local_path(existing, write=True)


def test_guard_refuses_package_and_installation(stdio_env):
    server = _guard()
    import mcp as mcp_pkg
    from pathlib import Path

    # Own source tree.
    with pytest.raises(ValueError):
        server._guard_local_path(Path(server.__file__), write=True)
    # A dependency inside site-packages (installation-wide protection, H1).
    dep = Path(mcp_pkg.__file__).parent / "server" / "__init__.py"
    with pytest.raises(ValueError):
        server._guard_local_path(dep, write=True)


def test_guard_refuses_config_dir(stdio_env, monkeypatch, tmp_path):
    monkeypatch.setenv("QCDB_CONFIG_DIR", str(tmp_path / "cfg"))
    server = _guard()
    # Recompute protected roots now that the config dir env is set.
    monkeypatch.setattr(server, "_PROTECTED_ROOTS", server._protected_roots())
    with pytest.raises(ValueError):
        server._guard_local_path(tmp_path / "cfg" / "store.json", write=True)


def test_guard_hosted_refuses_all(monkeypatch, tmp_path):
    monkeypatch.setenv("QCDB_MCP_HTTP", "1")
    server = _guard()
    with pytest.raises(ValueError):
        server._guard_local_path(tmp_path / "anything.pdf", write=True)
    with pytest.raises(ValueError):
        server._guard_local_path(tmp_path / "anything.pdf", write=False)


# ---------------------------------------------------------------------------
# M3 — request path safety
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [
    "/api/documents/projects/abc/search/",
    "/api/mapping/items/1a2b/mark-complete/",
    "/api/whoami/",
])
def test_path_ok(path):
    _check_api_path(path)  # no raise


@pytest.mark.parametrize("path", [
    "/api/documents//",              # empty id segment
    "/api/x/../../etc/passwd",        # traversal
    "/api/x/%2e%2e/y",               # encoded traversal
    "/api/notes/ evil/",             # space
    "http://evil/api",               # absolute
    "/api/docs/x?y=1",               # injected query
    "relative/path/",                # not rooted
])
def test_path_rejected(path):
    with pytest.raises(APIError):
        _check_api_path(path)


# ---------------------------------------------------------------------------
# old-H1 — upload retry rewinds file handles
# ---------------------------------------------------------------------------
def test_rewind_files_seeks_to_zero():
    f = io.BytesIO(b"payload")
    f.read()
    _rewind_files([("file", ("n.pdf", f, "application/pdf"))])
    assert f.tell() == 0 and f.read() == b"payload"


def test_upload_retry_reuploads_full_body(monkeypatch):
    # First send 401, refresh, retry must see the full body (not zero bytes).
    seen = []
    monkeypatch.setattr(clientmod, "refresh", lambda store, port: None)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        status = 401 if len(seen) == 1 else 200
        return httpx.Response(status, json={"ok": True})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://qcdatabase.ai")
    c = QCClient(store=object(), access_token=None, http=http)
    # access_token None => stdio path (retry+refresh). Provide token via store header.
    monkeypatch.setattr(c, "_headers", lambda: {"Authorization": "Bearer t"})
    f = io.BytesIO(b"REAL-BYTES")
    c.upload("/api/documents/projects/p/upload/", files=[("files", ("a.pdf", f, "application/pdf"))])
    assert len(seen) == 2
    assert b"REAL-BYTES" in seen[0] and b"REAL-BYTES" in seen[1]  # retry not empty


# ---------------------------------------------------------------------------
# old-M2 — pagination follows `next`, flags truncation
# ---------------------------------------------------------------------------
def _client_with(handler):
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://qcdatabase.ai")
    c = QCClient(store=object(), access_token="tok", http=http)
    return c


def test_get_all_follows_next():
    def handler(request):
        page = request.url.params.get("page")
        if page == "2":
            return httpx.Response(200, json={"results": [3, 4], "next": None})
        return httpx.Response(200, json={"results": [1, 2],
                                         "next": "https://qcdatabase.ai/api/x/?page=2"})
    c = _client_with(handler)
    assert c.get_all("/api/x/") == [1, 2, 3, 4]
    assert c.last_truncated is False


def test_get_all_flags_truncation(monkeypatch):
    monkeypatch.setattr(clientmod, "_MAX_PAGES", 3)

    def handler(request):
        return httpx.Response(200, json={"results": [1], "next": "https://qcdatabase.ai/api/x/?page=9"})
    c = _client_with(handler)
    out = c.get_all("/api/x/")
    assert out == [1, 1, 1]
    assert c.last_truncated is True


def test_get_all_unwraps_lists_envelope():
    # /api/lists/projects/{pid}/ returns {"lists": [...]}; each list keeps its id.
    def handler(request):
        return httpx.Response(200, json={"lists": [
            {"id": "a1", "name": "Welders"},
            {"id": "b2", "name": "Materials"},
        ]})
    c = _client_with(handler)
    out = c.get_all("/api/lists/projects/p1/")
    assert [x["id"] for x in out] == ["a1", "b2"]


def test_get_all_unwraps_unknown_single_key_envelope():
    # Any {"<key>": [...]} single-key wrapper is unwrapped, not returned as one row.
    def handler(request):
        return httpx.Response(200, json={"widgets": [{"id": 1}, {"id": 2}]})
    c = _client_with(handler)
    assert c.get_all("/api/x/") == [{"id": 1}, {"id": 2}]


def test_get_all_wraps_bare_multikey_object():
    # A genuine single object (multi-key, no list value) is still wrapped as one row.
    def handler(request):
        return httpx.Response(200, json={"id": "x", "name": "solo"})
    c = _client_with(handler)
    assert c.get_all("/api/x/") == [{"id": "x", "name": "solo"}]


def test_get_all_wraps_single_key_nonlist_dict():
    # Boundary: a single-key dict whose value is NOT a list (e.g. an error
    # envelope) must stay wrapped, not get unwrapped by the generic fallback.
    def handler(request):
        return httpx.Response(200, json={"detail": "Not found."})
    c = _client_with(handler)
    assert c.get_all("/api/x/") == [{"detail": "Not found."}]


# ---------------------------------------------------------------------------
# _handle exposes the HTTP status (downloads key their /export/ fallback off it)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", [400, 404, 413, 500])
def test_handle_sets_status_code_on_apierror(code):
    def handler(request):
        return httpx.Response(code, json={"detail": "nope"})

    c = _client_with(handler)
    with pytest.raises(APIError) as ei:
        c.get("/api/x/")
    assert ei.value.status_code == code  # the download fallback keys off this


def test_handle_401_clears_token_and_sets_status_code():
    # access_token set => hosted path: a 401 falls straight through to _handle
    # (no refresh/retry), which clears the token and raises AuthError. Assert the
    # status is attached there too, since any status-aware caller relies on it.
    from qcdatabase_mcp import auth

    def handler(request):
        return httpx.Response(401, json={"detail": "expired"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://qcdatabase.ai")
    store = _FakeStore()
    c = QCClient(store=store, access_token="tok", http=http)
    with pytest.raises(auth.AuthError) as ei:
        c.get("/api/x/")
    assert store.cleared is True
    assert ei.value.status_code == 401


# ---------------------------------------------------------------------------
# H2 / M1 — subject extraction, fail-closed, session keying
# ---------------------------------------------------------------------------
def test_subject_tenant_scoped():
    assert hosted._subject_of({"id": "U1", "tenant": {"id": "T1"}}) == "T1:U1"
    assert hosted._subject_of({"id": "U1"}) == "-:U1"


def test_subject_fail_closed_without_id():
    assert hosted._subject_of({"email": "a@b.c", "tenant": {"id": "T1"}}) is None
    assert hosted._access_token_from_whoami("tok", {"email": "x"}) is None


def test_session_key_never_raw_token():
    a = AccessToken(token="SECRET", client_id="c", scopes=[], subject=None)
    hs = hosted.HostedSession(a)
    assert "SECRET" not in hs._key
    assert hs._key.startswith("tokenhash:")


# ---------------------------------------------------------------------------
# H2 / M2 — token verifier cache (positive, negative, hashed keys)
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class _FakeHTTP:
    def __init__(self, status, data):
        self.status = status
        self.data = data
        self.calls = 0

    async def get(self, path, headers=None):
        self.calls += 1
        return _FakeResp(self.status, self.data)


def test_verifier_caches_success_once():
    v = hosted.QCDBTokenVerifier(cache_ttl=60, neg_cache_ttl=5)
    v._http = _FakeHTTP(200, {"id": "U1", "tenant": {"id": "T1"}, "scopes": ["a"]})
    at1 = asyncio.run(v.verify_token("tok"))
    at2 = asyncio.run(v.verify_token("tok"))
    assert at1 is not None and at1.subject == "T1:U1"
    assert at2 is not None and at2.subject == at1.subject
    assert v._http.calls == 1  # second served from cache


def test_verifier_negative_caches_failure():
    v = hosted.QCDBTokenVerifier(cache_ttl=60, neg_cache_ttl=60)
    v._http = _FakeHTTP(401, None)
    assert asyncio.run(v.verify_token("bad")) is None
    assert asyncio.run(v.verify_token("bad")) is None
    assert v._http.calls == 1  # failure cached, no amplification


def test_verifier_200_without_id_is_unverified():
    v = hosted.QCDBTokenVerifier()
    v._http = _FakeHTTP(200, {"email": "x"})  # no user id
    with pytest.raises(hosted.VerificationUnavailable):
        asyncio.run(v.verify_token("tok"))


def test_verifier_cache_key_is_hashed():
    v = hosted.QCDBTokenVerifier()
    v._http = _FakeHTTP(200, {"id": "U1", "tenant": {"id": "T1"}})
    asyncio.run(v.verify_token("RAW-TOKEN"))
    assert all("RAW-TOKEN" not in k for k in v._cache)


def test_verifier_cache_values_contain_no_raw_token():
    """Cache entries must store only derived identity, never the raw bearer token."""
    v = hosted.QCDBTokenVerifier(cache_ttl=60, neg_cache_ttl=5)
    v._http = _FakeHTTP(200, {"id": "U2", "tenant": {"id": "T2"}, "scopes": ["read"]})
    asyncio.run(v.verify_token("SUPER-SECRET-TOKEN"))
    for _expires, identity in v._cache.values():
        # identity is _CachedIdentity or None — neither should contain the raw token
        assert identity is None or not hasattr(identity, "token"), (
            "cache entry must not be an AccessToken (which carries the raw token)"
        )
        if identity is not None:
            # Confirm none of the stored string fields equal the raw token
            assert identity.client_id != "SUPER-SECRET-TOKEN"
            assert identity.subject != "SUPER-SECRET-TOKEN"
            assert "SUPER-SECRET-TOKEN" not in (identity.scopes or [])


def test_verifier_two_users_isolated_regardless_of_cache_state():
    """Two distinct tokens must each resolve to their own user only.

    Verifies both the live-lookup path (first call per token) and the cache-hit
    path (second call per token) return the correct subject for each caller.
    """

    class _TwoUserHTTP:
        def __init__(self):
            self.calls = 0

        async def get(self, path, headers=None):
            self.calls += 1
            token = (headers or {}).get("Authorization", "").split()[-1]
            if token == "token-alice":
                return _FakeResp(200, {"id": "Ualice", "tenant": {"id": "T1"}, "scopes": ["read"]})
            if token == "token-bob":
                return _FakeResp(200, {"id": "Ubob", "tenant": {"id": "T1"}, "scopes": ["read"]})
            return _FakeResp(401, None)

    v = hosted.QCDBTokenVerifier(cache_ttl=60, neg_cache_ttl=5)
    v._http = _TwoUserHTTP()

    # First call for each token hits the API.
    at_alice = asyncio.run(v.verify_token("token-alice"))
    at_bob = asyncio.run(v.verify_token("token-bob"))
    assert at_alice is not None and at_alice.subject == "T1:Ualice"
    assert at_bob is not None and at_bob.subject == "T1:Ubob"
    assert at_alice.subject != at_bob.subject
    assert v._http.calls == 2

    # Second call for each token is served from cache — subjects still correct.
    at_alice2 = asyncio.run(v.verify_token("token-alice"))
    at_bob2 = asyncio.run(v.verify_token("token-bob"))
    assert at_alice2 is not None and at_alice2.subject == "T1:Ualice"
    assert at_bob2 is not None and at_bob2.subject == "T1:Ubob"
    assert v._http.calls == 2  # no additional API calls


def test_verifier_cache_hit_reconstruction_uses_callers_token():
    """On a cache hit the reconstructed AccessToken must carry the *caller's* raw
    token, not a credential from any other source.

    The cache stores only derived identity (_CachedIdentity, no token field).
    When the cache is pre-populated for a given key and verify_token is called
    with a token mapping to that key, the returned AccessToken.token must equal
    exactly the token the caller passed — proving reconstruction never substitutes
    a stored or external credential.
    """
    import time as _time

    v = hosted.QCDBTokenVerifier(cache_ttl=60, neg_cache_ttl=5)
    # Replace the live HTTP client with a fake that asserts it is never reached.
    v._http = _FakeHTTP(200, {"id": "SHOULD-NOT-BE-CALLED"})

    caller_token = "caller-token-xyz-unique"
    cache_key = v._cache_key(caller_token)

    # Inject a derived-identity-only cache entry (no raw token stored, per design).
    injected_identity = hosted._CachedIdentity(
        client_id="qcdatabase-mcp",
        scopes=["read"],
        subject="T1:Uowner",
        claims={"id": "Uowner", "tenant": {"id": "T1"}},
    )
    v._cache[cache_key] = (_time.monotonic() + 60, injected_identity)

    at = asyncio.run(v.verify_token(caller_token))

    assert at is not None, "cache hit should return a valid AccessToken"
    # Identity comes from the injected cache entry.
    assert at.subject == "T1:Uowner"
    # Token in the reconstructed AccessToken must be the caller's own token.
    assert at.token == caller_token, (
        f"reconstructed AccessToken.token ({at.token!r}) must equal the caller's "
        f"token ({caller_token!r}), not a stored credential"
    )
    # The HTTP client must not have been called at all (pure cache hit).
    assert v._http.calls == 0, "cache hit must not issue a whoami call"


# ---------------------------------------------------------------------------
# old-M1 — refresh only clears the token on invalid-grant
# ---------------------------------------------------------------------------
class _FakeStore:
    def __init__(self):
        self._token = {"refresh_token": "rt", "access_token": "at"}
        self._client = {"client_id": "cid"}
        self.cleared = False

    def get_token(self):
        return self._token

    def get_client(self, redirect_uri):
        return self._client

    def clear_token(self):
        self.cleared = True
        self._token = None

    def set_token(self, token):
        self._token = token


def _patch_token_post(monkeypatch, status, data=None):
    from qcdatabase_mcp import auth

    def fake_post(url, data=None, timeout=None):
        return _FakeResp(status, data if status == 200 else {"error": "x"})
    monkeypatch.setattr(auth.httpx, "post", fake_post)


def test_refresh_keeps_token_on_5xx(monkeypatch):
    from qcdatabase_mcp import auth
    _patch_token_post(monkeypatch, 503)
    store = _FakeStore()
    with pytest.raises(auth.AuthError):
        auth.refresh(store, 8765)
    assert store.cleared is False  # transient error must not log the user out


def test_refresh_clears_token_on_400(monkeypatch):
    from qcdatabase_mcp import auth
    _patch_token_post(monkeypatch, 400)
    store = _FakeStore()
    with pytest.raises(auth.AuthError):
        auth.refresh(store, 8765)
    assert store.cleared is True


# ---------------------------------------------------------------------------
# Session stores
# ---------------------------------------------------------------------------
def test_memory_store_ttl_and_isolation():
    s = hosted.MemorySessionStore(ttl=60, max_entries=10)
    s.set_active_project("T:U1", {"id": "A"})
    s.set_active_project("T:U2", {"id": "B"})
    assert s.get_active_project("T:U1") == {"id": "A"}
    assert s.get_active_project("T:U2") == {"id": "B"}
    s.set_active_project("T:U1", None)
    assert s.get_active_project("T:U1") is None
