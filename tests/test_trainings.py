"""Trainings tools: thin wrappers over /api/trainings/ (one endpoint per tool),
free private uploads (zipped on the user's machine, sent only to
redline.school), the submit-review confirm_charge preview, templates and
reviewed-version downloads, idempotent policy uploads, error-code passthrough
and the re-consent message for connections without the trainings scope."""
import base64
import hashlib
import io
import json
import zipfile

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


ZIP_URL = "https://ingest.redline.school/v1/uploads/u1/bundle.zip?token=x"


@pytest.fixture
def redline(monkeypatch):
    """Requests sent to redline.school, plus the canned responses by (method, host+path)."""
    seen = _Calls()

    def handler(request: httpx.Request):
        request.read()
        seen.append(request)
        status, body = seen.responses.get((request.method, request.url.host + request.url.path), (200, {}))
        if isinstance(body, bytes):
            return httpx.Response(status, content=body)
        return httpx.Response(status, json=body)

    monkeypatch.setattr(server, "_redline_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(server.hosted, "hosted_enabled", lambda: False)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return seen


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _bundle(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path / "redline.json").write_text('{"spec": "0.3"}')
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref")
    (tmp_path / ".DS_Store").write_text("x")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("ignored")
    return tmp_path


def _issue_url(calls):
    calls.responses[("POST", "/api/trainings/t1/versions/upload-url/")] = (201, {
        "upload_id": "u1", "url": "https://ingest.redline.school/v1/uploads/u1?token=x",
        "zip_url": ZIP_URL, "expires_at": "soon", "charge_usd": "0.00"})


def test_upload_folder_zips_locally_and_puts_only_to_redline(calls, redline, tmp_path):
    _issue_url(calls)
    calls.responses[("GET", "/api/trainings/t1/uploads/u1/")] = (200, {"status": "stored", "version_id": "v9"})
    redline.responses[("PUT", "ingest.redline.school/v1/uploads/u1/bundle.zip")] = (
        201, {"status": "stored", "review_status": "private", "version_id": "abc123", "warnings": []})
    out = server.upload_training_version("t1", folder_path=str(_bundle(tmp_path)), change_notes="v2")

    assert json.loads(calls[0].content) == {"change_notes": "v2"}  # free: no confirm_charge
    put = redline[0]
    assert put.method == "PUT" and str(put.url) == ZIP_URL
    assert "authorization" not in put.headers  # the QC Database token never goes to redline.school
    names = zipfile.ZipFile(io.BytesIO(put.content)).namelist()
    assert sorted(names) == ["index.html", "redline.json"]  # no dot-files, no node_modules
    assert all(b"<!doctype" not in c.content for c in calls)  # contents never reach QC Database
    assert "PRIVATE version" in out and "v9" in out and "submit_training_review" in out
    assert "/trainings/t1/manage/" in out


def test_upload_rejected_by_prechecks_lists_the_errors(calls, redline, tmp_path):
    _issue_url(calls)
    redline.responses[("PUT", "ingest.redline.school/v1/uploads/u1/bundle.zip")] = (422, {
        "status": "rejected", "charged": False,
        "errors": [{"code": "eval", "path": "game.js", "line": 12, "message": "eval is not allowed"}],
        "warnings": []})
    out = server.upload_training_version("t1", folder_path=str(_bundle(tmp_path)))
    assert "REJECTED" in out and "no charge" in out and "game.js:12" in out


def test_upload_refuses_a_non_redline_url(calls, redline, tmp_path):
    calls.responses[("POST", "/api/trainings/t1/versions/upload-url/")] = (201, {
        "upload_id": "u1", "zip_url": "https://evil.example/upload"})
    out = server.upload_training_version("t1", folder_path=str(_bundle(tmp_path)))
    assert out.startswith("Error:") and "redline.school" in out
    assert redline == []


def test_upload_without_folder_returns_the_zip_command(calls, redline):
    _issue_url(calls)
    out = server.upload_training_version("t1")
    assert ZIP_URL in out and "Compress-Archive" in out and "curl.exe" in out
    assert "npx" not in out and redline == []


def test_hosted_upload_forwards_a_begin_upload_zip(calls, redline, monkeypatch):
    monkeypatch.setattr(server.hosted, "hosted_enabled", lambda: True)
    data = _zip({"index.html": "<!doctype html>"})
    monkeypatch.setattr(server, "_read_hosted_upload", lambda handle: ("bundle.zip", data))
    _issue_url(calls)
    redline.responses[("PUT", "ingest.redline.school/v1/uploads/u1/bundle.zip")] = (
        201, {"status": "stored", "version_id": "abc123"})
    out = server.upload_training_version("t1", folder_path="handle-1")
    assert redline[0].content == data and "PRIVATE version" in out


def test_hosted_upload_needs_a_zip(calls, redline, monkeypatch):
    monkeypatch.setattr(server.hosted, "hosted_enabled", lambda: True)
    monkeypatch.setattr(server, "_read_hosted_upload", lambda handle: ("x.html", b"<!doctype html>"))
    out = server.upload_training_version("t1", folder_path="handle-1")
    assert out.startswith("Error:") and calls == [] and redline == []


def test_submit_without_confirmation_only_previews(calls):
    path = "/api/trainings/t1/versions/v1/submit-review/"
    calls.responses[("POST", path)] = (200, {"confirm_charge_required": True, "charge_usd": "25.00",
                                             "currency": "USD", "message": "Play it on a phone first."})
    out = server.submit_training_review("t1", "v1")
    assert json.loads(calls[0].content) == {"confirm_charge": False}
    assert "Not submitted yet" in out and "$25.00" in out


def test_submit_with_confirmation(calls):
    path = "/api/trainings/t1/versions/v1/submit-review/"
    calls.responses[("POST", path)] = (202, {"upload_id": "u1", "charge_usd": "25.00", "version": {}})
    out = server.submit_training_review("t1", "v1", confirm_charge=True)
    assert json.loads(calls[0].content) == {"confirm_charge": True}
    assert "Submitted" in out and "u1" in out


def test_error_codes_pass_through(calls):
    calls.responses[("POST", "/api/trainings/t1/versions/v1/submit-review/")] = (
        402, {"detail": "AI credits are too low.", "code": "insufficient_ai_credits"})
    out = server.submit_training_review("t1", "v1", confirm_charge=True)
    assert "insufficient_ai_credits" in out


def test_template_lists_then_unpacks_into_a_new_folder(calls, redline, tmp_path):
    url = "https://redline.school/templates/solo-2d.zip"
    calls.responses[("GET", "/api/trainings/templates/")] = (200, {"templates": [{"name": "solo-2d", "url": url}]})
    assert "solo-2d" in server.get_training_template()
    redline.responses[("GET", "redline.school/templates/solo-2d.zip")] = (
        200, _zip({"index.html": "<!doctype html>", "js/game.js": "// GAME"}))
    out = server.get_training_template("solo-2d", folder=str(tmp_path / "new"))
    assert "Unpacked 2 files" in out
    assert (tmp_path / "new" / "js" / "game.js").read_text() == "// GAME"
    assert "authorization" not in redline[0].headers


def test_unpack_refuses_unsafe_paths_and_non_empty_folders(calls, redline, tmp_path):
    url = "https://redline.school/templates/solo-2d.zip"
    calls.responses[("GET", "/api/trainings/templates/")] = (200, {"templates": [{"name": "solo-2d", "url": url}]})
    redline.responses[("GET", "redline.school/templates/solo-2d.zip")] = (200, _zip({"../evil.js": "x"}))
    out = server.get_training_template("solo-2d", folder=str(tmp_path / "a"))
    assert "unsafe path" in out and not (tmp_path / "evil.js").exists()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "keep.txt").write_text("mine")
    redline.responses[("GET", "redline.school/templates/solo-2d.zip")] = (200, _zip({"keep.txt": "x"}))
    out = server.get_training_template("solo-2d", folder=str(tmp_path / "b"))
    assert "isn't an empty folder" in out and (tmp_path / "b" / "keep.txt").read_text() == "mine"


def test_download_version_unpacks_the_reviewed_bundle(calls, redline, tmp_path):
    data = _zip({"index.html": "<!doctype html>", "quiz.json": "{}"})
    calls.responses[("GET", "/api/trainings/t1/versions/v1/bundle/")] = (200, {
        "success": True, "encoding": "base64", "filename": "trench-abc.zip",
        "data": base64.b64encode(data).decode()})
    out = server.download_training_version("t1", "v1", folder=str(tmp_path / "work"))
    assert "Unpacked 2 files" in out and (tmp_path / "work" / "quiz.json").exists()


def test_download_version_refuses_unreviewed(calls, redline):
    calls.responses[("GET", "/api/trainings/t1/versions/v1/bundle/")] = (
        409, {"detail": "Only a version that passed the security review can be downloaded.", "code": "not_reviewed"})
    assert "not_reviewed" in server.download_training_version("t1", "v1")


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
    (tmp_path / ".DS_Store").write_text("ignored")
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


# --- phase 2: compliance -----------------------------------------------------
def test_assign_training_previews_without_confirm(calls):
    calls.responses[("POST", "/api/trainings/t1/assignments/")] = (200, {"preview": True, "assignments": []})
    out = server.assign_training("t1", ["a@b.com"], project_id="p1", valid_for_days=365, recurring=True)
    assert len(calls) == 1
    body = json.loads(calls[0].content)
    assert body == {"assignees": ["a@b.com"], "confirm": False, "recurring": True, "invite_role": "viewer",
                    "project_id": "p1", "valid_for_days": 365}
    assert out.startswith("PREVIEW ONLY")


def test_invite_users_caps_and_confirms(calls):
    assert server.invite_users("p1", [f"p{i}@x.com" for i in range(201)]).startswith("Error:")
    assert calls == []
    calls.responses[("POST", "/api/trainings/invites/")] = (200, {"preview": False, "results": []})
    server.invite_users("p1", "a@b.com, c@d.com", confirm=True)
    body = json.loads(calls[0].content)
    assert body["people"] == ["a@b.com", "c@d.com"] and body["confirm"] is True


def test_read_tools_call_one_endpoint_each(calls):
    server.list_assignments(project_id="p1")
    server.list_permission_requirements(scope="project", project_id="p1")
    server.get_my_trainings()
    assert [(c.method, c.url.path) for c in calls] == [
        ("GET", "/api/trainings/assignments/"),
        ("GET", "/api/trainings/permission-requirements/"),
        ("GET", "/api/trainings/me/"),
    ]
    assert calls[0].url.params["project_id"] == "p1"


def test_competition_tools(calls):
    server.get_training_leaderboard("t1", group_by="user_type", period="30d", limit=10)
    server.get_engagement_report(project_id="p1", since="2026-01-01")
    server.get_engagement_report(training_id="t1", group_by="project")
    assert [(c.method, c.url.path) for c in calls] == [
        ("GET", "/api/trainings/t1/leaderboard/"),
        ("GET", "/api/trainings/engagement/"),
        ("GET", "/api/trainings/t1/engagement/"),
    ]
    assert dict(calls[0].url.params) == {"group_by": "user_type", "period": "30d", "limit": "10"}
    assert dict(calls[1].url.params) == {"project_id": "p1", "since": "2026-01-01"}
    assert dict(calls[2].url.params) == {"group_by": "project"}


def test_training_required_reaches_the_agent_with_title_expiry_and_link(calls):
    calls.responses[("POST", "/api/mapping/items/i1/mark-accepted/")] = (403, {
        "code": "training_required", "detail": "Requires \"Weld Acceptance\" to accept Welds.",
        "trainings": [{"title": "Weld Acceptance", "expired_at": "2026-10-01T00:00:00Z",
                       "play_url": "/trainings/t1/play/"}],
    })
    out = server.mark_map_item_accepted("i1")
    assert "Training required" in out and "[code: training_required]" in out
    assert "Weld Acceptance (expired 2026-10-01)" in out
    assert "/trainings/t1/play/" in out and "don't retry" in out
    assert "not a member" not in out
