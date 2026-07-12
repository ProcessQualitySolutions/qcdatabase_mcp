"""Hosted-mode web pages and transport-hardening config.

Covers what a broken deployment regresses on quietly: the home page advertising
the right MCP endpoint, /health staying secret-free, and the stateless/JSON
transport defaults that keep the server working behind proxies and restarts.
"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from qcdatabase_mcp import hosted, pages


# ---------------------------------------------------------------------------
# Transport hardening defaults (proxy/restart survival)
# ---------------------------------------------------------------------------
def test_stateless_and_json_default_on(monkeypatch):
    monkeypatch.delenv("QCDB_MCP_STATELESS", raising=False)
    monkeypatch.delenv("QCDB_MCP_JSON_RESPONSE", raising=False)
    assert hosted.stateless_enabled() is True
    assert hosted.json_response_enabled() is True


def test_stateless_and_json_can_opt_out(monkeypatch):
    monkeypatch.setenv("QCDB_MCP_STATELESS", "0")
    monkeypatch.setenv("QCDB_MCP_JSON_RESPONSE", "false")
    assert hosted.stateless_enabled() is False
    assert hosted.json_response_enabled() is False


# ---------------------------------------------------------------------------
# Home page & health
# ---------------------------------------------------------------------------
def test_home_page_advertises_mcp_endpoint(monkeypatch):
    monkeypatch.setenv("QCDB_MCP_RESOURCE_URL", "https://mcp.example.com")
    html = pages.home_page_html()
    assert "https://mcp.example.com/mcp" in html
    assert "Connectors" in html  # the connect instructions are present


def test_home_page_escapes_configured_urls(monkeypatch):
    # Operator-controlled, but must still never break out of the HTML.
    monkeypatch.setenv("QCDB_MCP_RESOURCE_URL", 'https://x"><script>alert(1)</script>')
    html = pages.home_page_html()
    assert "<script>alert(1)</script>" not in html


def test_health_payload_static_facts_only(monkeypatch):
    monkeypatch.setenv("QCDB_MCP_RESOURCE_URL", "https://mcp.example.com")
    payload = pages.health_payload()
    assert payload["status"] == "ok"
    assert payload["mcp_endpoint"] == "https://mcp.example.com/mcp"
    # Nothing token- or user-shaped may ever appear here.
    assert not any("token" in k or "user" in k for k in payload)


def test_register_pages_adds_public_routes():
    mcp = FastMCP("test")
    pages.register_pages(mcp)
    paths = {route.path for route in mcp._custom_starlette_routes}
    assert {"/", "/health"} <= paths


def test_home_route_serves_html(monkeypatch):
    monkeypatch.setenv("QCDB_MCP_RESOURCE_URL", "https://mcp.example.com")
    mcp = FastMCP("test")
    pages.register_pages(mcp)
    by_path = {route.path: route for route in mcp._custom_starlette_routes}
    response = asyncio.run(by_path["/"].endpoint(None))
    assert response.status_code == 200
    assert b"https://mcp.example.com/mcp" in response.body
