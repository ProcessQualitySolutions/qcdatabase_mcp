"""Regression tests for issue #15: update tools for jobs, packages, line specs
and projects, plus the job/package create fields the API accepts.

The update tools PATCH only the fields that were passed: a blank argument never
wipes a field, emptying one takes an explicit 'clear', and metadata is merged
into what is stored rather than replacing it.
"""

from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from qcdatabase_mcp import server
from qcdatabase_mcp.client import QCClient


class _Store:
    def __init__(self):
        self.active = {"id": "p1", "name": "Old name"}

    def get_active_project(self):
        return self.active

    def set_active_project(self, record):
        self.active = record

    def clear_token(self):
        pass


class _Calls(list):
    def __init__(self):
        super().__init__()
        self.responses: dict[tuple[str, str], object] = {}
        self.store = _Store()

    def bodies(self, method: str) -> list[dict]:
        return [json.loads(r.content) for r in self if r.method == method]


@pytest.fixture
def calls(monkeypatch):
    seen = _Calls()

    def handler(request: httpx.Request):
        request.read()
        seen.append(request)
        return httpx.Response(200, json=seen.responses.get((request.method, request.url.path), {"id": "x"}))

    http = httpx.Client(base_url="https://qc.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "client", lambda: QCClient(store=seen.store, access_token="tok", http=http))
    monkeypatch.setattr(server, "require_project", lambda: "p1")
    return seen


def test_update_job_sends_only_supplied_fields(calls):
    server.update_job("j1", due_date="2026-12-01", priority="high", description="")
    assert [(r.method, r.url.path) for r in calls] == [("PATCH", "/api/jobs/j1/")]
    assert calls.bodies("PATCH") == [{"due_date": "2026-12-01", "priority": "high"}]


def test_clear_empties_named_fields(calls):
    server.update_job("j1", clear="due_date, po_number")
    assert calls.bodies("PATCH") == [{"due_date": None, "po_number": ""}]


def test_clear_rejects_unknown_or_conflicting_fields(calls):
    with pytest.raises(ToolError):
        server.update_job("j1", clear="name")
    with pytest.raises(ToolError):
        server.update_job("j1", due_date="2026-12-01", clear="due_date")
    assert calls == []


def test_nothing_to_update_sends_nothing(calls):
    for out in (server.update_job("j1"), server.update_package("k1"),
                server.update_line_spec("l1"), server.update_project()):
        assert out.startswith("Nothing to update")
    assert calls == []


def test_metadata_is_merged_into_existing(calls):
    calls.responses[("GET", "/api/packages/k1/")] = {"metadata": {"area": "N", "unit": "2", "old": 1}}
    server.update_package("k1", metadata='{"unit": "3", "old": null, "new": true}')
    assert [r.method for r in calls] == ["GET", "PATCH"]
    assert calls.bodies("PATCH") == [{"metadata": {"area": "N", "unit": "3", "new": True}}]


def test_metadata_must_be_an_object(calls):
    with pytest.raises(ToolError):
        server.update_job("j1", metadata="[1, 2]")


def test_update_package_pcf_and_status(calls):
    server.update_package("k1", status="testing", pcf_north_indicator_on=False, pcf_north_axis="X")
    assert calls.bodies("PATCH") == [{"status": "testing", "pcf_north_indicator_on": False,
                                     "pcf_north_axis": "X"}]


def test_update_line_spec(calls):
    server.update_line_spec("l1", status="archived", clear="requirements")
    assert calls[0].url.path == "/api/line-specs/l1/"
    assert calls.bodies("PATCH") == [{"status": "archived", "requirements": ""}]


def test_update_project_defaults_to_active_and_refreshes_its_name(calls):
    server.update_project(name="New name", end_date="2027-03-31")
    assert calls[0].url.path == "/api/projects/p1/"
    assert calls.bodies("PATCH") == [{"name": "New name", "end_date": "2027-03-31"}]
    assert calls.store.active == {"id": "p1", "name": "New name"}


def test_update_other_project_leaves_active_name(calls):
    server.update_project(project_id="p2", name="Other")
    assert calls[0].url.path == "/api/projects/p2/"
    assert calls.store.active["name"] == "Old name"


def test_create_job_accepts_scheduling_fields(calls):
    server.create_job("Pipe rack", "J-1", po_number="PO-9", wo_number="WO-4",
                      priority="low", start_date="2026-11-01", due_date="2026-12-01")
    assert calls.bodies("POST") == [{"project": "p1", "name": "Pipe rack", "code": "J-1",
                                     "po_number": "PO-9", "wo_number": "WO-4", "priority": "low",
                                     "start_date": "2026-11-01", "due_date": "2026-12-01"}]


def test_create_package_accepts_status_metadata_and_pcf(calls):
    server.create_package("j1", "Hydro 1", "HP-1", status="open", metadata='{"area": "N"}',
                          pcf_north_indicator_on=True, pcf_north_axis="Y")
    assert calls.bodies("POST") == [{"project": "p1", "job": "j1", "name": "Hydro 1", "code": "HP-1",
                                     "status": "open", "pcf_north_indicator_on": True,
                                     "pcf_north_axis": "Y", "metadata": {"area": "N"}}]
