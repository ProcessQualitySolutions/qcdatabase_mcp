"""Regression tests for issue #16: map item create/update cover the fields the
API accepts, and sign-off statuses stay behind the mark_* tools.

update_map_item lives in the public server so it no longer depends on the
private hosted module, which registers after us (FastMCP keeps the first tool
of a name, so this one wins on the hosted server).
"""

from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from qcdatabase_mcp import server
from qcdatabase_mcp.client import QCClient


class _Store:
    def get_active_project(self):
        return None

    def clear_token(self):
        pass


@pytest.fixture
def calls(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request):
        request.read()
        if request.method != "GET":
            seen.append(request)
        return httpx.Response(200, json={"id": "m1", "project": "p1"})

    http = httpx.Client(base_url="https://qc.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "client", lambda: QCClient(store=_Store(), access_token="tok", http=http))
    monkeypatch.setattr(server, "require_project", lambda: "p1")
    return seen


def _body(req: httpx.Request) -> dict:
    return json.loads(req.content)


def test_update_map_item_is_a_public_tool():
    names = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert "update_map_item" in names


def test_update_sends_only_supplied_fields(calls):
    server.update_map_item("m1", notes="Re-ground", assigned_to="u1", status="in_progress",
                           flag_rotation=90, sheet_number=2, label="")
    assert (calls[0].method, calls[0].url.path) == ("PATCH", "/api/mapping/items/m1/")
    assert _body(calls[0]) == {"notes": "Re-ground", "assigned_to": "u1", "status": "in_progress",
                               "flag_rotation": 90, "sheet_number": 2}


def test_zero_coordinates_are_sent(calls):
    server.update_map_item("m1", x_position=0.0, y_position=0.0)
    assert _body(calls[0]) == {"x_position": 0.0, "y_position": 0.0}


def test_clear_second_point_and_assignee(calls):
    server.update_map_item("m1", clear="x_position_2,y_position_2,assigned_to")
    assert _body(calls[0]) == {"x_position_2": None, "y_position_2": None, "assigned_to": None}


def test_second_point_must_come_in_pairs(calls):
    with pytest.raises(ToolError):
        server.update_map_item("m1", x_position_2=10.0)
    with pytest.raises(ToolError):
        server.update_map_item("m1", clear="x_position_2")
    assert calls == []


@pytest.mark.parametrize("status, tool", [("completed", "mark_map_item_complete"),
                                          ("Accepted", "mark_map_item_accepted")])
def test_sign_off_statuses_are_refused(calls, status, tool):
    with pytest.raises(ToolError, match=tool):
        server.update_map_item("m1", status=status)
    with pytest.raises(ToolError, match=tool):
        server.create_map_item("d1", "s1", "W1", x_position=1.0, y_position=2.0, status=status)
    assert calls == []


def test_unknown_status_is_refused(calls):
    with pytest.raises(ToolError, match="planned"):
        server.update_map_item("m1", status="done")
    assert calls == []


def test_nothing_to_update(calls):
    assert server.update_map_item("m1").startswith("Nothing to update")
    assert calls == []


def test_drawing_and_schema_are_not_parameters():
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    props = tools["update_map_item"].parameters["properties"]
    assert not {"drawing", "drawing_id", "schema", "schema_id", "project"} & set(props)
    assert "web app" in tools["update_map_item"].description


def test_create_accepts_assignee_status_and_bim_id(calls):
    server.create_map_item("d1", "s1", "W1", x_position=1.0, y_position=2.0,
                           assigned_to="u1", status="planned", bim_component_id="IFC-42")
    body = _body(calls[0])
    assert body["assigned_to"] == "u1"
    assert body["status"] == "planned"
    assert body["bim_component_id"] == "IFC-42"
