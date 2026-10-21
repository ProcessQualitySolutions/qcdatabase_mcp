"""Regression tests for issues #8, #10 and #11.

* #8: the hosted home page states the real number of registered tools.
* #10: the QR tools' scopes are requested, and a 403 caused by a missing scope
  says so instead of blaming project membership.
* #11: signed file URLs survive the 600-char trim, and the hosted download tools
  can return a link instead of an attached blob (for clients that mangle it).
"""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from qcdatabase_mcp import pages, server
from qcdatabase_mcp.auth import DEFAULT_SCOPES
from qcdatabase_mcp.client import APIError, QCClient


def _tool(name: str):
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    return tools[name].fn


# ---------------------------------------------------------------------------
# #8 tool count
# ---------------------------------------------------------------------------
def test_home_page_states_the_registered_tool_count(monkeypatch):
    monkeypatch.setenv("QCDB_MCP_RESOURCE_URL", "https://mcp.example.com")
    mcp = FastMCP("test")
    for i in range(3):
        mcp.add_tool(lambda: None, name=f"t{i}")
    pages.register_pages(mcp)
    by_path = {route.path: route for route in mcp._custom_starlette_routes}
    body = asyncio.run(by_path["/"].endpoint(None)).body.decode()
    assert "3 tools span the areas below" in body
    assert "__TOOL_COUNT__" not in body


def test_home_page_without_a_count_avoids_a_stale_number(monkeypatch):
    monkeypatch.setenv("QCDB_MCP_RESOURCE_URL", "https://mcp.example.com")
    html = pages.home_page_html()
    assert "More than 100 tools" in html and "80 tools" not in html


# ---------------------------------------------------------------------------
# #10 QR scopes and the missing-scope 403
# ---------------------------------------------------------------------------
def test_qr_scopes_are_requested():
    assert {"qr:read", "qr:write"} <= set(DEFAULT_SCOPES.split())


def _client_returning(response: httpx.Response) -> QCClient:
    http = httpx.Client(transport=httpx.MockTransport(lambda r: response),
                        base_url="https://qcdatabase.ai")
    return QCClient(store=object(), access_token="tok", http=http)


def test_403_names_the_missing_scope_from_the_challenge():
    c = _client_returning(httpx.Response(
        403, headers={"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="qr:write"'},
        json={"detail": "nope"}))
    with pytest.raises(APIError) as ei:
        c.post("/api/qr/generate/", json={})
    assert "scope qr:write" in str(ei.value)
    assert "not a member" not in str(ei.value)


def test_403_recognises_a_scope_message_without_a_named_scope():
    c = _client_returning(httpx.Response(
        403, json={"detail": "The access token does not grant the scope required for this request."}))
    with pytest.raises(APIError) as ei:
        c.post("/api/qr/generate/", json={})
    assert "OAuth scope" in str(ei.value) and "not a member" not in str(ei.value)


def test_other_403s_keep_the_membership_message():
    c = _client_returning(httpx.Response(403, json={"detail": "You are not a member."}))
    with pytest.raises(APIError) as ei:
        c.get("/api/x/")
    assert "not a member of this project" in str(ei.value)


# ---------------------------------------------------------------------------
# #11 URLs and link delivery
# ---------------------------------------------------------------------------
def test_pretty_keeps_long_signed_urls_whole():
    url = "https://storage.example.com/drawings/sht23.png?" + "X-Sig=" + "a" * 1200
    out = server._pretty({"image_file": url, "notes": "n" * 1200})
    assert url in out
    assert out.count("... (truncated)") == 1  # the long non-URL text is still trimmed


def test_fmt_value_keeps_long_urls_whole():
    url = "https://storage.example.com/x.pdf?sig=" + "b" * 900
    assert server._fmt_value(url) == url


class _Store:
    def get_active_project(self):
        return {"id": "proj-1", "name": "Test Project"}


class _Uploads:
    def __init__(self):
        self.stored = []

    def store_download(self, content, name):
        self.stored.append((name, content))
        return f"https://mcp.example.com/files/{name}?t=1"


def _image_env(data: bytes):
    return {"success": True, "filename": "sht23.png", "encoding": "base64",
            "data": base64.b64encode(data).decode("ascii"), "width": 40, "height": 30}


@pytest.fixture
def hosted_with_uploads(monkeypatch):
    uploads = _Uploads()
    monkeypatch.setattr(server.hosted, "hosted_enabled", lambda: True)
    monkeypatch.setattr(server, "_uploads", uploads)

    class Fake:
        store = _Store()

        def get(self, path, **params):
            return _image_env(b"\x89PNG real bytes")

    monkeypatch.setattr(server, "client", lambda: Fake())
    return uploads


def test_drawing_image_as_link_returns_a_download_link(hosted_with_uploads):
    out = _tool("get_drawing_image")("dr-1", as_link=True)
    assert isinstance(out, str)
    assert "https://mcp.example.com/files/sht23.png?t=1" in out
    assert "40x30 px" in out  # the coordinate note still comes first
    assert hosted_with_uploads.stored == [("sht23.png", b"\x89PNG real bytes")]


def test_drawing_image_defaults_to_an_attached_resource(hosted_with_uploads):
    out = _tool("get_drawing_image")("dr-1")
    assert isinstance(out, list)
    assert hosted_with_uploads.stored == []


def test_export_drawing_as_link(hosted_with_uploads):
    out = _tool("export_drawing")("dr-1", as_link=True)
    assert "https://mcp.example.com/files/" in out
