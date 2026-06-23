"""OAuth 2.0 login for QCDatabase.AI.

Implements exactly the flow the QC Database spec describes for headless / MCP
clients:

* Dynamic Client Registration (RFC 7591) -> a public, PKCE-required client.
* Authorization Code + PKCE (RFC 7636), with the redirect sent to a tiny local
  web server on the loopback interface so we can catch the code automatically.
* Token exchange and refresh, both pinned to the tenant the user picks on the
  consent screen.

The login step opens the user's browser once; after that the saved refresh token
keeps the connection alive and ``ensure_access_token`` refreshes silently.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import time
import urllib.parse
import webbrowser
from typing import Any

import httpx

from . import BASE_URL
from .config import Store

# The everyday-user scope set: read everywhere, plus write where a daily-driver
# actually needs it. Lists / shippers / tenants are read-only by design. This is
# the permanent ceiling for the registered client, so it stays as narrow as the
# "do real turnover work" goal allows.
DEFAULT_SCOPES = (
    "tenants:read "
    "projects:read projects:write "
    "jobs:read jobs:write "
    "packages:read packages:write "
    "drawings:read drawings:write "
    "mapping:read mapping:write "
    "documents:read documents:write "
    "forms:read forms:write "
    "notes:read notes:write "
    "references:read references:write "
    "photos:read photos:write "
    "linespecs:read linespecs:write "
    "lists:read "
    "shippers:read"
)

CLIENT_NAME = "QC Database Local MCP Server"
DEFAULT_CALLBACK_PORT = 8765
_REGISTER_URL = f"{BASE_URL}/oauth/register/"
_AUTHORIZE_URL = f"{BASE_URL}/oauth/authorize/"
_TOKEN_URL = f"{BASE_URL}/oauth/token/"


class AuthError(RuntimeError):
    """Raised when we are not logged in, or a login / refresh attempt fails."""


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _new_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# ---------------------------------------------------------------------------
# Loopback callback server
# ---------------------------------------------------------------------------
_SUCCESS_HTML = b"""<!doctype html><html><head><meta charset="utf-8">
<title>QC Database connected</title></head>
<body style="font-family:system-ui;text-align:center;padding-top:4rem">
<h2>You're connected to QC Database.</h2>
<p>You can close this tab and return to your assistant.</p>
</body></html>"""

_ERROR_HTML = b"""<!doctype html><html><head><meta charset="utf-8">
<title>QC Database login failed</title></head>
<body style="font-family:system-ui;text-align:center;padding-top:4rem">
<h2>Login did not complete.</h2>
<p>You can close this tab and try again in your assistant.</p>
</body></html>"""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    # Filled in by the calling thread.
    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.result = {k: v[0] for k, v in params.items()}
        ok = "code" in _CallbackHandler.result
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_SUCCESS_HTML if ok else _ERROR_HTML)

    def log_message(self, *_args: Any) -> None:  # silence default logging
        pass


def _wait_for_callback(port: int, timeout: float) -> dict[str, str]:
    _CallbackHandler.result = {}
    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = 1.0
    deadline = time.time() + timeout
    try:
        while time.time() < deadline and not _CallbackHandler.result:
            server.handle_request()
    finally:
        server.server_close()
    return _CallbackHandler.result


# ---------------------------------------------------------------------------
# Client registration
# ---------------------------------------------------------------------------
def _redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}/callback"


def _register_client(store: Store, redirect_uri: str) -> dict[str, Any]:
    cached = store.get_client(redirect_uri)
    if cached and cached.get("client_id"):
        return cached

    payload = {
        "redirect_uris": [redirect_uri],
        "client_name": CLIENT_NAME,
        "grant_types": ["authorization_code", "refresh_token"],
        "scope": DEFAULT_SCOPES,
    }
    try:
        resp = httpx.post(_REGISTER_URL, json=payload, timeout=30.0)
    except httpx.HTTPError as exc:
        raise AuthError(f"Could not reach QC Database to register the app: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise AuthError(
            f"Client registration failed ({resp.status_code}): {resp.text[:300]}"
        )
    registration = resp.json()
    if not registration.get("client_id"):
        raise AuthError("Client registration succeeded but returned no client_id.")
    store.set_client(redirect_uri, registration)
    return registration


# ---------------------------------------------------------------------------
# Token persistence helpers
# ---------------------------------------------------------------------------
def _store_token(store: Store, token: dict[str, Any]) -> dict[str, Any]:
    expires_in = int(token.get("expires_in", 3600))
    # Refresh a minute early so a call never races the expiry.
    token = dict(token)
    token["expires_at"] = time.time() + max(expires_in - 60, 0)
    store.set_token(token)
    return token


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def login(store: Store, port: int, timeout: float = 300.0) -> dict[str, Any]:
    """Run the full browser login and persist the resulting tokens.

    Returns the granted scope info. Raises :class:`AuthError` on any failure.
    """
    redirect_uri = _redirect_uri(port)
    registration = _register_client(store, redirect_uri)
    client_id = registration["client_id"]

    verifier, challenge = _new_pkce()
    state = secrets.token_urlsafe(24)
    authorize_url = _AUTHORIZE_URL + "?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": registration.get("scope", DEFAULT_SCOPES),
            "state": state,
        }
    )

    # Open the browser, but keep going even if it fails (headless box etc.) -
    # the URL is also returned to the caller so they can open it manually.
    opened = False
    try:
        opened = webbrowser.open(authorize_url)
    except Exception:
        opened = False

    if not opened:
        raise AuthError(
            "Could not open a browser automatically. Open this URL to finish "
            f"signing in, then run the login again:\n{authorize_url}"
        )

    result = _wait_for_callback(port, timeout)
    if not result:
        raise AuthError(
            "Timed out waiting for the browser sign-in. Please run login again. "
            f"If a browser did not open, visit:\n{authorize_url}"
        )
    if "error" in result:
        raise AuthError(
            f"Sign-in was rejected: {result.get('error')} "
            f"{result.get('error_description', '')}".strip()
        )
    if result.get("state") != state:
        raise AuthError("Sign-in failed a security check (state mismatch). Try again.")
    code = result.get("code")
    if not code:
        raise AuthError("Sign-in returned no authorization code. Try again.")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    try:
        resp = httpx.post(_TOKEN_URL, data=data, timeout=30.0)
    except httpx.HTTPError as exc:
        raise AuthError(f"Could not reach QC Database to get a token: {exc}") from exc
    if resp.status_code != 200:
        raise AuthError(f"Token exchange failed ({resp.status_code}): {resp.text[:300]}")
    return _store_token(store, resp.json())


def refresh(store: Store, port: int) -> dict[str, Any]:
    """Use the stored refresh token to get a fresh access token."""
    token = store.get_token()
    if not token or not token.get("refresh_token"):
        raise AuthError("Not logged in. Run the 'login' tool first.")
    redirect_uri = _redirect_uri(port)
    registration = store.get_client(redirect_uri)
    if not registration:
        raise AuthError("No registered app on this callback port. Run 'login' again.")

    data = {
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
        "client_id": registration["client_id"],
    }
    try:
        resp = httpx.post(_TOKEN_URL, data=data, timeout=30.0)
    except httpx.HTTPError as exc:
        raise AuthError(f"Could not reach QC Database to refresh the session: {exc}") from exc
    if resp.status_code != 200:
        store.clear_token()
        raise AuthError(
            "Your session expired and could not be refreshed. Run 'login' again."
        )
    new_token = resp.json()
    # Rotation is on; the server may not re-send the refresh token, so keep ours.
    if not new_token.get("refresh_token"):
        new_token["refresh_token"] = token["refresh_token"]
    return _store_token(store, new_token)


def ensure_access_token(store: Store, port: int) -> str:
    """Return a valid access token, refreshing if it is expired or about to be.

    Raises :class:`AuthError` if the user has never logged in.
    """
    token = store.get_token()
    if not token:
        raise AuthError("Not logged in. Run the 'login' tool first.")
    if time.time() >= token.get("expires_at", 0):
        token = refresh(store, port)
    return token["access_token"]
