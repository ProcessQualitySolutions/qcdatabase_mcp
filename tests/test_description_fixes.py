"""Regression tests for issue #17: tool parameters and descriptions match the API.

* create_training takes the API's own field name ('type') and passes
  policy_visible_to_learners through.
* create_reference_request accepts 'itp_line_item' like every other tool and
  sends the endpoint's 'itp_item'.
* create_reference documents and validates the reference status enum.
* list_lock_types no longer claims lock types can only be managed in the web app.
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
        seen.append(request)
        return httpx.Response(201, json={"id": "x"})

    http = httpx.Client(base_url="https://qc.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "client", lambda: QCClient(store=_Store(), access_token="tok", http=http))
    monkeypatch.setattr(server, "require_project", lambda: "p1")
    return seen


def _params(name: str) -> dict:
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    return tools[name].parameters["properties"]


def test_create_training_uses_api_field_names(calls):
    props = _params("create_training")
    assert "type" in props and "training_type" not in props
    assert "policy_visible_to_learners" in props

    server.create_training("Harassment", type="presentation", policy_visible_to_learners=False)
    body = json.loads(calls[0].content)
    assert body["type"] == "presentation"
    assert body["policy_visible_to_learners"] is False


def test_create_training_omits_policy_visibility_when_unset(calls):
    server.create_training("Trench Safety")
    assert "policy_visible_to_learners" not in json.loads(calls[0].content)


@pytest.mark.parametrize("given, sent", [("itp_line_item", "itp_item"), ("itp_item", "itp_item"),
                                         ("map_item", "map_item")])
def test_reference_request_item_type_naming(calls, given, sent):
    server.create_reference_request(given, "i1", "document", document_folder="f1")
    assert json.loads(calls[0].content)["item_type"] == sent


def test_reference_status_is_documented_and_validated(calls):
    doc = server.create_reference.__doc__
    for st in ("pass", "fail", "compliant", "non-compliant", "pending-review"):
        assert st in doc

    with pytest.raises(ToolError, match="pending-review"):
        server.create_reference("map_item", "m1", "document", "d1", status="passed")
    assert calls == []

    server.create_reference("map_item", "m1", "document", "d1", status="Pending-Review")
    assert json.loads(calls[0].content)["status"] == "pending-review"


def test_lock_type_description_matches_api():
    doc = server.list_lock_types.__doc__
    assert "API does let project admins create" in doc
