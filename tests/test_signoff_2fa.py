"""2FA sign-offs (qcdatabase.ai docs/signoff_2fa_plan.md section 10).

A project can require a fresh authenticator code for chosen sign-offs. The API
refuses with 403 (429 when rate limited) and ``code: signoff_2fa_required``
unless the request carries the code. These tests check, with the HTTP client
faked by httpx.MockTransport (no live API calls):

* each refusal reason becomes a clear instruction for the assistant, ending in
  the policy line, on both 403 and 429;
* the code travels only in the X-TOTP-Code header and never shows up in error
  text or logs;
* every gated tool passes totp_code through (and the skipped ones do not take it);
* the bulk tools hit the batch routes with the right bodies and summarise
  per-item outcomes.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session

from qcdatabase_mcp import BASE_URL, server
from qcdatabase_mcp.client import SIGNOFF_2FA_POLICY, TOTP_HEADER, QCClient

CODE = "654321"
P = "p1"


class _Store:
    def get_active_project(self):
        return {"id": P, "name": "P"}

    def clear_token(self, **_kw):
        pass


class _Calls(list):
    def __init__(self):
        super().__init__()
        self.reply: object = None

    def routes(self):
        return [(r.method, r.url.path) for r in self]

    def body(self, i=-1):
        r = self[i]
        return json.loads(r.content) if r.content else None


@pytest.fixture
def calls(monkeypatch):
    seen = _Calls()

    def handler(request: httpx.Request):
        request.read()
        seen.append(request)
        reply = seen.reply
        if callable(reply):
            reply = reply(request)
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply if reply is not None else {"id": "x"})

    http = httpx.Client(base_url="https://qc.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "client", lambda: QCClient(store=_Store(), access_token="tok", http=http))
    monkeypatch.setattr(server, "require_project", lambda: P)
    return seen


def _tools():
    return {t.name: t for t in server.mcp._tool_manager.list_tools()}


def _refusal(reason, status=403, **extra):
    body = {
        "code": "signoff_2fa_required",
        "reason": reason,
        "detail": "Accepting Pipe Weld requires a new authenticator code.",
        "error": "Accepting Pipe Weld requires a new authenticator code.",
        "keys": ["map_item.s1.mark_accepted"],
        "actions": ["Accept Pipe Weld", "Complete Pipe Weld"],
        "code_field": "totp_code",
        "code_header": "X-TOTP-Code",
        "enroll_url": None,
        "usable_at": None,
    }
    body.update(extra)
    return httpx.Response(status, json=body)


# --- refusal reasons -------------------------------------------------------------
@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.parametrize("reason, expect", [
    ("missing", ["requires a new code from the user's authenticator app",
                 "current six-digit code", "totp_code", "Accept Pipe Weld", "Complete Pipe Weld"]),
    ("invalid", ["not accepted", "NEXT code", "every 30 seconds", "used once"]),
    ("used", ["already used", "NEXT code", "every 30 seconds", "used once"]),
    ("not_enrolled", ["no authenticator app set up", f"{BASE_URL}/two-factor/setup/"]),
    ("authenticator_too_new", ["2026-11-15T09:30:00", "project administrator can waive"]),
    ("rate_limited", ["Wait a few minutes"]),
])
def test_each_refusal_reason_maps_to_a_clear_instruction(calls, reason, expect, status):
    calls.reply = _refusal(
        reason, status,
        enroll_url="/two-factor/setup/" if reason == "not_enrolled" else None,
        usable_at="2026-11-15T09:30:00+00:00" if reason == "authenticator_too_new" else None,
    )
    with pytest.raises(ToolError) as exc:
        server.mark_map_item_accepted("m1")
    text = str(exc.value)
    assert f"({status})" in text
    assert "signoff_2fa_required" in text and f"reason: {reason}" in text
    for phrase in expect:
        assert phrase in text, (reason, phrase, text)
    assert text.rstrip().endswith(SIGNOFF_2FA_POLICY)
    assert "never ask for or store the authenticator secret" in text
    # Not mistaken for a membership / scope problem.
    assert "member of this project" not in text and "OAuth scope" not in text


def test_rate_limited_429_is_not_a_generic_error(calls):
    calls.reply = _refusal("rate_limited", 429)
    with pytest.raises(ToolError) as exc:
        server.mark_itp_complete("i1")
    assert "QC Database returned an error (429)" not in str(exc.value)


def test_other_429_still_generic(calls):
    calls.reply = httpx.Response(429, json={"detail": "Slow down."})
    with pytest.raises(ToolError, match=r"returned an error \(429\)"):
        server.mark_itp_complete("i1")


def test_refusal_exposes_status_code(calls):
    calls.reply = _refusal("missing")
    c = server.client()
    with pytest.raises(Exception) as exc:
        c.post("/api/mapping/items/m1/mark-complete/")
    assert exc.value.status_code == 403


# --- the code: header only, never echoed or logged --------------------------------
def test_code_is_sent_as_the_header_only(calls):
    server.mark_map_item_complete("m1", totp_code=" 654 321 ")
    req = calls[-1]
    assert req.headers[TOTP_HEADER] == CODE
    assert CODE not in str(req.url)
    assert not req.content or CODE.encode() not in req.content


def test_no_header_without_a_code(calls):
    server.mark_map_item_complete("m1")
    server.mark_map_item_complete("m1", totp_code="")
    assert all(TOTP_HEADER not in r.headers for r in calls)


@pytest.mark.parametrize("bad", ["12345", "abcdef", "123456789", "12 34 5x", "1\r\nX: y"])
def test_malformed_code_is_refused_locally_without_echo(calls, bad):
    with pytest.raises(ToolError) as exc:
        server.mark_map_item_complete("m1", totp_code=bad)
    assert calls == []
    text = str(exc.value)
    assert bad.strip() not in text
    assert "six-digit" in text and text.endswith(SIGNOFF_2FA_POLICY)


def test_code_never_appears_in_error_text(calls):
    # Even an API error body that (wrongly) echoed the code must not carry it back.
    calls.reply = httpx.Response(400, json={"detail": f"bad code {CODE}", "errors": [f"code {CODE}"]})
    with pytest.raises(ToolError) as exc:
        server.unlock_lock("l1", totp_code=CODE)
    assert CODE not in str(exc.value)
    assert "[redacted]" in str(exc.value)
    assert getattr(exc.value.__cause__, "status_code", None) == 400

    calls.reply = _refusal("invalid", detail=f"code {CODE} was wrong")
    with pytest.raises(ToolError) as exc:
        server.unlock_lock("l1", totp_code=CODE)
    assert CODE not in str(exc.value)


def test_code_not_in_network_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectError(f"boom {request.headers.get(TOTP_HEADER)}", request=request)

    http = httpx.Client(base_url="https://qc.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "client", lambda: QCClient(store=_Store(), access_token="tok", http=http))
    monkeypatch.setattr(server, "require_project", lambda: P)
    with pytest.raises(ToolError) as exc:
        server.delete_lock("l1", totp_code=CODE)
    assert CODE not in str(exc.value)
    assert exc.value.__cause__.__cause__ is None  # the httpx error (holding the request) is not chained


def test_code_never_logged_end_to_end(calls, caplog):
    """Through the real MCP protocol at DEBUG, nothing logged (SDK, httpx or
    ours) may carry the code. The SDK's SSE transport logs raw bodies and its
    message log could start including arguments; the redaction filter covers
    both (unit-tested below)."""
    async def run():
        async with create_connected_server_and_client_session(server.mcp) as session:
            return await session.call_tool("mark_map_item_complete", {"item_id": "m1", "totp_code": CODE})

    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(run())
    assert result.isError is False
    assert calls[-1].headers[TOTP_HEADER] == CODE
    assert caplog.records, "expected the SDK to log at DEBUG"
    assert CODE not in caplog.text
    assert all(CODE not in r.getMessage() for r in caplog.records)


def test_log_filter_redacts_argument_and_header_forms():
    f = server._RedactTotpCode()
    for msg in (f"arguments={{'item_id': 'm1', 'totp_code': '{CODE}'}}",
                f'{{"totp_code": "{CODE}"}}',
                f"X-TOTP-Code: {CODE}"):
        rec = logging.LogRecord("mcp.server.lowlevel.server", logging.DEBUG, __file__, 1, "%s", (msg,), None)
        f.filter(rec)
        assert CODE not in rec.getMessage()
        assert "[redacted]" in rec.getMessage()


# --- every gated tool passes totp_code through ------------------------------------
GATED = [
    ("mark_map_item_complete", ("m1",), {}, "POST", "/api/mapping/items/m1/mark-complete/"),
    ("mark_map_item_accepted", ("m1",), {}, "POST", "/api/mapping/items/m1/mark-accepted/"),
    ("bulk_update_map_items", ("d1", "s1", '[{"id": "m1", "data": {"r": "ACC"}}]'), {},
     "POST", "/api/mapping/items/bulk-update/"),
    ("mark_itp_complete", ("i1",), {}, "POST", f"/api/packages/projects/{P}/itp-line-items/i1/mark-complete/"),
    ("mark_itp_accepted", ("i1",), {}, "POST", f"/api/packages/projects/{P}/itp-line-items/i1/mark-accepted/"),
    ("unlock_lock", ("l1",), {}, "POST", f"/api/locks/projects/{P}/locks/l1/unlock/"),
    ("delete_lock", ("l1",), {}, "DELETE", f"/api/locks/projects/{P}/locks/l1/"),
    ("complete_form_submission", ("f1",), {}, "POST", f"/api/forms/projects/{P}/submissions/f1/complete/"),
    ("confirm_shipper_checklist_item", ("s1", "outgoing", 2), {}, "POST",
     f"/api/shippers/projects/{P}/s1/checklists/outgoing/2/confirm/"),
    ("unconfirm_shipper_checklist_item", ("s1", "receiving", 0), {}, "POST",
     f"/api/shippers/projects/{P}/s1/checklists/receiving/0/unconfirm/"),
    ("receive_shipper_line_item", ("s1", "l1"), {}, "POST", f"/api/shippers/projects/{P}/s1/line-items/l1/receive/"),
    ("unreceive_shipper_line_item", ("s1", "l1"), {}, "POST", f"/api/shippers/projects/{P}/s1/line-items/l1/unreceive/"),
    ("set_shipper_status", ("s1", "shipped"), {}, "PATCH", f"/api/shippers/projects/{P}/s1/"),
    ("unlock_shipper", ("s1",), {}, "POST", f"/api/shippers/projects/{P}/s1/unlock/"),
    ("delete_shipper", ("s1",), {}, "DELETE", f"/api/shippers/projects/{P}/s1/"),
    ("bulk_mark_map_items_complete", ("m1,m2",), {}, "POST", "/api/mapping/items/bulk-mark-complete/"),
    ("bulk_mark_map_items_accepted", ("m1,m2",), {}, "POST", "/api/mapping/items/bulk-mark-accepted/"),
    ("bulk_mark_itp_complete", ("i1,i2",), {}, "POST", f"/api/packages/projects/{P}/itp-line-items/bulk-mark-complete/"),
    ("bulk_mark_itp_accepted", ("i1,i2",), {}, "POST", f"/api/packages/projects/{P}/itp-line-items/bulk-mark-accepted/"),
    ("bulk_confirm_shipper_checklist_items", ("s1", "outgoing", "0,1"), {}, "POST",
     f"/api/shippers/projects/{P}/s1/checklists/outgoing/confirm/"),
    ("bulk_unconfirm_shipper_checklist_items", ("s1", "outgoing", "0,1"), {}, "POST",
     f"/api/shippers/projects/{P}/s1/checklists/outgoing/unconfirm/"),
    ("bulk_receive_shipper_line_items", ("s1", "l1,l2"), {}, "POST", f"/api/shippers/projects/{P}/s1/line-items/receive/"),
    ("bulk_unreceive_shipper_line_items", ("s1", "l1,l2"), {}, "POST",
     f"/api/shippers/projects/{P}/s1/line-items/unreceive/"),
    ("bulk_unlock_locks", ("k1,k2",), {}, "POST", f"/api/locks/projects/{P}/locks/unlock/"),
    ("bulk_delete_locks", ("k1,k2",), {}, "POST", f"/api/locks/projects/{P}/locks/delete/"),
    ("set_document_extracted_data", ("d1", '{"a": 1}'), {}, "POST", "/api/documents/d1/extracted-data/"),
    ("delete_document", ("d1",), {}, "DELETE", "/api/documents/d1/"),
    ("bulk_delete_documents", ("d1,d2",), {}, "POST", "/api/documents/bulk-delete/"),
]


@pytest.mark.parametrize("name, args, kwargs, method, path", GATED, ids=[g[0] for g in GATED])
def test_gated_tool_passes_code_as_header(calls, name, args, kwargs, method, path):
    getattr(server, name)(*args, **kwargs, totp_code=CODE)
    assert calls.routes() == [(method, path)]
    assert calls[-1].headers[TOTP_HEADER] == CODE
    body = calls[-1].content or b""
    assert CODE.encode() not in body and "totp_code" not in body.decode()


def test_update_map_item_passes_code(calls):
    calls.reply = lambda req: {"id": "m1", "project": P} if req.method == "GET" else {"id": "m1"}
    server.update_map_item("m1", status="pending", totp_code=CODE)
    assert calls.routes() == [("GET", "/api/mapping/items/m1/"), ("PATCH", "/api/mapping/items/m1/")]
    assert TOTP_HEADER not in calls[0].headers  # only the write carries it
    assert calls[1].headers[TOTP_HEADER] == CODE
    assert calls.body() == {"status": "pending"}


SKIPPED = ("create_shipper", "update_shipper", "remove_shipper_line_item", "create_form_submission")


def test_gated_tools_document_and_expose_totp_code():
    tools = _tools()
    names = {g[0] for g in GATED} | {
        "update_map_item", "update_drawing", "upload_large_format_drawing_version", "upload_zipmap",
        "upload_document", "upload_document_version", "upload_drawing_version", "submit_fillable_template",
    }
    for name in names:
        tool = tools[name]
        assert "totp_code" in tool.parameters["properties"], name
        assert "totp_code" not in tool.parameters.get("required", []), name
        desc = " ".join(tool.description.split())
        assert server._SIGNOFF_2FA_SENTENCE in desc, name
    for name in SKIPPED:
        assert "totp_code" not in tools[name].parameters["properties"], name


# --- batch routing and per-item summaries -----------------------------------------
def test_bulk_map_complete_body_and_summary(calls):
    calls.reply = {
        "success": True, "updated": 1,
        "items": [{"id": "aaa", "status": "completed"}],
        "blocked_count": 1, "permission_blocked": 1,
        "message": "1 item has an active lock.",
    }
    out = server.bulk_mark_map_items_complete('["AAA", "bbb", "ccc", "bbb"]', totp_code=CODE)
    assert calls.body() == {"items": ["AAA", "bbb", "ccc"], "undo": False}
    assert "1 of 3" in out and "- AAA: changed (status completed)" in out
    assert "Not changed (" in out and "bbb" in out and "ccc" in out
    assert "blocked by a lock" in out and "no sign-off permission" in out
    assert "1 item has an active lock." in out


def test_bulk_map_accept_undo(calls):
    calls.reply = {"success": True, "updated": 2, "items": [{"id": "m1", "status": "completed"},
                                                          {"id": "m2", "status": "completed"}]}
    out = server.bulk_mark_map_items_accepted("m1, m2", undo=True)
    assert calls.routes() == [("POST", "/api/mapping/items/bulk-mark-accepted/")]
    assert calls.body() == {"items": ["m1", "m2"], "undo": True}
    assert out.startswith("Undo accept: 2 of 2")
    assert TOTP_HEADER not in calls[-1].headers


def test_bulk_itp_summary_lists_changed_skipped_unchanged(calls):
    calls.reply = {
        "updated": 1,
        "items": [{"id": "i1"}],
        "skipped": [{"id": "i2", "label": "Hydro", "error": "This item has 1 active lock.", "status": "pending"}],
        "unchanged": ["i3"],
    }
    out = server.bulk_mark_itp_complete("i1,i2,i3,i4", totp_code=CODE)
    assert calls.body() == {"items": ["i1", "i2", "i3", "i4"], "undo": False}
    assert "- i1: changed" in out
    assert "- i2 (Hydro): skipped - This item has 1 active lock." in out
    assert "- i3: unchanged" in out
    assert "- i4: not reported" in out


@pytest.mark.parametrize("detail", [["No line item could be changed."], "No line item could be changed."],
                         ids=["old-list-detail", "new-string-detail"])
def test_bulk_itp_all_skipped_error_lists_reasons(calls, detail):
    calls.reply = httpx.Response(400, json={
        "detail": detail,
        "skipped": [{"id": "i2", "label": "Hydro", "error": "Required photo missing."}],
    })
    with pytest.raises(ToolError) as exc:
        server.bulk_mark_itp_accepted("i2")
    text = str(exc.value)
    assert "The request was rejected (400). No line item could be changed." in text
    assert "['" not in text and "[\"" not in text
    assert "Hydro: Required photo missing." in text


def test_bulk_itp_accepts_500(calls):
    calls.reply = {"updated": 0, "items": [], "skipped": [], "unchanged": []}
    server.bulk_mark_itp_complete(",".join(f"id{i}" for i in range(500)))
    assert len(calls.body()["items"]) == 500


def test_bulk_unlock_locks_body_and_summary(calls):
    calls.reply = {
        "locks": [{"id": "k1", "status": "unlocked", "lock_type": {"id": "t", "name": "Fit-up"}}],
        "skipped": [{"id": "k2", "reason": "already_unlocked"},
                    {"id": "k3", "reason": "permission_denied"},
                    {"id": "k4", "reason": "training_required"},
                    {"id": "k5", "reason": "not_found"}],
    }
    out = server.bulk_unlock_locks('["k1", "k2", "k3", "k4", "k5", "k6"]', totp_code=CODE)
    assert calls.body() == {"locks": ["k1", "k2", "k3", "k4", "k5", "k6"]}
    assert calls[-1].headers[TOTP_HEADER] == CODE
    assert out.startswith("1 of 6 lock(s) unlocked.")
    assert "- k1 (Fit-up): unlocked" in out
    assert "- k2: skipped - already unlocked" in out
    assert "- k3: skipped - you may not do this" in out
    assert "- k4: skipped - a required training" in out
    assert "- k5: skipped - not found" in out
    assert "- k6: not reported by the API" in out


def test_bulk_delete_locks_summary(calls):
    calls.reply = {"locks": [{"id": "k1"}], "skipped": []}
    out = server.bulk_delete_locks("k1")
    assert calls.routes() == [("POST", f"/api/locks/projects/{P}/locks/delete/")]
    assert "- k1: withdrawn (soft-deleted)" in out
    assert TOTP_HEADER not in calls[-1].headers


def test_bulk_checklist_body_and_summary(calls):
    calls.reply = {"outgoing_checklist_state": {
        "0": {"checked": True, "user": "Ann", "item": "Tagged", "confirmed_at": "2026-11-14T10:00:00Z"},
    }}
    out = server.bulk_confirm_shipper_checklist_items(
        "s1", "Outgoing", "[0, 3, 0]", expected_items='["Tagged", "Bolts"]', totp_code=CODE)
    assert calls.body() == {"indexes": [0, 3], "expected_items": ["Tagged", "Bolts"]}
    assert "- [0] Tagged: confirmed by Ann at 2026-11-14T10:00:00Z" in out
    assert "- [3]: not confirmed" in out


@pytest.mark.parametrize("indexes, expected", [("0,-1", ""), ("a,b", ""), ("0,1", '["only one"]')])
def test_bulk_checklist_rejects_bad_input_locally(calls, indexes, expected):
    with pytest.raises(ToolError):
        server.bulk_confirm_shipper_checklist_items("s1", "outgoing", indexes, expected_items=expected)
    assert calls == []


def test_bulk_receive_summary(calls):
    calls.reply = [
        {"id": "l1", "received_at": "2026-11-14T10:00:00Z", "received_by_name": "Ann"},
        {"id": "l2", "received_at": None},
    ]
    out = server.bulk_receive_shipper_line_items("s1", "l1,l2", totp_code=CODE)
    assert calls.body() == {"items": ["l1", "l2"]}
    assert "- l1: received by Ann at 2026-11-14T10:00:00Z" in out
    assert "- l2: not received" in out


@pytest.mark.parametrize("name, args, limit", [
    ("bulk_mark_map_items_complete", (), 500),
    ("bulk_mark_itp_complete", (), 500),
    ("bulk_unlock_locks", (), 500),
    ("bulk_receive_shipper_line_items", ("s1",), 500),
])
def test_bulk_limits_refused_before_sending(calls, name, args, limit):
    ids = ",".join(f"id{i}" for i in range(limit + 1))
    with pytest.raises(ToolError, match=f"At most {limit}"):
        getattr(server, name)(*args, ids)
    assert calls == []


def test_bulk_tools_are_destructive_signoffs():
    tools = _tools()
    for name in ("bulk_mark_map_items_complete", "bulk_mark_map_items_accepted", "bulk_mark_itp_complete",
                 "bulk_mark_itp_accepted", "bulk_confirm_shipper_checklist_items",
                 "bulk_unconfirm_shipper_checklist_items", "bulk_receive_shipper_line_items",
                 "bulk_unreceive_shipper_line_items", "bulk_unlock_locks", "bulk_delete_locks"):
        ann = tools[name].annotations
        assert ann.destructiveHint is True and ann.readOnlyHint is False, name
        assert "One code covers the whole batch" in " ".join(tools[name].description.split()), name


# --- per-item detail from newer API responses (older shapes still tolerated) ------
def test_bulk_map_shows_skip_reasons_and_unchanged(calls):
    calls.reply = {
        "success": True, "updated": 1,
        "items": [{"id": "m1", "status": "accepted"}],
        "skipped": [{"id": "m2", "reason": "locked"},
                    {"id": "m3", "reason": "open_reference_requests"},
                    {"id": "m4", "reason": "not_completed"},
                    {"id": "m5", "reason": "permission_denied"},
                    {"id": "m6", "reason": "training_required"},
                    {"id": "m7", "reason": "not_found"}],
        "unchanged": ["m8"],
    }
    out = server.bulk_mark_map_items_accepted("m1,m2,m3,m4,m5,m6,m7,m8,m9", totp_code=CODE)
    assert "- m1: changed (status accepted)" in out
    assert "- m2: skipped - an active lock" in out
    assert "- m3: skipped - open reference requests" in out
    assert "- m4: skipped - not complete" in out
    assert "- m5: skipped - no sign-off permission" in out
    assert "- m6: skipped - a required training" in out
    assert "- m7: skipped - not found" in out
    assert "- m8: unchanged" in out
    assert "- m9: not reported by the API" in out
    assert "Not changed (" not in out


def test_bulk_undo_is_sent_as_json_boolean(calls):
    calls.reply = {"updated": 0, "items": [], "skipped": [], "unchanged": []}
    server.bulk_mark_itp_complete("i1", undo=True)
    server.bulk_mark_map_items_complete("m1", undo=1)
    assert calls.body(0)["undo"] is True and calls.body(1)["undo"] is True


@pytest.mark.parametrize("changed, expect", [
    ([0], ["- [0] Tagged: confirmed by Ann at T (changed by this request)",
           "- [3]: not confirmed (unchanged: already in that state)", "1 of 2 item(s) changed."]),
    (None, ["- [0] Tagged: confirmed by Ann at T", "keep their original confirmation"]),
])
def test_bulk_checklist_uses_changed_indexes(calls, changed, expect):
    reply = {"outgoing_checklist_state": {"0": {"checked": True, "user": "Ann", "item": "Tagged",
                                                "confirmed_at": "T"}}}
    if changed is not None:
        reply["changed_indexes"] = changed
    calls.reply = reply
    out = server.bulk_confirm_shipper_checklist_items("s1", "outgoing", "0,3")
    for phrase in expect:
        assert phrase in out
    if changed is None:
        assert "changed by this request" not in out


def test_single_checklist_says_when_nothing_changed(calls):
    calls.reply = {"changed_indexes": []}
    out = server.confirm_shipper_checklist_item("s1", "outgoing", 2)
    assert "already confirmed, so nothing changed" in out
    calls.reply = {"changed_indexes": [2]}
    assert "nothing changed" not in server.confirm_shipper_checklist_item("s1", "outgoing", 2)
    calls.reply = {}
    assert "nothing changed" not in server.unconfirm_shipper_checklist_item("s1", "outgoing", 2)
    calls.reply = {"changed_indexes": []}
    assert "already unconfirmed" in server.unconfirm_shipper_checklist_item("s1", "outgoing", 2)


def test_bulk_receive_uses_changed_items_header(calls):
    rows = [{"id": "l1", "received_at": "T1", "received_by_name": "Ann"},
            {"id": "l2", "received_at": None}]
    calls.reply = httpx.Response(200, json=rows, headers={"X-Changed-Items": "L1"})
    out = server.bulk_unreceive_shipper_line_items("s1", "l1,l2")
    assert "- l1: received by Ann at T1 (changed by this request)" in out
    assert "- l2: not received (unchanged: already in that state)" in out
    assert "1 of 2 line(s) changed." in out

    calls.reply = httpx.Response(200, json=rows, headers={"X-Changed-Items": ""})
    out = server.bulk_receive_shipper_line_items("s1", "l1,l2")
    assert "0 of 2 line(s) changed." in out

    calls.reply = rows  # older server: no header
    out = server.bulk_receive_shipper_line_items("s1", "l1,l2")
    assert "changed by this request" not in out and "line(s) changed" not in out


def test_update_drawing_passes_code(calls):
    calls.reply = lambda req: {"id": "d1", "project": P}
    server.update_drawing("d1", title="New title", totp_code=CODE)
    assert calls.routes() == [("GET", "/api/drawings/d1/"), ("PATCH", "/api/drawings/d1/")]
    assert TOTP_HEADER not in calls[0].headers
    assert calls[1].headers[TOTP_HEADER] == CODE
    assert calls.body() == {"title": "New title"}
