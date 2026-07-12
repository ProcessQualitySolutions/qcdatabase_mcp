# Run the hosted HTTP server on localhost for testing (Windows).
#
#   .\scripts\run-local.ps1            # http://127.0.0.1:8000
#   .\scripts\run-local.ps1 -Port 9000
#
# Creates a .venv on first run, installs the package, and starts --http mode
# bound to loopback. Open http://127.0.0.1:8000/ for the home page, /health
# for the health check; the MCP endpoint is http://127.0.0.1:8000/mcp.

param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment (.venv)..."
    python -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install -q -e .

Write-Host ""
Write-Host "Home page:    http://127.0.0.1:$Port/"
Write-Host "Health:       http://127.0.0.1:$Port/health"
Write-Host "MCP endpoint: http://127.0.0.1:$Port/mcp"
Write-Host ""

& ".venv\Scripts\python.exe" -m qcdatabase_mcp --http --host 127.0.0.1 --port $Port
