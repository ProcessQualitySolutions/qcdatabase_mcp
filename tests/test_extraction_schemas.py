"""Tests for the drawing / large-format-drawing extraction-schema tools.

The API exposes each project's extraction schema (the fields it pulls out of an
uploaded drawing or LFD) as a single envelope: ``{"type", "schema",
"json_schema"}``. These tools are plain pass-throughs: the whole envelope must
reach the agent via the standard pretty dump, and API errors must come back as
clean text through ``_safe``.

Pure unit tests: the network client is faked. Run: ``pytest`` (from the repo root).
"""

from __future__ import annotations

import pytest

from qcdatabase_mcp import server
from qcdatabase_mcp.client import APIError


def _tool(name: str):
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    return tools[name].fn


class _Store:
    def get_active_project(self):
        return {"id": "proj-1", "name": "Test Project"}


_ENVELOPE = {
    "type": "drawing",
    "schema": {
        "title": "string - the drawing title block text",
        "revision": "string - revision letter or number",
    },
    "json_schema": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"title": {"type": "string"}, "revision": {"type": "string"}},
        "required": ["title"],
    },
}


@pytest.mark.parametrize(
    ("tool_name", "endpoint"),
    [
        ("get_drawing_schema", "/api/projects/proj-1/schemas/drawing/"),
        (
            "get_large_format_drawing_schema",
            "/api/projects/proj-1/schemas/large-format-drawing/",
        ),
    ],
)
def test_schema_tools_pass_full_envelope_through(monkeypatch, tool_name, endpoint):
    seen = {}

    class Fake:
        store = _Store()

        def get(self, path, **params):
            seen["get"] = path
            seen["params"] = params
            return _ENVELOPE

    monkeypatch.setattr(server, "client", lambda: Fake())
    out = _tool(tool_name)()

    assert seen["get"] == endpoint
    assert seen["params"] == {}
    # Full pass-through: envelope keys, raw schema text, and the JSON Schema
    # internals must all reach the agent.
    for token in (
        "type",
        "drawing",
        "schema",
        "title block text",
        "json_schema",
        "draft-07",
        "required",
    ):
        assert token in out, f"{token!r} missing from {tool_name} output"


@pytest.mark.parametrize(
    "tool_name", ["get_drawing_schema", "get_large_format_drawing_schema"]
)
def test_schema_tools_surface_api_errors_as_text(monkeypatch, tool_name):
    class Fake:
        store = _Store()

        def get(self, path, **params):
            err = APIError("Access denied (403). You are signed in but ...")
            err.status_code = 403  # type: ignore[attr-defined]
            raise err

    monkeypatch.setattr(server, "client", lambda: Fake())
    out = _tool(tool_name)()
    assert out.startswith("Error:")
    assert "403" in out


@pytest.mark.parametrize(
    "tool_name", ["get_drawing_schema", "get_large_format_drawing_schema"]
)
def test_schema_tools_require_a_pinned_project(monkeypatch, tool_name):
    class _NoProject:
        def get_active_project(self):
            return None

    class Fake:
        store = _NoProject()

    monkeypatch.setattr(server, "client", lambda: Fake())
    out = _tool(tool_name)()
    assert out.startswith("Error:")
    assert "set_project" in out
