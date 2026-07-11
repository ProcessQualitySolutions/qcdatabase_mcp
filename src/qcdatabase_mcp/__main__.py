"""Entry point: ``python -m qcdatabase_mcp`` / the ``qcdatabase-mcp`` command.

By default this runs the MCP server over **stdio**, which is what desktop AI
apps (Claude Desktop and friends) launch and talk to - one local user, sign-in
handled by the ``login`` tool.

Pass ``--http`` to run the **multi-user hosted** server instead (for deploying at
e.g. ``mcp.qcdatabase.ai``). In that mode FastMCP is an OAuth 2.0 resource
server: each MCP client signs its user in against QCDatabase.AI and sends that
user's access token per request; the server verifies it and acts as that user.
See :mod:`.hosted` for the details.

Configuration (flags override environment):

    --http / QCDB_MCP_HTTP=1          run the hosted HTTP server instead of stdio
    --host / QCDB_MCP_HOST            bind interface        (default 127.0.0.1)
    --port / QCDB_MCP_PORT            bind port             (default 8000)
    --resource-url / QCDB_MCP_RESOURCE_URL   public URL of this server
                                             (required for non-loopback binds)
    --issuer-url   / QCDB_MCP_ISSUER_URL     OAuth authorization server
                                             (default https://qcdatabase.ai)
             QCDB_MCP_ALLOWED_ORIGINS / QCDB_MCP_ALLOWED_HOSTS
                                             extra allow-list entries (comma sep.)
"""

from __future__ import annotations

import argparse
import os
import sys

_LOOPBACK = {"", "127.0.0.1", "::1", "localhost"}
_TRUTHY = {"1", "true", "yes", "on"}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qcdatabase-mcp",
        description="MCP server for QC Database. Defaults to stdio; pass --http to host it for many users.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        default=_env_flag("QCDB_MCP_HTTP"),
        help="Run the multi-user hosted HTTP server instead of stdio.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("QCDB_MCP_HOST", "127.0.0.1"),
        help="Interface to bind in --http mode (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("QCDB_MCP_PORT", "8000")),
        help="Port to bind in --http mode (default: 8000).",
    )
    parser.add_argument(
        "--resource-url",
        default=os.environ.get("QCDB_MCP_RESOURCE_URL"),
        help="Public URL of this server (e.g. https://mcp.qcdatabase.ai). Required for non-loopback binds.",
    )
    parser.add_argument(
        "--issuer-url",
        default=os.environ.get("QCDB_MCP_ISSUER_URL"),
        help="OAuth authorization server clients sign in against (default: https://qcdatabase.ai).",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.http:
        # stdio: import lazily so the hosted config path is never touched here.
        from .server import run

        run()
        return

    # Publish the chosen configuration into the environment *before* importing
    # the server, which reads it once at import time to build the auth-configured
    # FastMCP instance.
    os.environ["QCDB_MCP_HTTP"] = "1"
    os.environ["QCDB_MCP_HOST"] = args.host
    os.environ["QCDB_MCP_PORT"] = str(args.port)
    if args.resource_url:
        os.environ["QCDB_MCP_RESOURCE_URL"] = args.resource_url
    if args.issuer_url:
        os.environ["QCDB_MCP_ISSUER_URL"] = args.issuer_url

    from . import hosted

    errors = hosted.validate_config()
    if errors:
        parser.error(" ".join(errors))

    if args.host.strip().lower() not in _LOOPBACK:
        print(
            f"NOTE: binding non-loopback host '{args.host}'. Put this behind a "
            "reverse proxy that terminates TLS - do not expose plain HTTP to the "
            "internet.",
            file=sys.stderr,
        )
    print(
        "Hosted mode (OAuth resource server):\n"
        f"  serving on   {args.host}:{args.port}\n"
        f"  resource id  {hosted.resource_url()}\n"
        f"  auth server  {hosted.issuer_url()}",
        file=sys.stderr,
    )

    from .server import run

    run()


if __name__ == "__main__":
    main()
