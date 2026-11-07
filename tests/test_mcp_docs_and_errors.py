"""Error messages and tool-doc fixes: #32 (create_map_item label), #33
(test_pressure), #34 (403 scope / 400 hint / bulk-update details), #37
(shipper search), #38 (note page_url).

The HTTP client is faked with httpx.MockTransport. No live API calls.
"""

from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from qcdatabase_mcp import server
from qcdatabase_mcp.client import APIError, QCClient


class _Store:
    def get_active_project(self):
        return {"id": "p1", "name": "P"}

    def clear_token(self, **_):
        pass


class _Calls(list):
    def __init__(self):
        super().__init__()
        self.responses: dict[tuple[str, str], object] = {}

    def bodies(self, method):
        return [json.loads(r.content) if r.content else None for r in self if r.method == method]


@pytest.fixture
def calls(monkeypatch):
    seen = _Calls()

    def handler(request: httpx.Request):
        request.read()
        seen.append(request)
        body = seen.responses.get((request.method, request.url.path), {"id": "x"})
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)

    http = httpx.Client(base_url="https://qc.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "client", lambda: QCClient(store=_Store(), access_token="tok", http=http))
    monkeypatch.setattr(server, "require_project", lambda: "p1")
    return seen


def _client_returning(response: httpx.Response) -> QCClient:
    http = httpx.Client(transport=httpx.MockTransport(lambda r: response),
                        base_url="https://qcdatabase.ai")
    return QCClient(store=_Store(), access_token="tok", http=http)


def _error(response: httpx.Response) -> str:
    with pytest.raises(APIError) as ei:
        _client_returning(response).post("/api/x/", json={})
    return str(ei.value)


def _tools():
    return {t.name: t for t in server.mcp._tool_manager.list_tools()}


# --- #34 part 3: 403 names the scope (qcdatabase.ai #126 shape) ------------------
def test_403_insufficient_scope_names_scope_and_says_reconnect():
    msg = _error(httpx.Response(
        403,
        headers={"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="shippers:write"'},
        json={"error": "insufficient_scope", "required_scope": "shippers:write",
              "detail": "This action needs the shippers:write scope."},
    ))
    assert "shippers:write" in msg
    assert "Reconnecting" in msg
    assert "not a member" not in msg


def test_403_scope_from_body_only():
    msg = _error(httpx.Response(
        403, json={"error": "insufficient_scope", "required_scope": "qr:write", "detail": "x"}))
    assert "missing OAuth scope qr:write" in msg


def test_other_403_is_membership_not_scope():
    msg = _error(httpx.Response(403, json={"detail": "You do not have permission to perform this action."}))
    assert "not a member" in msg or "lacks the role" in msg
    assert "Reconnecting" not in msg and "missing OAuth scope " not in msg.split("(this is not")[0]
    assert "You do not have permission" in msg


# --- #34 part 2: the id hint only when the error is about an id ------------------
def test_400_value_error_has_no_foreign_id_hint():
    msg = _error(httpx.Response(400, json={"test_pressure": ["A valid number is required."]}))
    assert "different project" not in msg
    assert "test_pressure: A valid number is required." in msg


def test_400_unknown_id_keeps_the_hint():
    msg = _error(httpx.Response(400, json={"job": ['Invalid pk "abc" - object does not exist.']}))
    assert "different project or organization" in msg


# --- #34 part 1: bulk_update_map_items surfaces per-item details -----------------
def test_bulk_update_shows_lock_and_enum_details(calls):
    calls.responses[("POST", "/api/mapping/items/bulk-update/")] = httpx.Response(400, json={
        "error": "Validation failed",
        "details": [
            'Item 1 ("WN-58"): This item has 1 active lock (quality hold) that must be unlocked '
            "before it can be edited, deleted, completed, or accepted.",
            'Item 2 ("WN-59"): schedule: "40" is not one of [\'Sch 40\', \'Sch 80\']',
        ],
    })
    with pytest.raises(ToolError) as ei:
        server.bulk_update_map_items("d1", "s1", json.dumps([
            {"id": "a", "data": {"size": "6"}}, {"id": "b", "data": {"schedule": "40"}}]))
    msg = str(ei.value)
    assert "Validation failed" in msg
    assert "active lock" in msg and "WN-58" in msg
    assert "Sch 40" in msg and "WN-59" in msg
    assert "different project" not in msg


def test_bulk_update_unknown_id_keeps_hint(calls):
    calls.responses[("POST", "/api/mapping/items/bulk-update/")] = httpx.Response(400, json={
        "error": "Validation failed",
        "details": ['Item 1: no map item "zz" on this drawing with this schema.'],
    })
    with pytest.raises(ToolError) as ei:
        server.bulk_update_map_items("d1", "s1", json.dumps([{"id": "zz", "data": {}}]))
    assert "no map item" in str(ei.value) and "different project" in str(ei.value)


def test_field_errors_and_validation_errors_are_itemized():
    msg = _error(httpx.Response(400, json={"error": "Validation failed.",
                                            "field_errors": {"source": "Unknown source."}}))
    assert "source: Unknown source." in msg
    msg = _error(httpx.Response(400, json={"error": "Validation failed.",
                                            "validation_errors": ["weld_count must be an integer"]}))
    assert "weld_count must be an integer" in msg


def test_lock_docs_say_edits_and_deletes_are_blocked():
    tools = _tools()
    assert "edit or delete" in tools["add_lock"].description
    assert "lock" in tools["update_map_item"].description
    assert "active lock" in tools["bulk_update_map_items"].description


# --- #32 create_map_item reports the stored label --------------------------------
def test_create_map_item_reports_auto_numbered_label(calls):
    calls.responses[("POST", "/api/mapping/items/")] = {"id": "m1", "label": "WN-59"}
    out = server.create_map_item("d1", "s1", "ZZ-RETEST-W2")
    head = out.splitlines()[0]
    assert head.startswith("Created map item 'WN-59'.")
    assert "auto-numbering" in head and "ZZ-RETEST-W2" in head


def test_create_map_item_same_label_has_no_rename_note(calls):
    calls.responses[("POST", "/api/mapping/items/")] = {"id": "m1", "label": "W-1"}
    head = server.create_map_item("d1", "s1", "W-1").splitlines()[0]
    assert head == "Created map item 'W-1'."
    assert "auto-numbering" in _tools()["create_map_item"].description


# --- #33 test_pressure is a bare number ------------------------------------------
@pytest.mark.parametrize("value, sent", [(150, "150.00"), (150.5, "150.50"), ("150", "150.00")])
def test_test_pressure_numbers_are_sent_as_decimal(calls, value, sent):
    server.update_package("pk1", test_pressure=value)
    assert calls.bodies("PATCH") == [{"test_pressure": sent}]


@pytest.mark.parametrize("value", ["150 psig", "150psi", "1.234", "123456789"])
def test_test_pressure_rejects_units_and_bad_precision(calls, value):
    with pytest.raises(ToolError, match="test_pressure"):
        server.update_package("pk1", test_pressure=value)
    assert calls == []


def test_create_package_test_pressure_and_schema(calls):
    server.create_package("j1", "N", "C", test_pressure=200)
    assert calls.bodies("POST")[0]["test_pressure"] == "200.00"
    tools = _tools()
    for name in ("create_package", "update_package"):
        prop = tools[name].parameters["properties"]["test_pressure"]
        types = {t.get("type") for t in prop.get("anyOf", [prop])}
        assert "number" in types and "string" not in types, (name, prop)
        assert "no units" in tools[name].description


def test_test_pressure_can_still_be_cleared(calls):
    server.update_package("pk1", clear="test_pressure")
    assert calls.bodies("PATCH") == [{"test_pressure": None}]


# --- #37 shipper search ----------------------------------------------------------
# The API filters since qcdatabase.ai #153; the tools pass 'search' through and
# render what comes back (no client-side filter).
def test_list_shippers_search_is_sent_to_the_api(calls):
    calls.responses[("GET", "/api/shippers/projects/p1/")] = [{"id": "s1", "title": "ZZ Demo BOL 001"}]
    out = server.list_shippers(search="  bol 001 ", drawing_id="d9")
    params = dict(calls[0].url.params)
    assert params["search"] == "bol 001" and params["drawing"] == "d9"
    assert "matching 'bol 001'" in out and "s1" in out
    assert "semantic_search" in _tools()["list_shippers"].description


def test_list_shippers_without_search_sends_no_param(calls):
    calls.responses[("GET", "/api/shippers/projects/p1/")] = []
    server.list_shippers()
    assert "search" not in dict(calls[0].url.params)


def test_list_shipper_line_items_search_is_sent_to_the_api(calls):
    calls.responses[("GET", "/api/shippers/projects/p1/sh1/line-items/")] = [
        {"id": "l1", "item_description": "Gasket kit"}]
    out = server.list_shipper_line_items("sh1", search="GASKET")
    assert dict(calls[0].url.params)["search"] == "GASKET"
    assert "l1" in out


def test_list_shipper_line_items_without_search_sends_no_param(calls):
    calls.responses[("GET", "/api/shippers/projects/p1/sh1/line-items/")] = []
    server.list_shipper_line_items("sh1")
    assert "search" not in dict(calls[0].url.params)


# --- #38 create_note page_url ------------------------------------------------------
def test_create_note_accepts_relative_page_url(calls):
    server.create_note("hi", page_url="/projects/p1/drawings/")
    assert calls.bodies("POST")[0]["page_url"] == "/projects/p1/drawings/"


@pytest.mark.parametrize("url", ["https://qcdatabase.ai/projects/p1/itp/", "//evil.test/x",
                                 "projects/p1/", "javascript:alert(1)"])
def test_create_note_rejects_non_relative_page_url(calls, url):
    with pytest.raises(ToolError, match="relative app path"):
        server.create_note("hi", page_url=url)
    assert calls == []
    assert "starting with a single '/'" in _tools()["create_note"].description
