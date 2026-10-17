"""Trainings tools: thin wrappers over /api/trainings/ (one endpoint per tool),
the confirm_charge preview, idempotent policy uploads, error-code passthrough
and the re-consent message for connections without the trainings scope."""
import hashlib
import json

import httpx
import pytest

from qcdatabase_mcp import server
from qcdatabase_mcp.client import QCClient


class _Store:
    def get_active_project(self):
        return None

    def clear_token(self):
        pass


def _client(handler):
    http = httpx.Client(base_url="https://qc.test", transport=httpx.MockTransport(handler))
    return QCClient(store=_Store(), access_token="tok", http=http)


class _Calls(list):
    """The requests a test's tools sent, plus the canned responses by (method, path)."""

    def __init__(self):
        super().__init__()
        self.responses: dict[tuple[str, str], tuple[int, object]] = {}


@pytest.fixture
def calls(monkeypatch):
    seen = _Calls()

    def handler(request: httpx.Request):
        request.read()
        seen.append(request)
        status, body = seen.responses.get((request.method, request.url.path), (200, {}))
        return httpx.Response(status, json=body)

    monkeypatch.setattr(server, "client", lambda: _client(handler))
    return seen


def test_create_training_posts_scope_and_brief(calls):
    calls.responses[("POST", "/api/trainings/")] = (201, {"id": "t1", "status": "idea"})
    out = server.create_training("Trench Safety", scope="project", project_id="p1", idea_brief="Teach shoring")
    assert len(calls) == 1
    body = json.loads(calls[0].content)
    assert body == {"title": "Trench Safety", "scope": "project", "project_id": "p1", "idea_brief": "Teach shoring", "type": "game"}
    assert calls[0].headers["Authorization"] == "Bearer tok"
    assert '"status": "idea"' in out or "idea" in out


def test_project_scope_needs_project_id(calls):
    assert server.list_trainings(scope="project").startswith("Error:")
    assert calls == []


def test_upload_version_without_confirmation_only_previews(calls):
    path = "/api/trainings/t1/versions/upload-url/"
    calls.responses[("POST", path)] = (200, {"confirm_charge_required": True, "charge_usd": "25.00", "currency": "USD", "message": "Test locally first."})
    out = server.upload_training_version("t1")
    assert json.loads(calls[0].content) == {"confirm_charge": False}
    assert "Not uploaded yet" in out and "$25.00" in out


def test_upload_version_with_confirmation_returns_the_command(calls):
    path = "/api/trainings/t1/versions/upload-url/"
    calls.responses[("POST", path)] = (201, {"upload_id": "u1", "url": "https://ingest.redline.school/v1/uploads/u1?token=x",
                                             "expires_at": "soon", "command": 'npx @redline/dev upload <folder> --url "https://ingest…"'})
    out = server.upload_training_version("t1", confirm_charge=True, change_notes="v2")
    assert json.loads(calls[0].content) == {"confirm_charge": True, "change_notes": "v2"}
    assert "npx @redline/dev upload <your bundle folder>" in out and "u1" in out


def test_error_codes_pass_through(calls):
    calls.responses[("POST", "/api/trainings/t1/versions/upload-url/")] = (402, {"detail": "AI credits are too low.", "code": "insufficient_ai_credits"})
    out = server.upload_training_version("t1", confirm_charge=True)
    assert "insufficient_ai_credits" in out


def test_missing_scope_explains_reconsent(calls):
    calls.responses[("GET", "/api/trainings/t1/")] = (403, {"detail": "The access token does not grant the scope required for this request."})
    out = server.get_training("t1")
    assert "lacks the trainings:read / trainings:write access" in out
    assert "login" in out


def test_policy_upload_sends_an_idempotency_key(calls, tmp_path):
    pdf = tmp_path / "policy.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%%EOF")
    calls.responses[("POST", "/api/trainings/t1/policy/")] = (201, {"event": "policy_attached", "revision": 1})
    server.upload_training_policy("t1", str(pdf))
    server.upload_training_policy("t1", str(pdf))
    keys = [c.headers.get("Idempotency-Key") for c in calls]
    assert all(keys) and keys[0] != keys[1]  # one key per logical upload
    assert b'name="file"' in calls[0].content


def test_policy_remove(calls):
    calls.responses[("DELETE", "/api/trainings/t1/policy/")] = (200, {"removed_revision": 2})
    out = server.upload_training_policy("t1", remove=True)
    assert calls[0].method == "DELETE" and "removed_revision" in out
    assert server.upload_training_policy("t1", "x.pdf", remove=True).startswith("Error:")


def test_validate_computes_the_manifest_locally_without_contents(calls, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path / "redline.json").write_text('{"spec": "0.2"}')
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("ignored")
    calls.responses[("POST", "/api/trainings/t1/validate/")] = (200, {"ok": False, "errors": [{"code": "missing_file", "path": "DESIGN.md", "message": "required"}], "warnings": [], "stats": {"files": 2}})
    out = server.validate_training_bundle("t1", folder_path=str(tmp_path))
    body = json.loads(calls[0].content)
    assert [f["path"] for f in body["files"]] == ["index.html", "redline.json"]
    assert body["files"][0]["sha256"] == hashlib.sha256(b"<!doctype html>").hexdigest()
    assert "<!doctype" not in calls[0].content.decode()  # never file contents
    assert body["redline_json"] == {"spec": "0.2"}
    assert "FAILED" in out and "missing_file" in out


def test_review_polls_upload_then_version(calls):
    calls.responses[("GET", "/api/trainings/t1/uploads/u1/")] = (200, {"status": "reviewed", "version_id": "v1", "review_status": "passed"})
    calls.responses[("GET", "/api/trainings/t1/versions/v1/review/")] = (200, {"review_status": "passed", "review_report": {"files": []}})
    out = server.get_training_review("t1", upload_id="u1")
    assert [c.url.path for c in calls] == ["/api/trainings/t1/uploads/u1/", "/api/trainings/t1/versions/v1/review/"]
    assert "review_report" in out


def test_authoring_guide_renders_markdown(calls):
    calls.responses[("GET", "/api/trainings/authoring-guide/")] = (200, {"part": "mobile", "preface": "Pitfalls first.", "markdown": "## Mobile", "parts": ["quickstart", "mobile"]})
    out = server.get_training_authoring_guide("mobile")
    assert calls[0].url.params["part"] == "mobile"
    assert out.startswith("Pitfalls first.") and "## Mobile" in out and "quickstart" in out
