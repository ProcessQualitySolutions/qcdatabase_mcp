"""Exercise tool results through the SDK's MCP client/server protocol boundary."""

import asyncio
import base64

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import EmbeddedResource, TextContent

from qcdatabase_mcp import server
from qcdatabase_mcp.auth import AuthError
from qcdatabase_mcp.client import APIError
from qcdatabase_mcp.config import SessionChangedError


def _call(name, arguments=None, *, mcp=None):
    async def run():
        async with create_connected_server_and_client_session(mcp or server.mcp) as session:
            return await session.call_tool(name, arguments or {})

    return asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [
        AuthError("Sign in again"),
        SessionChangedError("The saved login changed; select a project again"),
        APIError("Access denied (403)"),
        server.NeedsProject("Choose a project"),
        OSError("Cannot write the file"),
        ValueError("Invalid tool argument"),
    ],
)
def test_known_errors_are_protocol_errors(monkeypatch, error):
    def fail():
        raise error

    wrapped = server._safe(fail)
    with pytest.raises(ToolError) as exc:
        wrapped()
    assert str(exc.value) == str(error)
    assert exc.value.__cause__ is error

    monkeypatch.setattr(server, "client", fail)
    result = _call("get_drawing_schema")
    assert result.isError is True
    assert result.structuredContent is None
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    assert str(error) in result.content[0].text


def test_safe_does_not_catch_unexpected_programming_errors():
    @server._safe
    def fail():
        raise TypeError("Programming bug")

    with pytest.raises(TypeError, match="Programming bug"):
        fail()


class _Store:
    def get_active_project(self):
        return {"id": "project-1"}


def test_normal_tool_text_is_not_duplicated_as_structured_json(monkeypatch):
    envelope = {"type": "drawing", "schema": {"revision": "string"}}

    class Fake:
        store = _Store()

        def get(self, path, **params):
            return envelope

    monkeypatch.setattr(server, "client", lambda: Fake())
    expected = server.get_drawing_schema()
    result = _call("get_drawing_schema")
    assert result.isError is False
    assert result.structuredContent is None
    assert result.content == [TextContent(type="text", text=expected)]


def test_binary_download_content_blocks_survive_protocol_unchanged(monkeypatch):
    payload = b"%PDF-1.7\n\x00\xffbinary\n%%EOF"

    class Fake:
        store = _Store()

        def get(self, path, **params):
            return {
                "success": True,
                "encoding": "base64",
                "filename": "drawing.pdf",
                "data": base64.b64encode(payload).decode("ascii"),
            }

    monkeypatch.setattr(server, "client", lambda: Fake())
    monkeypatch.setattr(server.hosted, "hosted_enabled", lambda: True)
    arguments = {"drawing_id": "drawing-1", "save_path": "drawing.pdf"}
    expected = server.export_drawing(**arguments)
    result = _call("export_drawing", arguments)
    assert result.isError is False
    assert result.structuredContent is None
    assert result.content == expected
    assert len(result.content) == 2
    assert isinstance(result.content[0], TextContent)
    assert isinstance(result.content[1], EmbeddedResource)
    assert base64.b64decode(result.content[1].resource.blob) == payload
    assert result.content[1].resource.mimeType == "application/pdf"


def test_tool_helper_preserves_explicit_structured_output(monkeypatch):
    isolated = FastMCP("structured-output-regression")
    monkeypatch.setattr(server, "mcp", isolated)

    @server._tool("Explicit Structured Output", structured_output=True)
    def structured() -> dict[str, str]:
        return {"value": "retained"}

    result = _call("structured", mcp=isolated)
    assert result.isError is False
    assert result.structuredContent == {"value": "retained"}


def test_signoff_annotations_and_confirmation_instructions_on_wire():
    async def run():
        async with create_connected_server_and_client_session(server.mcp) as session:
            return await session.list_tools()

    tools = {tool.name: tool for tool in asyncio.run(run()).tools}
    for name in (
        "mark_map_item_complete",
        "mark_map_item_accepted",
        "complete_form_submission",
        "mark_itp_complete",
        "mark_itp_accepted",
        "unlock_lock",
    ):
        tool = tools[name]
        assert tool.annotations.readOnlyHint is False, name
        assert tool.annotations.destructiveHint is True, name
        assert "confirm" in tool.description or "explicit go-ahead" in tool.description, name
        if name != "unlock_lock":
            assert tool.annotations.idempotentHint is True, name
