@echo off
rem Run the hosted HTTP server on localhost for testing (Windows, double-clickable).
rem
rem   scripts\run-local.bat          -> http://127.0.0.1:8000
rem   scripts\run-local.bat 9000     -> http://127.0.0.1:9000
rem
rem Creates a .venv on first run, installs the package, and starts --http mode
rem bound to loopback. Open http://127.0.0.1:8000/ for the home page, /health
rem for the health check; the MCP endpoint is http://127.0.0.1:8000/mcp.

setlocal
cd /d "%~dp0.."

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8000"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment ^(.venv^)...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: could not create the virtual environment. Is Python installed and on PATH?
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -m pip install -q -e .
if errorlevel 1 (
    echo ERROR: package install failed.
    pause
    exit /b 1
)

echo.
echo Home page:    http://127.0.0.1:%PORT%/
echo Health:       http://127.0.0.1:%PORT%/health
echo MCP endpoint: http://127.0.0.1:%PORT%/mcp
echo.

".venv\Scripts\python.exe" -m qcdatabase_mcp --http --host 127.0.0.1 --port %PORT%
pause
