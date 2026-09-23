"""Unit tests for the drawing metadata update tool."""

from __future__ import annotations

from typing import Any

import pytest

from qcdatabase_mcp import server
from qcdatabase_mcp.client import APIError


def _registered_tool(name: str):
    tools = {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}
    return tools[name]


def _tool(name: str):
    return _registered_tool(name).fn


class _Store:
    def __init__(self, project: dict[str, str] | None = None):
        self.project = project if project is not None else {
            "id": "proj-1",
            "name": "Test Project",
        }

    def get_active_project(self):
        return self.project


class _FakeClient:
    def __init__(self, drawing: Any = None, patch_result: Any = None):
        self.store = _Store()
        self.drawing = (
            {"id": "drawing-1", "project": "proj-1"}
            if drawing is None
            else drawing
        )
        self.patch_result = (
            {"id": "drawing-1", "project": "proj-1"}
            if patch_result is None
            else patch_result
        )
        self.calls: list[tuple[Any, ...]] = []

    def get(self, path):
        self.calls.append(("get", path))
        return self.drawing

    def patch(self, path, json=None):
        self.calls.append(("patch", path, json))
        return self.patch_result


def test_update_drawing_routes_to_detail_patch_and_forwards_allowed_fields(monkeypatch):
    result = {
        "id": "drawing-1",
        "project": "proj-1",
        "title": "Pipe rack isometric",
        "server_added_field": {"kept": True},
    }
    fake = _FakeClient(patch_result=result)
    monkeypatch.setattr(server, "client", lambda: fake)

    out = _tool("update_drawing")(
        "drawing-1",
        drawing_number="ISO-101",
        title="Pipe rack isometric",
        line_number="10-P-100",
        sheet_number="2",
        revision="C",
    )

    assert fake.calls == [
        ("get", "/api/drawings/drawing-1/"),
        (
            "patch",
            "/api/drawings/drawing-1/",
            {
                "drawing_number": "ISO-101",
                "title": "Pipe rack isometric",
                "line_number": "10-P-100",
                "sheet_number": "2",
                "revision": "C",
            },
        ),
    ]
    assert "server_added_field" in out
    assert '"kept": true' in out


def test_update_drawing_omits_none_and_only_sends_blank_allowed_fields(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "client", lambda: fake)

    _tool("update_drawing")(
        "drawing-1",
        drawing_number="",
        line_number="",
        sheet_number="",
    )

    assert fake.calls[-1] == (
        "patch",
        "/api/drawings/drawing-1/",
        {"drawing_number": "", "line_number": "", "sheet_number": ""},
    )


@pytest.mark.parametrize("field", ["title", "revision"])
@pytest.mark.parametrize("value", ["", "   "])
def test_update_drawing_rejects_blank_title_and_revision(monkeypatch, field, value):
    fake = _FakeClient()
    monkeypatch.setattr(server, "client", lambda: fake)

    out = _tool("update_drawing")("drawing-1", **{field: value})

    assert out == f"Error: '{field}' must not be blank."
    assert fake.calls == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("title", 123, "'title' must be a string."),
        ("drawing_number", "D" * 101, "'drawing_number' must be at most 100 characters."),
        ("title", "T" * 256, "'title' must be at most 255 characters."),
        ("line_number", "L" * 101, "'line_number' must be at most 100 characters."),
        ("sheet_number", "S" * 21, "'sheet_number' must be at most 20 characters."),
        ("revision", "R" * 21, "'revision' must be at most 20 characters."),
    ],
)
def test_update_drawing_rejects_invalid_types_and_lengths(
    monkeypatch, field, value, message
):
    fake = _FakeClient()
    monkeypatch.setattr(server, "client", lambda: fake)

    out = _tool("update_drawing")("drawing-1", **{field: value})

    assert out == f"Error: {message}"
    assert fake.calls == []


def test_update_drawing_does_not_patch_when_valid_and_invalid_values_are_mixed(
    monkeypatch,
):
    fake = _FakeClient()
    monkeypatch.setattr(server, "client", lambda: fake)

    out = _tool("update_drawing")(
        "drawing-1",
        drawing_number="ISO-101",
        revision="R" * 21,
    )

    assert out == "Error: 'revision' must be at most 20 characters."
    assert fake.calls == []


def test_update_drawing_omitted_fields_are_not_sent(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "client", lambda: fake)

    _tool("update_drawing")("drawing-1", title="New title")

    assert fake.calls[-1][2] == {"title": "New title"}


def test_update_drawing_noop_does_not_fetch_or_patch(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "client", lambda: fake)

    out = _tool("update_drawing")("drawing-1")

    assert "Nothing to update" in out
    assert fake.calls == []


@pytest.mark.parametrize(
    ("drawing", "message"),
    [
        ({"id": "drawing-1", "project": "proj-other"}, "not the active project"),
        ({"id": "drawing-1"}, "has no project"),
        (["unexpected"], "has no project"),
    ],
)
def test_update_drawing_fails_closed_when_project_mismatches_or_is_missing(
    monkeypatch, drawing, message
):
    fake = _FakeClient(drawing=drawing)
    monkeypatch.setattr(server, "client", lambda: fake)

    out = _tool("update_drawing")("drawing-1", title="Unsafe update")

    assert out.startswith("Error:")
    assert message in out
    assert not any(call[0] == "patch" for call in fake.calls)


def test_update_drawing_requires_active_project(monkeypatch):
    fake = _FakeClient()
    fake.store = _Store(project={})
    monkeypatch.setattr(server, "client", lambda: fake)

    out = _tool("update_drawing")("drawing-1", title="New title")

    assert out.startswith("Error:")
    assert "No project is set" in out
    assert fake.calls == []


@pytest.mark.parametrize("failing_method", ["get", "patch"])
def test_update_drawing_surfaces_api_errors(monkeypatch, failing_method):
    fake = _FakeClient()

    def fail(*args, **kwargs):
        raise APIError("API rejected drawing update")

    setattr(fake, failing_method, fail)
    monkeypatch.setattr(server, "client", lambda: fake)

    out = _tool("update_drawing")("drawing-1", revision="D")

    assert out == "Error: API rejected drawing update"


def test_update_drawing_is_registered_with_mutation_metadata():
    tool = _registered_tool("update_drawing")

    assert tool.title == "Update Drawing"
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is True
    assert tool.annotations.idempotentHint is True