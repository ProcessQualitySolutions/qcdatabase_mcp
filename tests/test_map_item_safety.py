"""Retain local project and input safeguards with upstream's richer PATCH tool."""

from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from qcdatabase_mcp import server


@pytest.fixture
def api(monkeypatch):
    calls = []
    record = {"id": "item", "project": "project"}
    def get(path):
        calls.append(("get", path))
        return record
    def patch(path, json):
        calls.append(("patch", path, json))
        return {**record, **json, "extra": "preserved"}
    fake = SimpleNamespace(store=SimpleNamespace(get_active_project=lambda: {"id": "project"}),
                           get=get, patch=patch)
    monkeypatch.setattr(server, "client", lambda: fake)
    return calls, record


def test_update_payload_omissions_zero_and_passthrough(api):
    calls, _ = api
    out = server.update_map_item("item", label="W2", x_position=0, x_position_2=10, y_position_2=20)
    assert calls == [("get", "/api/mapping/items/item/"),
                     ("patch", "/api/mapping/items/item/",
                      {"label": "W2", "x_position": 0, "x_position_2": 10, "y_position_2": 20})]
    assert "preserved" in out


@pytest.mark.parametrize("values", [{"label": " "}, {"label": "x"*101},
                                   {"x_position": float("nan")}, {"y_position": float("inf")},
                                   {"x_position": True}])
def test_invalid_values_never_call_api(api, values):
    with pytest.raises(ToolError):
        server.update_map_item("item", **values)
    assert not api[0]


@pytest.mark.parametrize("project", ["other", None, {}, {"id": "other"}])
def test_project_guard(api, project):
    api[1]["project"] = project
    with pytest.raises(ToolError, match="active-project"):
        server.update_map_item("item", label="W3")
    assert len(api[0]) == 1


def test_requires_active_project(api, monkeypatch):
    monkeypatch.setattr(server, "require_project", lambda: (_ for _ in ()).throw(
        server.NeedsProject("Select a project")))
    with pytest.raises(ToolError, match="Select a project"):
        server.update_map_item("item", notes="Updated")
    assert not api[0]


def test_noop_preserves_upstream_blank_omission(api):
    assert "Nothing to update" in server.update_map_item("item", label="")
    assert not api[0]
