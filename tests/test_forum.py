"""Forum tools: thin wrappers over /api/forum/ (one endpoint per tool), JSON
writes without an image and multipart with one, paging passthrough, untrimmed
and fenced forum text, and the re-consent message for connections without the
forum scope."""
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


def test_list_boards(calls):
    calls.responses[("GET", "/api/forum/boards/")] = (200, [{"id": "b1", "slug": "welding", "post_count": 3}])
    out = server.list_forum_boards()
    assert calls[0].url.path == "/api/forum/boards/"
    assert "welding" in out and "(1 found)" in out


def test_list_posts_passes_filters_and_paging(calls):
    calls.responses[("GET", "/api/forum/posts/")] = (200, {
        "count": 30, "next": "https://qc.test/api/forum/posts/?page=2", "previous": None,
        "results": [{"id": "p1", "title": "PWHT on P91?"}],
    })
    out = server.list_forum_posts(board="welding", search="pwht", is_question=True, page=1, page_size=10)
    params = dict(calls[0].url.params)
    assert params == {"board": "welding", "search": "pwht", "is_question": "true", "page": "1", "page_size": "10"}
    assert "PWHT on P91?" in out
    assert "30 in total" in out and "next page" in out


def test_list_posts_omits_unset_filters(calls):
    calls.responses[("GET", "/api/forum/posts/")] = (200, {"count": 0, "next": None, "results": []})
    out = server.list_forum_posts(is_question=False)
    assert dict(calls[0].url.params) == {"is_question": "false", "page": "1", "page_size": "25"}
    assert "No forum posts found." in out


def test_bad_paging_never_calls_the_api(calls):
    with pytest.raises(ToolError):
        server.list_forum_posts(page_size=500)
    with pytest.raises(ToolError):
        server.list_forum_replies("p1", page=0)
    assert calls == []


def test_get_post_is_fenced_and_untrimmed(calls):
    long_text = "x" * 2000
    calls.responses[("GET", "/api/forum/posts/p1/")] = (200, {"id": "p1", "content": long_text, "replies": []})
    out = server.get_forum_post("p1")
    assert out.startswith(server._DATA_FENCE)
    assert long_text in out


def test_list_replies(calls):
    calls.responses[("GET", "/api/forum/posts/p1/replies/")] = (200, {
        "count": 1, "next": None, "results": [{"id": "r1", "content": "Yes, per B31.1"}],
    })
    out = server.list_forum_replies("p1", page=2, page_size=5)
    assert dict(calls[0].url.params) == {"page": "2", "page_size": "5"}
    assert "Yes, per B31.1" in out and "next page" not in out


def test_create_post_sends_json_without_image(calls):
    calls.responses[("POST", "/api/forum/posts/")] = (201, {"id": "p9", "web_url": "https://qc.test/forum/p9/"})
    out = server.create_forum_post("welding", "Title", "Body", is_question=True)
    assert calls[0].headers["content-type"] == "application/json"
    assert json.loads(calls[0].content) == {"board": "welding", "title": "Title", "content": "Body", "is_question": True}
    assert "Posted 'Title'." in out and "https://qc.test/forum/p9/" in out


def test_create_post_with_image_is_multipart(calls, tmp_path):
    img = tmp_path / "joint.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    calls.responses[("POST", "/api/forum/posts/")] = (201, {"id": "p9"})
    server.create_forum_post("welding", "Title", "Body", image_path=str(img))
    req = calls[0]
    assert req.headers["content-type"].startswith("multipart/form-data")
    body = req.content
    assert b'name="image"; filename="joint.png"' in body
    assert b'name="is_question"\r\n\r\nfalse' in body
    assert b'name="board"\r\n\r\nwelding' in body


def test_reply_with_parent(calls):
    calls.responses[("POST", "/api/forum/posts/p1/replies/")] = (201, {"id": "r2"})
    out = server.reply_to_forum_post("p1", "Agreed", parent_reply_id="r1")
    assert json.loads(calls[0].content) == {"content": "Agreed", "parent_reply": "r1"}
    assert out.startswith("Replied to post p1.")


def test_reply_without_parent_omits_it(calls):
    server.reply_to_forum_post("p1", "Agreed")
    assert json.loads(calls[0].content) == {"content": "Agreed"}


def test_banned_user_error_passes_through(calls):
    calls.responses[("POST", "/api/forum/posts/p1/replies/")] = (403, {"detail": "You are banned from the forum."})
    with pytest.raises(ToolError, match="banned") as exc:
        server.reply_to_forum_post("p1", "hi")
    assert "forum:read / forum:write" not in str(exc.value)


def test_missing_scope_explains_reconsent(calls):
    calls.responses[("GET", "/api/forum/boards/")] = (403, {"detail": "The access token does not grant the scope required for this request."})
    with pytest.raises(ToolError, match="lacks the forum:read / forum:write access") as exc:
        server.list_forum_boards()
    assert "login" in str(exc.value)


def test_forum_scopes_are_requested():
    from qcdatabase_mcp.auth import DEFAULT_SCOPES

    assert "forum:read" in DEFAULT_SCOPES.split() and "forum:write" in DEFAULT_SCOPES.split()
