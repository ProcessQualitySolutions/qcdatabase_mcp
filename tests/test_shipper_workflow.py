"""The rest of the shipper workflow (qcdatabase.ai #124, MCP #39) and the
line-spec actions (#36).

The HTTP client is faked with httpx.MockTransport, so these check the exact
routes, methods, query strings and bodies the tools send. No live API calls.
"""

from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from qcdatabase_mcp import server
from qcdatabase_mcp.client import QCClient

S = "/api/shippers/projects/p1/"


class _Store:
    def get_active_project(self):
        return {"id": "p1", "name": "P"}

    def clear_token(self):
        pass


class _Calls(list):
    def __init__(self):
        super().__init__()
        self.responses: dict[tuple[str, str], object] = {}

    def routes(self):
        return [(r.method, r.url.path) for r in self]

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


def _tools():
    return {t.name: t for t in server.mcp._tool_manager.list_tools()}


# --- unlock / status ------------------------------------------------------------
def test_unlock_posts_to_the_unlock_endpoint(calls):
    out = server.unlock_shipper("s1")
    assert calls.routes() == [("POST", f"{S}s1/unlock/")]
    assert "unlocked" in out


def test_set_status_draft_points_at_unlock(calls):
    with pytest.raises(ToolError, match="unlock_shipper"):
        server.set_shipper_status("s1", "draft")
    assert calls == []


# --- checklists -------------------------------------------------------------------
def test_confirm_and_unconfirm_routes_and_item_guard(calls):
    server.confirm_shipper_checklist_item("s1", "Outgoing", 2, item="Bolts torqued")
    server.unconfirm_shipper_checklist_item("s1", "receiving", 0)
    assert calls.routes() == [
        ("POST", f"{S}s1/checklists/outgoing/2/confirm/"),
        ("POST", f"{S}s1/checklists/receiving/0/unconfirm/"),
    ]
    assert calls.bodies("POST") == [{"item": "Bolts torqued"}, {}]


def test_checklist_rejects_bad_kind_and_index(calls):
    with pytest.raises(ToolError):
        server.confirm_shipper_checklist_item("s1", "sideways", 0)
    with pytest.raises(ToolError):
        server.confirm_shipper_checklist_item("s1", "outgoing", -1)
    assert calls == []


def test_checklist_conflict_is_reported(calls):
    calls.responses[("POST", f"{S}s1/checklists/outgoing/0/confirm/")] = httpx.Response(
        409, json={"code": "checklist_changed", "detail": "This checklist was changed since it was loaded."})
    with pytest.raises(ToolError) as exc:
        server.confirm_shipper_checklist_item("s1", "outgoing", 0, item="Tagged")
    assert "checklist" in str(exc.value).lower()


# --- receiving, notes, reorder ---------------------------------------------------------
def test_receive_and_unreceive(calls):
    server.receive_shipper_line_item("s1", "l1")
    server.unreceive_shipper_line_item("s1", "l1")
    assert calls.routes() == [("POST", f"{S}s1/line-items/l1/receive/"),
                              ("POST", f"{S}s1/line-items/l1/unreceive/")]


def test_notes(calls):
    server.add_shipper_line_item_note("s1", "l1", " some_damaged ", text="2 flanges dented")
    server.remove_shipper_line_item_note("s1", "l1", "1730800000123")
    server.list_shipper_note_categories()
    assert calls.routes() == [
        ("POST", f"{S}s1/line-items/l1/notes/"),
        ("DELETE", f"{S}s1/line-items/l1/notes/1730800000123/"),
        ("GET", "/api/shippers/note-categories/"),
    ]
    assert calls.bodies("POST") == [{"category": "some_damaged", "text": "2 flanges dented"}]
    with pytest.raises(ToolError):
        server.add_shipper_line_item_note("s1", "l1", "")


def test_reorder_sends_every_id_in_order(calls):
    calls.responses[("POST", f"{S}s1/line-items/reorder/")] = [{"id": "b"}, {"id": "a"}]
    server.reorder_shipper_line_items("s1", "b, a, b")
    server.reorder_shipper_line_items("s1", '["a", "b"]')
    assert calls.bodies("POST") == [{"item_ids": ["b", "a"]}, {"item_ids": ["a", "b"]}]
    with pytest.raises(ToolError):
        server.reorder_shipper_line_items("s1", "")


# --- audit and export ---------------------------------------------------------------------
def test_audit_summarises(calls):
    calls.responses[("POST", f"{S}s1/audit/")] = {"audit_results": [
        {"status": "ok"}, {"status": "changed", "discrepancies": ['Revision changed: "A" -> "B"']}],
        "audit_run_at": "2026-11-06T10:00:00Z"}
    out = server.audit_shipper("s1")
    assert calls.routes() == [("POST", f"{S}s1/audit/")]
    assert out.startswith("Audited 2 line(s): 1 with changes or errors.")


def test_export_pdf_passes_only_chosen_options(calls, monkeypatch, tmp_path):
    monkeypatch.setattr(server.hosted, "hosted_enabled", lambda: False)
    calls.responses[("GET", f"{S}s1/export-pdf/")] = httpx.Response(
        200, content=b"%PDF-1.4 shipper", headers={"Content-Type": "application/pdf"})
    dest = tmp_path / "out.pdf"
    out = server.export_shipper_pdf("s1", include_checklists=True, include_comments=True, save_path=str(dest))
    assert dict(calls[0].url.params) == {"include_checklists": "true", "include_comments": "true"}
    assert dest.read_bytes() == b"%PDF-1.4 shipper"
    assert str(dest) in out


# --- settings ---------------------------------------------------------------------------
def test_create_and_update_type(calls):
    server.create_shipper_type("Flatbed", default_send_location="loc1",
                               outgoing_checklist="Strapped\nTarped", statuses='["draft", "loaded"]')
    server.update_shipper_type("t1", receiving_checklist="Counted, Photographed", clear="default_notes, statuses")
    assert calls.routes() == [("POST", f"{S}types/"), ("PATCH", f"{S}types/t1/")]
    assert calls.bodies("POST") == [{"name": "Flatbed", "default_send_location": "loc1",
                                     "outgoing_checklist": ["Strapped", "Tarped"], "statuses": ["draft", "loaded"]}]
    assert calls.bodies("PATCH") == [{"receiving_checklist": ["Counted", "Photographed"],
                                      "default_notes": "", "statuses": []}]
    calls.clear()
    assert server.update_shipper_type("t1").startswith("Nothing to update")
    for bad in ({"name": ""}, {"name": "X", "statuses": '[1, 2]'}):
        with pytest.raises(ToolError):
            server.create_shipper_type(**bad)
    assert calls == []


def test_locations_and_deletes(calls):
    server.create_shipper_location("Laydown yard", contact_person="Sam")
    server.update_shipper_location("loc1", clear="address")
    server.delete_shipper_location("loc1")
    server.delete_shipper_type("t1")
    assert calls.routes() == [("POST", f"{S}locations/"), ("PATCH", f"{S}locations/loc1/"),
                              ("DELETE", f"{S}locations/loc1/"), ("DELETE", f"{S}types/t1/")]
    assert calls.bodies("POST") == [{"name": "Laydown yard", "contact_person": "Sam"}]
    assert calls.bodies("PATCH") == [{"address": ""}]


# --- lookups ---------------------------------------------------------------------------------
def test_lookups(calls):
    calls.responses[("GET", f"{S}drawings/d1/bom/")] = {"bom": [{"item": "Pipe"}, {"item": "Elbow"}]}
    calls.responses[("GET", f"{S}non-drawing-suggestions/")] = {"suggestions": ["Gaskets"]}
    calls.responses[("GET", f"{S}drawings/search/")] = []
    calls.responses[("GET", f"{S}drawings/d1/map-items/")] = []
    server.find_drawings_to_ship(" ISO ")
    bom = server.get_drawing_bom_for_shipping("d1")
    server.list_drawing_map_items_for_shipping("d1", schema_id="sc1")
    sugg = server.list_non_drawing_item_suggestions()
    assert dict(calls[0].url.params) == {"q": "ISO"}
    assert dict(calls[2].url.params) == {"schema_id": "sc1"}
    assert '0: {"item": "Pipe"}' in bom and '1: {"item": "Elbow"}' in bom
    assert "Gaskets" in sugg
    with pytest.raises(ToolError):
        server.find_drawings_to_ship("I")


# --- line specs (#36) ----------------------------------------------------------------------------
def test_line_spec_actions(calls):
    calls.responses[("POST", "/api/line-specs/ls1/attach-documents/")] = {"success": True, "attached": 2}
    calls.responses[("POST", "/api/line-specs/ls1/detach-documents/")] = {"success": True, "detached": 1}
    calls.responses[("POST", "/api/line-specs/ls1/generate-requirements/")] = {
        "success": True, "requirements": "1. Hydrotest at 1.5x design."}
    assert "Attached 2" in server.attach_line_spec_documents("ls1", "d1, d2")
    assert "Detached 1" in server.detach_line_spec_documents("ls1", '["d1"]')
    out = server.generate_line_spec_requirements("ls1")
    assert "not saved" in out and "Hydrotest" in out
    assert calls.bodies("POST") == [{"document_ids": ["d1", "d2"]}, {"document_ids": ["d1"]}, None]


# --- annotations and wording -----------------------------------------------------------------
def test_annotations():
    tools = _tools()
    sign_offs = ("unlock_shipper", "confirm_shipper_checklist_item", "unconfirm_shipper_checklist_item",
                 "receive_shipper_line_item", "unreceive_shipper_line_item", "reorder_shipper_line_items")
    for name in sign_offs:
        assert tools[name].annotations.destructiveHint, name
        assert tools[name].annotations.idempotentHint, name
    for name in ("remove_shipper_line_item_note", "update_shipper_type", "delete_shipper_type",
                 "update_shipper_location", "delete_shipper_location", "detach_line_spec_documents"):
        assert tools[name].annotations.destructiveHint, name
    for name in ("list_shipper_note_categories", "find_drawings_to_ship", "get_drawing_bom_for_shipping",
                 "list_drawing_map_items_for_shipping", "list_non_drawing_item_suggestions"):
        assert tools[name].annotations.readOnlyHint, name
    for name in ("audit_shipper", "add_shipper_line_item_note", "create_shipper_type",
                 "create_shipper_location", "attach_line_spec_documents", "generate_line_spec_requirements"):
        assert not tools[name].annotations.readOnlyHint, name
        assert not tools[name].annotations.destructiveHint, name


def test_descriptions_carry_the_rules_and_no_web_only_claims():
    tools = _tools()
    for name in ("confirm_shipper_checklist_item", "receive_shipper_line_item", "unlock_shipper"):
        assert "Shippers Edit" in tools[name].description, name
    for name in ("create_shipper_type", "delete_shipper_location"):
        assert "Project Admins only" in tools[name].description, name
    assert "checklist_changed" in tools["confirm_shipper_checklist_item"].description
    for name, tool in tools.items():
        if "shipper" in name:
            text = tool.description.lower()
            assert "web app only" not in text and "done in the web app" not in text, name
