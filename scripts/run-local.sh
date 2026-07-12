#!/usr/bin/env bash
# Run the hosted HTTP server on localhost for testing (Mac/Linux).
#
#   ./scripts/run-local.sh          # http://127.0.0.1:8000
#   PORT=9000 ./scripts/run-local.sh
#
# Creates a .venv on first run, installs the package, and starts --http mode
# bound to loopback. Open http://127.0.0.1:8000/ for the home page, /health
# for the health check; the MCP endpoint is http://127.0.0.1:8000/mcp.
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${PORT:-8000}"

if [ ! -d .venv ]; then
    echo "Creating virtual environment (.venv)..."
    python3 -m venv .venv
fi

.venv/bin/python -m pip install -q -e .

echo
echo "Home page:    http://127.0.0.1:${PORT}/"
echo "Health:       http://127.0.0.1:${PORT}/health"
echo "MCP endpoint: http://127.0.0.1:${PORT}/mcp"
echo

exec .venv/bin/python -m qcdatabase_mcp --http --host 127.0.0.1 --port "$PORT"
