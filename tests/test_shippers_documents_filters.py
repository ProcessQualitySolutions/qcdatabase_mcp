"""Issue #30: shipper write tools, document delete / restore / history, and
the new list_drawings / list_documents filters.

The HTTP client is faked with httpx.MockTransport, so these check the exact
routes, methods, query strings and bodies the tools send. No live API calls.
"""

from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from qcdatabase_mcp import hosted, server
from qcdatabase_mcp.auth import DEFAULT_SCOPES
from qcdatabase_mcp.client import QCClient

SHIPPERS = "/api/shippers/projects/p1/"


class _Store:
    def get_active_project(self):
        return {"id": "p1", "name": "P"}

    def clear_token(self):
        pass


class _Calls(list):
    def __init__(self):
        super().__init__()
        self.responses: dict[tuple[str, str], object] = {}

    def routes(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self]

    def bodies(self, method: str) -> list:
        return [json.loads(r.content) for r in self if r.method == method]


@pytest.fixture
def calls(monkeypatch):
    seen = _Calls()

    def handler(request: httpx.Request):
        request.read()
        seen.append(request)
        body = seen.responses.get((request.method, request.url.path), {"id": "x"})
        if callable(body):
            body = body(request)
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)

    http = httpx.Client(base_url="https://qc.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "client", lambda: QCClient(store=_Store(), access_token="tok", http=http))
    monkeypatch.setattr(server, "require_project", lambda: "p1")
    return seen


def _tools():
    return {t.name: t for t in server.mcp._tool_manager.list_tools()}


# ---------------------------------------------------------------------------
# Shippers
# ---------------------------------------------------------------------------
def test_create_shipper_sends_type_and_only_supplied_fields(calls):
    server.create_shipper("t1", title="", notes="", shipment_date="2026-11-02")
    assert calls.routes() == [("POST", SHIPPERS)]
    # Blank title / notes are left out so the type's defaults apply.
    assert calls.bodies("POST") == [{"shipper_type": "t1", "shipment_date": "2026-11-02"}]


def test_create_shipper_with_line_items(calls):
    server.create_shipper("t1", line_items='{"drawing": "d1"}')
    assert calls.bodies("POST") == [{"shipper_type": "t1", "line_items": [{"drawing": "d1"}]}]


def test_create_shipper_rejects_bad_line_items(calls):
    for bad in ("[]", '"x"', "[1]", "not json"):
        with pytest.raises(ToolError):
            server.create_shipper("t1", line_items=bad)
    assert calls == []


def test_update_shipper_sends_only_supplied_fields(calls):
    server.update_shipper("s1", title="Load 4", notes="")
    assert calls.routes() == [("PATCH", f"{SHIPPERS}s1/")]
    assert calls.bodies("PATCH") == [{"title": "Load 4"}]


def test_update_shipper_clear_and_nothing_to_update(calls):
    server.update_shipper("s1", clear="send_location, notes")
    assert calls.bodies("PATCH") == [{"send_location": None, "notes": ""}]
    calls.clear()
    assert server.update_shipper("s1").startswith("Nothing to update")
    with pytest.raises(ToolError):
        server.update_shipper("s1", clear="title")
    assert calls == []


def test_update_shipper_has_no_status_argument():
    # Status changes go through set_shipper_status so the lock is explicit.
    props = _tools()["update_shipper"].parameters["properties"]
    assert "status" not in props


def test_set_shipper_status_patches_status_only(calls):
    server.set_shipper_status("s1", "shipped")
    assert calls.routes() == [("PATCH", f"{SHIPPERS}s1/")]
    assert calls.bodies("PATCH") == [{"status": "shipped"}]


def test_set_shipper_status_refuses_draft_and_blank(calls):
    # Back to draft is an unlock, which the API leaves to the web app.
    for bad in ("draft", "", "  "):
        with pytest.raises(ToolError):
            server.set_shipper_status("s1", bad)
    assert calls == []


def test_delete_shipper_route(calls):
    out = server.delete_shipper("s1")
    assert calls.routes() == [("DELETE", f"{SHIPPERS}s1/")]
    assert "soft-deleted" in out


def test_add_line_items_always_posts_an_array(calls):
    calls.responses[("POST", f"{SHIPPERS}s1/line-items/")] = [{"id": "l1"}]
    server.add_shipper_line_items("s1", '{"line_item_type": "non_drawing_item", "item_description": "Bolts"}')
    server.add_shipper_line_items("s1", '[{"drawing": "d1"}, {"drawing": "d2"}]')
    assert calls.routes() == [("POST", f"{SHIPPERS}s1/line-items/")] * 2
    assert calls.bodies("POST") == [
        [{"line_item_type": "non_drawing_item", "item_description": "Bolts"}],
        [{"drawing": "d1"}, {"drawing": "d2"}],
    ]


def test_update_line_item_sends_only_supplied_fields(calls):
    server.update_shipper_line_item("s1", "l1", quantity=3)
    server.update_shipper_line_item("s1", "l1", item_description="Gaskets", sort_order=0)
    server.update_shipper_line_item("s1", "l1", clear="quantity")
    assert calls.routes() == [("PATCH", f"{SHIPPERS}s1/line-items/l1/")] * 3
    assert calls.bodies("PATCH") == [
        {"quantity": 3},
        {"item_description": "Gaskets", "sort_order": 0},
        {"quantity": None},
    ]
    calls.clear()
    assert server.update_shipper_line_item("s1", "l1").startswith("Nothing to update")
    assert calls == []


def test_remove_line_item_route(calls):
    server.remove_shipper_line_item("s1", "l1")
    assert calls.routes() == [("DELETE", f"{SHIPPERS}s1/line-items/l1/")]


def test_shipper_read_tools_routes_and_filters(calls):
    server.get_shipper("s1")
    server.list_shipper_types()
    server.list_shipper_locations()
    server.list_shippers(drawing_id="d1")
    server.list_shippers()
    assert calls.routes() == [
        ("GET", f"{SHIPPERS}s1/"),
        ("GET", f"{SHIPPERS}types/"),
        ("GET", f"{SHIPPERS}locations/"),
        ("GET", SHIPPERS),
        ("GET", SHIPPERS),
    ]
    assert dict(calls[3].url.params) == {"drawing": "d1"}
    assert dict(calls[4].url.params) == {}


def test_shipper_tool_annotations():
    tools = _tools()
    for name in ("update_shipper", "set_shipper_status", "delete_shipper",
                 "update_shipper_line_item", "remove_shipper_line_item"):
        assert tools[name].annotations.destructiveHint, name
    for name in ("create_shipper", "add_shipper_line_items"):
        assert not tools[name].annotations.destructiveHint, name
        assert not tools[name].annotations.readOnlyHint, name
    for name in ("get_shipper", "list_shipper_types", "list_shipper_locations"):
        assert tools[name].annotations.readOnlyHint, name
    assert "admin" in tools["delete_shipper"].description.lower()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
def test_delete_document_route(calls):
    out = server.delete_document("d1")
    assert calls.routes() == [("DELETE", "/api/documents/d1/")]
    assert "restore_document" in out


def test_bulk_delete_and_restore_send_project_and_ids(calls):
    calls.responses[("POST", "/api/documents/bulk-delete/")] = {"success": True, "deleted": 2}
    calls.responses[("POST", "/api/documents/bulk-restore/")] = {"success": True, "restored": 1}
    assert "2 document" in server.bulk_delete_documents("d1, d2, d1")
    assert "1 document" in server.bulk_restore_documents('["d3"]')
    assert calls.routes() == [("POST", "/api/documents/bulk-delete/"),
                              ("POST", "/api/documents/bulk-restore/")]
    assert calls.bodies("POST") == [{"project": "p1", "ids": ["d1", "d2"]},
                                    {"project": "p1", "ids": ["d3"]}]


def test_bulk_document_ids_are_validated_locally(calls):
    with pytest.raises(ToolError):
        server.bulk_delete_documents(" , ")
    with pytest.raises(ToolError):
        server.bulk_restore_documents(",".join(f"d{i}" for i in range(201)))
    assert calls == []


def test_restore_trash_and_history_routes(calls):
    calls.responses[("GET", "/api/documents/trash/")] = {"count": 0, "next": None, "results": []}
    server.restore_document("d1")
    server.list_deleted_documents()
    server.get_document_history("d1", page=2, page_size=500)
    assert calls.routes() == [
        ("POST", "/api/documents/d1/restore/"),
        ("GET", "/api/documents/trash/"),
        ("GET", "/api/documents/d1/history/"),
    ]
    assert dict(calls[1].url.params) == {"project": "p1"}
    # page_size is capped at the API's maximum.
    assert dict(calls[2].url.params) == {"page": "2", "page_size": "200"}


def test_document_export_falls_back_to_export_route(calls, monkeypatch, tmp_path):
    # download_document is the document export: base64 envelope first, the raw
    # /export/ file when the envelope is over its size cap (413).
    monkeypatch.setattr(server.hosted, "hosted_enabled", lambda: False)
    monkeypatch.setattr(server, "_save_bytes", lambda path, content: f"{path} {content!r}")
    calls.responses[("GET", "/api/documents/projects/p1/document/d1/download/")] = (
        httpx.Response(413, json={"error": "too large"})
    )
    calls.responses[("GET", "/api/documents/projects/p1/document/d1/export/")] = (
        httpx.Response(200, content=b"%PDF-1.7")
    )
    out = server.download_document("d1", save_path=str(tmp_path / "x.pdf"))
    assert calls.routes() == [
        ("GET", "/api/documents/projects/p1/document/d1/download/"),
        ("GET", "/api/documents/projects/p1/document/d1/export/"),
    ]
    assert "%PDF-1.7" in out


def test_document_tool_annotations():
    tools = _tools()
    for name in ("delete_document", "bulk_delete_documents"):
        assert tools[name].annotations.destructiveHint, name
        assert "soft" in tools[name].description.lower(), name
    for name in ("list_deleted_documents", "get_document_history"):
        assert tools[name].annotations.readOnlyHint, name


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def _params(request: httpx.Request) -> list[tuple[str, str]]:
    return sorted(request.url.params.multi_items())


def test_list_drawings_new_filters_pass_through(calls):
    server.list_drawings(job="j1", package="k1", updated_since="2026-10-01T00:00:00Z")
    assert calls.routes() == [("GET", "/api/drawings/")]
    assert _params(calls[0]) == [
        ("job", "j1"), ("ordering", "updated_at"), ("package", "k1"),
        ("project", "p1"), ("updated_since", "2026-10-01T00:00:00Z"),
    ]


def test_list_drawings_blank_filters_are_omitted(calls):
    server.list_drawings(drawing_type="isometric", job="", package="", updated_since="")
    assert _params(calls[0]) == [("drawing_type", "isometric"), ("project", "p1")]


def test_list_documents_new_filters_pass_through(calls):
    server.list_documents(
        job="j1", package="k1", tags="MTR, heat-lot ,",
        metadata='{"heat_number": "4471", "qty": 3, "ok": true}',
    )
    assert _params(calls[0]) == sorted([
        ("project", "p1"), ("job", "j1"), ("package", "k1"),
        ("tag", "MTR"), ("tag", "heat-lot"),
        ("metadata__heat_number", "4471"), ("metadata__qty", "3"), ("metadata__ok", "true"),
    ])


def test_list_documents_blank_filters_are_omitted(calls):
    server.list_documents(folder_id="f1", job="", package="", tags=" , ", metadata="")
    assert _params(calls[0]) == [("folder", "f1"), ("project", "p1")]


def test_list_documents_rejects_bad_metadata(calls):
    for bad in ('["a"]', '{"k": {"nested": 1}}', "nope"):
        with pytest.raises(ToolError):
            server.list_documents(metadata=bad)
    assert calls == []


def test_repeated_tag_filter_survives_pagination(calls):
    def page(request):
        if request.url.params.get("page") == "2":
            return {"next": None, "results": [{"id": "b"}]}
        return {"next": "https://qc.test/api/documents/?page=2&project=p1&tag=MTR&tag=CMTR",
                "results": [{"id": "a"}]}

    calls.responses[("GET", "/api/documents/")] = page
    out = server.list_documents(tags="MTR,CMTR")
    assert "(2 found)" in out
    assert [v for k, v in calls[1].url.params.multi_items() if k == "tag"] == ["MTR", "CMTR"]


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------
def test_default_and_advertised_scopes_include_the_needed_writes(monkeypatch):
    monkeypatch.delenv("QCDB_MCP_SCOPES", raising=False)
    scopes = hosted.advertised_scopes()
    for needed in ("shippers:read", "shippers:write", "documents:read",
                   "documents:write", "drawings:read"):
        assert needed in DEFAULT_SCOPES.split()
        assert needed in scopes
