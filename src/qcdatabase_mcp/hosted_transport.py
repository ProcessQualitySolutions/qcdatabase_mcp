"""HTTP boundary for safe authentication failure responses."""

import logging

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from .hosted import VerificationUnavailable

_log = logging.getLogger(__name__)


class VerificationErrorMiddleware:
    """Catch verifier failures outside Starlette's authentication middleware."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def observed_send(message):
            if message["type"] == "http.response.start" and message["status"] == 401:
                headers = dict(scope.get("headers", []))
                bearer = headers.get(b"authorization", b"").lower().startswith(b"bearer ")
                _log.info("mcp_auth outcome=unauthorized bearer_present=%s", bearer)
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
        app.add_middleware(VerificationErrorMiddleware)
        return app
