"""Entry point: ``python -m qcdatabase_mcp`` / the ``qcdatabase-mcp`` command.

Runs the MCP server over stdio, which is what desktop AI apps (Claude Desktop and
friends) launch and talk to.
"""

from __future__ import annotations

from .server import run


def main() -> None:
    run()


if __name__ == "__main__":
    main()
