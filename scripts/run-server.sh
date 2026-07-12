#!/usr/bin/env bash
# Run the hosted HTTP server in production (Linux server).
#
#   git pull && ./scripts/run-server.sh
#
# Configuration comes from the environment, or from a .env file in the repo
# root (KEY=value lines; .env is gitignored). Copy deploy/example.env to .env
# and edit it. QCDB_MCP_RESOURCE_URL (the public https:// URL of this server)
# is REQUIRED. The venv is created/updated automatically, so a plain
# "git pull && ./scripts/run-server.sh" always runs the current checkout.
#
# Bind stays on 127.0.0.1 by default: put a TLS-terminating reverse proxy in
# front (see deploy/nginx.conf or deploy/Caddyfile) rather than exposing the
# port. The proxy MUST forward the original Host header.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

: "${QCDB_MCP_HOST:=127.0.0.1}"
: "${QCDB_MCP_PORT:=8000}"

if [ -z "${QCDB_MCP_RESOURCE_URL:-}" ]; then
    echo "ERROR: QCDB_MCP_RESOURCE_URL is not set." >&2
    echo "Set it to this server's public URL (e.g. https://mcp.qcdatabase.ai)," >&2
    echo "either in the environment or in a .env file (see deploy/example.env)." >&2
    exit 1
fi

if [ ! -d .venv ]; then
    echo "Creating virtual environment (.venv)..."
    python3 -m venv .venv
fi

# Re-install on every start so a git pull is all a deploy needs.
.venv/bin/python -m pip install -q -e .

exec .venv/bin/python -m qcdatabase_mcp --http \
    --host "$QCDB_MCP_HOST" --port "$QCDB_MCP_PORT" \
    --resource-url "$QCDB_MCP_RESOURCE_URL"
