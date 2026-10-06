"""Package moves must issue one focused PATCH, never upload or copy a drawing."""

import json

import pytest

from qcdatabase_mcp import server
from qcdatabase_mcp.client import APIError

D = "11111111-1111-4111-8111-111111111111"
P = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"


class Fake:
    def __init__(self):
        self.store = self
        self.project = {"id": PROJECT}
        self.records = {
            f"/api/drawings/{D}/": {"id": D, "project": PROJECT, "package": None},
            f"/api/packages/{P}/": {"id": P, "project": PROJECT},
        }
        self.saved = {
            **self.records[f"/api/drawings/{D}/"], "package": P,
            "drawing_number": "D-1", "title": "Original", "revision": "A",
            "line_number": "L1", "sheet_number": "2",
            "server_extension": {"arbitrary": [1, 2]}, "file": "original.pdf",
        }
        self.calls = []
        self.failure = None

    def get_active_project(self):
        return self.project

    def get(self, path):
        self.calls.append(("GET", path))
        if self.failure == path:
            raise APIError(self.message)
        return self.records[path]

    def patch(self, path, json):
        self.calls.append(("PATCH", path, json))
        if self.failure == "PATCH":
            raise APIError(self.message)
        return self.saved


@pytest.fixture
def api(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(server, "client", lambda: fake)
    return fake


def move(drawing=D, package=P):
    return server.move_drawing_to_package(drawing, package)


def test_exact_requests_and_full_saved_record(api):
    out = move()
    assert api.calls == [
        ("GET", f"/api/drawings/{D}/"),
        ("GET", f"/api/packages/{P}/"),
        ("PATCH", f"/api/drawings/{D}/", {"package": P}),
    ]
    assert json.loads(out.split("\n\n", 1)[1]) == api.saved


@pytest.mark.parametrize("field", ["drawing", "package"])
@pytest.mark.parametrize("value", ["", " ", "../schemas/map-items", "x?y=1", "not-uuid", None, 123])
def test_invalid_ids_never_reach_api(api, field, value):
    out = move(**{field: value})
    assert "must be a nonempty UUID" in out
    assert api.calls == []


def test_requires_project(api):
    api.project = None
    assert "No project is set" in move()
    assert api.calls == []


@pytest.mark.parametrize("path", [f"/api/drawings/{D}/", f"/api/packages/{P}/"])
@pytest.mark.parametrize("record", [{}, None, [], {"project": "other"}, {"project": {}}])
def test_bad_or_foreign_records_fail_closed(api, path, record):
    api.records[path] = record
    assert move().startswith("Error:")
    assert not any(call[0] == "PATCH" for call in api.calls)


@pytest.mark.parametrize("stage", [f"/api/drawings/{D}/", f"/api/packages/{P}/", "PATCH"])
@pytest.mark.parametrize("message", ["Not found (404)", "Access denied (403)", "Rejected (400)", "Unavailable (503)"])
def test_api_errors_surface_without_success(api, stage, message):
    api.failure, api.message = stage, message
    assert move() == f"Error: {message}"
    if stage != "PATCH":
        assert not any(call[0] == "PATCH" for call in api.calls)


@pytest.mark.parametrize("nested", [False, True])
def test_noop_still_validates_destination_and_returns_current(api, nested):
    current = api.records[f"/api/drawings/{D}/"]
    current["package"] = {"id": P} if nested else P
    for record in api.records.values():
        record["project"] = {"id": PROJECT} if nested else PROJECT
    out = move()
    assert "already assigned" in out
    assert json.loads(out.split("\n\n", 1)[1]) == current
    assert api.calls == [("GET", f"/api/drawings/{D}/"), ("GET", f"/api/packages/{P}/")]


def test_move_annotations_and_metadata_interface():
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    tool = tools["move_drawing_to_package"]
    assert not tool.annotations.readOnlyHint
    assert tool.annotations.destructiveHint
    assert tool.annotations.idempotentHint
    assert set(tool.parameters["properties"]) == {"drawing_id", "destination_package_id"}
    assert set(tools["update_drawing"].parameters["properties"]) == {
        "drawing_id", "drawing_number", "title", "line_number", "sheet_number", "revision",
    }
