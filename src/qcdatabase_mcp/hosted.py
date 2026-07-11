"""Multi-user hosting for the QC Database MCP server.

Everything in this module is inert unless the server is started in **hosted HTTP
mode** (``QCDB_MCP_HTTP=1`` / ``--http``). The default stdio server is untouched
by any of it.

## The model

Hosted mode turns the server into an OAuth 2.0 **resource server**, exactly as
the MCP authorization spec prescribes for a remote server:

* The MCP client (Claude, etc.) discovers the authorization server from the
  ``/.well-known/oauth-protected-resource`` document FastMCP publishes, runs the
  Authorization-Code + PKCE flow against **QCDatabase.AI**, and sends the
  resulting access token as ``Authorization: Bearer <token>`` on every request.
* :class:`QCDBTokenVerifier` validates that token by calling ``/api/whoami/``
  (which both proves the token is good *and* tells us who the user is), then
  hands FastMCP an :class:`AccessToken`. The raw token is carried through so the
  tools can act against the QC Database API as that user.
* Per-user session state (the active project) lives in server memory keyed by
  the user's stable id - see :class:`HostedSession`.

No credentials are ever stored on disk in this mode; identity travels with each
request. That is what makes one deployment safe for many users.

## Backend requirements

QCDatabase.AI already provides everything the flow needs: OAuth discovery at
``/.well-known/oauth-authorization-server`` and ``/.well-known/oauth-protected-
resource``, Authorization-Code + PKCE, and dynamic client registration. For full
RFC 8707 audience binding (so a token minted for the API cannot be replayed at an
unrelated resource) the authorization server should honor a ``resource``
indicator of the deployed MCP URL; until then this server trusts any valid
QCDatabase-issued token, which is acceptable because it forwards to the very same
QCDatabase API.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from urllib.parse import urlparse

import httpx
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from . import BASE_URL

_TRUTHY = {"1", "true", "yes", "on"}
_LOOPBACK_HOSTS = {"", "127.0.0.1", "::1", "localhost"}
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = "8000"


# ---------------------------------------------------------------------------
# Configuration (all from the environment; the CLI sets these from its flags)
# ---------------------------------------------------------------------------
def hosted_enabled() -> bool:
    return os.environ.get("QCDB_MCP_HTTP", "").strip().lower() in _TRUTHY


def bind_host() -> str:
    return os.environ.get("QCDB_MCP_HOST", _DEFAULT_HOST).strip() or _DEFAULT_HOST


def bind_port() -> int:
    try:
        return int(os.environ.get("QCDB_MCP_PORT", _DEFAULT_PORT))
    except ValueError:
        return int(_DEFAULT_PORT)


def issuer_url() -> str:
    """The OAuth authorization server clients should sign in against."""
    return os.environ.get("QCDB_MCP_ISSUER_URL", BASE_URL).rstrip("/")


def resource_url() -> str:
    """This MCP server's own public URL (the OAuth resource identifier).

    In production set ``QCDB_MCP_RESOURCE_URL`` to e.g. ``https://mcp.qcdatabase.ai``.
    For a loopback dev bind we derive a sensible default.
    """
    explicit = os.environ.get("QCDB_MCP_RESOURCE_URL")
    if explicit:
        return explicit.rstrip("/")
    return f"http://{bind_host()}:{bind_port()}"


def _split(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def validate_config() -> list[str]:
    """Return a list of fatal misconfigurations (empty means good to serve)."""
    errors: list[str] = []
    if bind_host().lower() not in _LOOPBACK_HOSTS and not os.environ.get("QCDB_MCP_RESOURCE_URL"):
        errors.append(
            "QCDB_MCP_RESOURCE_URL must be set to this server's public URL "
            "(e.g. https://mcp.qcdatabase.ai) when binding a non-loopback host, so "
            "MCP clients receive the correct OAuth resource identifier."
        )
    return errors


def auth_settings() -> AuthSettings:
    """OAuth resource-server settings: which AS to trust, and our resource id."""
    return AuthSettings(
        issuer_url=issuer_url(),
        resource_server_url=resource_url(),
        required_scopes=None,  # per-endpoint scopes are enforced by the API itself
    )


def transport_security() -> TransportSecuritySettings:
    """Host/Origin allow-lists that block DNS-rebinding against the endpoint."""
    hosts: set[str] = set()
    origins: set[str] = set()

    res = urlparse(resource_url())
    if res.netloc:
        hosts.add(res.netloc)  # host[:port] as seen without a TLS-terminating proxy
        origins.add(f"{res.scheme}://{res.netloc}")
    if res.hostname:
        hosts.add(res.hostname)  # bare host as seen behind a proxy that drops the port

    # Only trust loopback Host/Origin when we are actually bound to loopback -
    # in production a `Host: localhost` should not be waved through.
    if bind_host().strip().lower() in _LOOPBACK_HOSTS:
        port = str(bind_port())
        for local in ("127.0.0.1", "localhost"):
            hosts.add(local)
            hosts.add(f"{local}:{port}")
            origins.add(f"http://{local}:{port}")

    for extra in _split(os.environ.get("QCDB_MCP_ALLOWED_HOSTS")):
        hosts.add(extra)
    for extra in _split(os.environ.get("QCDB_MCP_ALLOWED_ORIGINS")):
        origins.add(extra)

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(hosts),
        allowed_origins=sorted(origins),
    )


# ---------------------------------------------------------------------------
# Token verification (resource-server side of OAuth)
# ---------------------------------------------------------------------------
def _subject_of(data: object) -> str | None:
    """Return a globally-unique per-user key from a ``/api/whoami/`` response.

    The whoami contract returns the caller's global user UUID in ``id`` and the
    active tenant in ``tenant.id``. We key on ``tenant_id:user_id`` so that even
    if a user id were ever only unique within a tenant, two users could never
    collide onto one session (which would leak one user's pinned project to
    another). Returns ``None`` if there is no user id - the caller MUST then treat
    the token as unverified rather than invent a key.
    """
    if not isinstance(data, dict):
        return None
    user_id = data.get("id")
    if not user_id:
        return None
    tenant = data.get("tenant")
    tenant_id = tenant.get("id") if isinstance(tenant, dict) else None
    return f"{tenant_id or '-'}:{user_id}"


def _scopes_of(data: object) -> list[str]:
    if not isinstance(data, dict):
        return []
    value = data.get("scopes")
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return value.split()
    return []


def _access_token_from_whoami(token: str, data: object) -> AccessToken | None:
    """Build an AccessToken from a whoami body, or None if it carries no user id."""
    subject = _subject_of(data)
    if subject is None:
        # Fail closed: a 200 we can't tie to a user is not a verified token.
        return None
    return AccessToken(
        token=token,
        client_id=str((data.get("client_id") if isinstance(data, dict) else None) or "qcdatabase-mcp"),
        scopes=_scopes_of(data),
        subject=subject,
        resource=resource_url(),
        claims=data if isinstance(data, dict) else None,
    )


def _token_cache_ttl() -> float:
    """Seconds a successful verification is trusted before re-checking whoami.

    This is also the window in which a token revoked upstream still works here.
    """
    try:
        return float(os.environ.get("QCDB_MCP_TOKEN_CACHE_TTL", "60"))
    except ValueError:
        return 60.0


def _token_neg_cache_ttl() -> float:
    """Seconds a failed verification is remembered, to blunt whoami amplification."""
    try:
        return float(os.environ.get("QCDB_MCP_TOKEN_NEG_CACHE_TTL", "5"))
    except ValueError:
        return 5.0


class QCDBTokenVerifier(TokenVerifier):
    """Validate a bearer token by asking the QC Database API who it belongs to.

    A short in-memory cache keeps a burst of tool calls from turning into one
    ``/api/whoami/`` round-trip apiece. Failures are cached briefly too, so a
    flood of garbage tokens can't turn this endpoint into an amplification lever
    against the API. Cache entries are keyed by a SHA-256 of the token, never the
    token itself, so raw credentials never sit in a data structure or a heap dump.
    """

    def __init__(
        self,
        cache_ttl: float | None = None,
        neg_cache_ttl: float | None = None,
        max_entries: int = 4096,
    ) -> None:
        self._ttl = _token_cache_ttl() if cache_ttl is None else cache_ttl
        self._neg_ttl = _token_neg_cache_ttl() if neg_cache_ttl is None else neg_cache_ttl
        self._max = max_entries
        # key = sha256(token) -> (expires_at, AccessToken | None)
        self._cache: dict[str, tuple[float, AccessToken | None]] = {}
        self._lock = threading.Lock()
        # One pooled client for the life of the process (never explicitly closed -
        # it lives as long as the server does). follow_redirects is safe because
        # httpx strips Authorization on cross-origin redirects (see httpx>=0.27).
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=15.0, follow_redirects=True)

    @staticmethod
    def _cache_key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def verify_token(self, token: str) -> AccessToken | None:
        now = time.monotonic()
        key = self._cache_key(token)
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1]  # may be a valid AccessToken or a cached None

        access: AccessToken | None = None
        try:
            resp = await self._http.get(
                "/api/whoami/", headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError:
            return None  # transient network trouble: don't cache, re-check next time
        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                data = {}
            access = _access_token_from_whoami(token, data)

        ttl = self._ttl if access is not None else self._neg_ttl
        with self._lock:
            if len(self._cache) >= self._max:
                # Cheap bound: drop everything already expired, else reset.
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
                if len(self._cache) >= self._max:
                    self._cache.clear()
            self._cache[key] = (now + ttl, access)
        return access


# ---------------------------------------------------------------------------
# Per-user session state (the active project), keyed by user id
# ---------------------------------------------------------------------------
# Only ONE piece of state needs to outlive a single request: which project each
# user has pinned. It is tiny, non-secret, and disposable - losing it just makes
# the user run set_project again. So the default is in-memory (ideal for a
# single self-hosted instance), and multi-replica deployments opt into a shared
# backend behind this 2-method interface. The token-verification cache is
# deliberately NOT shared: it is a local perf optimization each replica rebuilds
# from /api/whoami/ on its own.
#
# Configuration:
#   QCDB_MCP_REDIS_URL     if set, share state via Redis across replicas
#   QCDB_MCP_SESSION_TTL   seconds a pinned project survives idle (default 8h)

_DEFAULT_SESSION_TTL = 8 * 60 * 60
_MEMORY_MAX_ENTRIES = 50_000


def _session_ttl() -> int:
    try:
        return int(os.environ.get("QCDB_MCP_SESSION_TTL", str(_DEFAULT_SESSION_TTL)))
    except ValueError:
        return _DEFAULT_SESSION_TTL


class MemorySessionStore:
    """In-process store with TTL + a size cap so it can never leak unboundedly.

    Correct for a single instance (or several instances behind sticky routing).
    """

    def __init__(self, ttl: int = _DEFAULT_SESSION_TTL, max_entries: int = _MEMORY_MAX_ENTRIES) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._data: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get_active_project(self, key: str) -> dict | None:
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry[0] <= now:
                self._data.pop(key, None)
                return None
            return entry[1]

    def set_active_project(self, key: str, project: dict | None) -> None:
        now = time.monotonic()
        with self._lock:
            if project is None:
                self._data.pop(key, None)
                return
            if len(self._data) >= self._max:
                # Drop everything already expired; if still full, evict soonest-to-expire.
                self._data = {k: v for k, v in self._data.items() if v[0] > now}
                if len(self._data) >= self._max:
                    oldest = min(self._data, key=lambda k: self._data[k][0])
                    self._data.pop(oldest, None)
            self._data[key] = (now + self._ttl, project)


class RedisSessionStore:
    """Shared store for multi-replica deployments. Ephemeral keys with a TTL.

    Enabled by ``QCDB_MCP_REDIS_URL``. Requires the optional ``redis`` extra
    (``pip install 'qcdatabase-mcp[redis]'``).
    """

    def __init__(self, url: str, ttl: int = _DEFAULT_SESSION_TTL, prefix: str = "qcdb:mcp:proj:") -> None:
        try:
            import redis  # optional dependency, imported only when actually used
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise RuntimeError(
                "QCDB_MCP_REDIS_URL is set but the 'redis' package is not installed. "
                "Install it with: pip install 'qcdatabase-mcp[redis]'"
            ) from exc
        # redis-py clients are thread-safe (pooled), which suits FastMCP running
        # each sync tool in a worker thread.
        self._redis = redis.Redis.from_url(url)
        self._ttl = ttl
        self._prefix = prefix

    def get_active_project(self, key: str) -> dict | None:
        raw = self._redis.get(self._prefix + key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    def set_active_project(self, key: str, project: dict | None) -> None:
        full = self._prefix + key
        if project is None:
            self._redis.delete(full)
        else:
            self._redis.set(full, json.dumps(project), ex=self._ttl)


def build_session_store():
    """Pick the session store from the environment (Redis if configured, else memory)."""
    url = os.environ.get("QCDB_MCP_REDIS_URL")
    if url:
        return RedisSessionStore(url, ttl=_session_ttl())
    return MemorySessionStore(ttl=_session_ttl())


_STORE = None
_STORE_LOCK = threading.Lock()


def session_store():
    """Return the process-wide session store, building it once on first use."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = build_session_store()
    return _STORE


class HostedSession:
    """A :class:`~qcdatabase_mcp.config.Store`-compatible facade for one user.

    Only the surface the tools actually touch is implemented. The bearer token is
    read straight from the authenticated request (never persisted); the active
    project is delegated to the configured :func:`session_store`, keyed by the
    user's stable id, so it is shared correctly whether that store is in-memory
    (single instance) or Redis (many replicas).
    """

    def __init__(self, access: AccessToken) -> None:
        self._access = access
        # The verifier already fails closed when there is no subject, so this is
        # belt-and-suspenders: if a subject is ever missing, key on a HASH of the
        # token, never the raw token (which would land in the Redis keyspace).
        if access.subject:
            self._key = access.subject
        else:
            self._key = "tokenhash:" + hashlib.sha256(access.token.encode("utf-8")).hexdigest()

    # active project -----------------------------------------------------
    def get_active_project(self) -> dict | None:
        return session_store().get_active_project(self._key)

    def set_active_project(self, project: dict | None) -> None:
        session_store().set_active_project(self._key, project)

    # token (request-scoped, read only) ----------------------------------
    def get_token(self) -> dict:
        return {
            "access_token": self._access.token,
            "scope": " ".join(self._access.scopes or []),
        }

    def clear_token(self) -> None:
        # Nothing to forget server-side: sign-out is handled by the MCP client.
        return None

    # OAuth client registration is a stdio-only concern -------------------
    def get_client(self, redirect_uri: str):  # pragma: no cover - unused in hosted mode
        return None

    def set_client(self, redirect_uri: str, registration: dict) -> None:  # pragma: no cover
        return None
