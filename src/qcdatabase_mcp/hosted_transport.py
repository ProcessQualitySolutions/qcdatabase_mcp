"""HTTP boundary for safe authentication failure responses."""

import logging

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.routing import Route

from .hosted import VerificationUnavailable, advertised_scopes

_log = logging.getLogger(__name__)


class VerificationErrorMiddleware:
    """Catch verifier failures outside Starlette's authentication middleware.

    Also adds ``scope="..."`` to the SDK's 401 ``WWW-Authenticate`` challenge
    (RFC 6750 section 3) so MCP clients that read the challenge, not just the
    protected-resource document, request the advertised scopes.
    """

    def __init__(self, app, scopes=None):
        self.app = app
        self.scope_param = (
            ', scope="' + " ".join(scopes) + '"' if scopes else ""
        ).encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def observed_send(message):
            if message["type"] == "http.response.start" and message["status"] == 401:
                headers = dict(scope.get("headers", []))
                bearer = headers.get(b"authorization", b"").lower().startswith(b"bearer ")
                _log.info("mcp_auth outcome=unauthorized bearer_present=%s", bearer)
                if self.scope_param:
                    message = dict(message, headers=[
                        (name, value + self.scope_param)
                        if name.lower() == b"www-authenticate"
                        and value.lower().startswith(b"bearer")
                        and b"scope=" not in value.lower()
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
        from mcp.server.auth.routes import build_resource_metadata_url
        from urllib.parse import urlparse

        from mcp.server.auth.routes import create_protected_resource_routes

        scopes = advertised_scopes()
        resource = self.settings.auth.resource_server_url if self.settings.auth else None
        if resource:
            metadata_path = urlparse(str(build_resource_metadata_url(resource))).path
            # Rebuild the metadata document with scopes_supported. The SDK fills
            # it from required_scopes, which would also REQUIRE every scope on
            # every token; advertising must not turn into enforcement.
            advertised = create_protected_resource_routes(
                resource_url=resource,
                authorization_servers=[self.settings.auth.issuer_url],
                scopes_supported=scopes or None,
            )[0]
            for i, r in enumerate(app.routes):
                if getattr(r, "path", None) == metadata_path:
                    app.routes[i] = advertised
            root = "/.well-known/oauth-protected-resource"
            if metadata_path != root:
                app.routes.append(Route(root, endpoint=advertised.endpoint, methods=["GET", "OPTIONS"]))
        app.add_middleware(VerificationErrorMiddleware, scopes=scopes)
        return app
