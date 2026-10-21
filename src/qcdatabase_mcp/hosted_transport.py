"""HTTP boundary for safe authentication failure responses."""

import logging
import re
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.routing import Route

from .hosted import VerificationUnavailable, advertised_scopes

_log = logging.getLogger(__name__)

_CHALLENGE_PARAM = re.compile(rb'([A-Za-z_]+)="([^"]*)"')


def _rewrite_challenge(value, scope_value, token_sent):
    """Rebuild the SDK's Bearer challenge.

    Without a token the challenge carries no ``error`` (RFC 6750 section 3.1),
    and ``scope`` is added when the SDK left it out (section 3).
    """
    params = [
        (k, v) for k, v in _CHALLENGE_PARAM.findall(value)
        if token_sent or k.lower() not in (b"error", b"error_description")
    ]
    if scope_value and not any(k.lower() == b"scope" for k, _ in params):
        params.append((b"scope", scope_value))
    if not params:
        return b"Bearer"
    return b"Bearer " + b", ".join(k + b'="' + v + b'"' for k, v in params)


def _exact_issuer(issuer) -> str:
    """The issuer exactly as the authorization server publishes it.

    pydantic normalises a bare origin to end in "/", but QC Database's
    metadata ``issuer`` has no trailing slash and clients compare the two
    byte for byte (RFC 8414 section 3.3).
    """
    value = str(issuer)
    return value[:-1] if urlparse(value).path == "/" else value


class CorsMiddleware:
    """CORS for the MCP endpoint, limited to the transport's allowed origins.

    Preflights are answered here, before authentication, so they get a 204
    instead of a 401. Actual responses, 401 challenges included, carry
    ``Access-Control-Allow-Origin`` and expose ``WWW-Authenticate`` so browser
    clients can start discovery. Origins outside the DNS-rebinding allow-list
    get no CORS headers: the transport rejects their requests anyway. The
    metadata routes under /.well-known/ have the SDK's own CORS handling.
    """

    ALLOW_METHODS = b"GET, POST, DELETE, OPTIONS"
    ALLOW_HEADERS = (b"Authorization, Content-Type, Accept, Mcp-Session-Id, "
                     b"Mcp-Protocol-Version, Last-Event-ID")
    EXPOSE_HEADERS = b"WWW-Authenticate, Mcp-Session-Id, Mcp-Protocol-Version"

    def __init__(self, app, allowed_origins=()):
        self.app = app
        self.allowed_origins = {o.encode() for o in allowed_origins}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith("/.well-known/"):
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers", []))
        origin = headers.get(b"origin")
        allowed = origin in self.allowed_origins
        cors = [(b"access-control-allow-origin", origin)] if allowed else []

        if scope["method"] == "OPTIONS" and b"access-control-request-method" in headers:
            if allowed:
                cors += [
                    (b"access-control-allow-methods", self.ALLOW_METHODS),
                    (b"access-control-allow-headers", self.ALLOW_HEADERS),
                    (b"access-control-max-age", b"600"),
                ]
            await send({"type": "http.response.start", "status": 204,
                        "headers": cors + [(b"vary", b"Origin")]})
            await send({"type": "http.response.body", "body": b""})
            return

        if not allowed:
            return await self.app(scope, receive, send)

        cors += [(b"access-control-expose-headers", self.EXPOSE_HEADERS), (b"vary", b"Origin")]

        async def cors_send(message):
            if message["type"] == "http.response.start":
                message = dict(message, headers=list(message.get("headers", [])) + cors)
            await send(message)

        await self.app(scope, receive, cors_send)


class VerificationErrorMiddleware:
    """Catch verifier failures outside Starlette's authentication middleware.

    Also adds ``scope="..."`` to the SDK's 401 ``WWW-Authenticate`` challenge
    (RFC 6750 section 3) so MCP clients that read the challenge, not just the
    protected-resource document, request the advertised scopes, and drops
    ``error="invalid_token"`` when no token was sent.
    """

    def __init__(self, app, scopes=None):
        self.app = app
        self.scope_value = " ".join(scopes).encode() if scopes else b""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def observed_send(message):
            if message["type"] == "http.response.start" and message["status"] == 401:
                headers = dict(scope.get("headers", []))
                bearer = headers.get(b"authorization", b"").lower().startswith(b"bearer ")
                _log.info("mcp_auth outcome=unauthorized bearer_present=%s", bearer)
                message = dict(message, headers=[
                    (name, _rewrite_challenge(value, self.scope_value, bearer))
                    if name.lower() == b"www-authenticate"
                    and value.lower().startswith(b"bearer")
                    else (name, value)
                    for name, value in message.get("headers", [])
                ])
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        except VerificationUnavailable as exc:
            forbidden = exc.status_code == 403
            response = JSONResponse(
                {
                    "error": "access_denied" if forbidden else "temporarily_unavailable",
                    "error_description": (
                        "QC Database denied the identity check. Check account access "
                        "with your administrator; reconnecting may not help."
                        if forbidden else
                        "QC Database identity verification is unavailable. Retry shortly; "
                        "this is not a request to reconnect."
                    ),
                },
                status_code=exc.status_code,
                headers={"Cache-Control": "no-store", **({} if forbidden else {"Retry-After": "5"})},
            )
            # No invalid_token challenge: clients must not discard credentials.
            await response(scope, receive, send)


class HostedFastMCP(FastMCP):
    def streamable_http_app(self):
        app = super().streamable_http_app()
        # The SDK serves path-specific metadata for the /mcp resource. Preserve
        # root discovery for existing clients, using the very same handler.
        from mcp.server.auth.routes import build_resource_metadata_url, cors_middleware
        from mcp.shared.auth import ProtectedResourceMetadata

        scopes = advertised_scopes()
        resource = self.settings.auth.resource_server_url if self.settings.auth else None
        if resource:
            metadata_path = urlparse(str(build_resource_metadata_url(resource))).path
            # Rebuild the metadata document with scopes_supported. The SDK fills
            # it from required_scopes, which would also REQUIRE every scope on
            # every token; advertising must not turn into enforcement.
            document = ProtectedResourceMetadata(
                resource=resource,
                authorization_servers=[self.settings.auth.issuer_url],
                scopes_supported=scopes or None,
            ).model_dump(mode="json", exclude_none=True)
            document["authorization_servers"] = [_exact_issuer(self.settings.auth.issuer_url)]

            async def protected_resource_metadata(request):
                return JSONResponse(document, headers={"Cache-Control": "public, max-age=3600"})

            advertised = Route(
                metadata_path,
                endpoint=cors_middleware(protected_resource_metadata, ["GET", "OPTIONS"]),
                methods=["GET", "OPTIONS"],
            )
            for i, r in enumerate(app.routes):
                if getattr(r, "path", None) == metadata_path:
                    app.routes[i] = advertised
            root = "/.well-known/oauth-protected-resource"
            if metadata_path != root:
                app.routes.append(Route(root, endpoint=advertised.endpoint, methods=["GET", "OPTIONS"]))
        app.add_middleware(VerificationErrorMiddleware, scopes=scopes)
        # Added last so it is outermost: preflights never reach authentication.
        security = self.settings.transport_security
        app.add_middleware(CorsMiddleware,
                           allowed_origins=security.allowed_origins if security else ())
        return app
