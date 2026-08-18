"""Regression tests for the file-download tools: base64 envelope endpoints, the
fallback to the raw-binary export endpoint, and canvas-image delivery.

Context: the API added proxy-safe download endpoints that return a file as base64
inside a JSON envelope, because a signed object-storage link cannot be fetched
from the hosted server (it gets a proxy 403). The document / drawing / large-format
download tools now prefer those envelope endpoints and fall back to the raw-binary
``/export/`` endpoint when the envelope route is missing (404/405 — e.g. an API
that hasn't deployed it yet) or the file is over the envelope's size cap (413).
Two new tools return a drawing's rendered canvas image (PNG) plus its pixel size,
so map items can be overlaid in HTML with no rescaling.

Pure unit tests: the network client is faked and local (stdio) mode is used, so
files land on the temp filesystem and each tool's output is a simple string.
Run: ``pytest`` (from the repo root).
"""

from __future__ import annotations

import base64

import pytest

from qcdatabase_mcp import server
from qcdatabase_mcp.client import APIError


def _tool(name: str):
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    return tools[name].fn


class _Store:
    def get_active_project(self):
        return {"id": "proj-1", "name": "Test Project"}


def _envelope(data: bytes, filename: str, **extra):
    env = {
        "success": True,
        "id": "x-1",
        "filename": filename,
        "content_type": "application/pdf",
        "size": len(data),
        "encoding": "base64",
        "data": base64.b64encode(data).decode("ascii"),
    }
    env.update(extra)
    return env


def _api_error(status: int) -> APIError:
    err = APIError(f"HTTP {status}")
    err.status_code = status  # type: ignore[attr-defined]
    return err


@pytest.fixture(autouse=True)
def _local_mode(monkeypatch):
    # stdio mode: _deliver_download writes to disk and returns a string.
    monkeypatch.setattr(server.hosted, "hosted_enabled", lambda: False)


# ---------------------------------------------------------------------------
# Preferred path: the proxy-safe base64 endpoint
# ---------------------------------------------------------------------------
def test_download_document_uses_base64_download_endpoint(monkeypatch, tmp_path):
    seen = {}
    pdf = b"%PDF original document bytes"

    class Fake:
        store = _Store()

        def get(self, path, **params):
            seen["get"] = path
            return _envelope(pdf, "original.pdf")

        def download(self, path, **params):  # pragma: no cover - must not run
            seen["download"] = path
            return b"RAW-SHOULD-NOT-BE-USED"

    monkeypatch.setattr(server, "client", lambda: Fake())
    dest = tmp_path / "doc.pdf"
    out = _tool("download_document")("doc-1", str(dest))

    assert seen["get"] == "/api/documents/projects/proj-1/document/doc-1/download/"
    assert "download" not in seen  # base64 worked, so no raw fallback
    assert dest.read_bytes() == pdf
    assert str(dest) in out


def test_export_drawing_prefers_base64_and_forwards_variant(monkeypatch, tmp_path):
    seen = {}
    pdf = b"%PDF drawing map overlay"

    class Fake:
        store = _Store()

        def get(self, path, **params):
            seen["get"] = path
            seen["params"] = params
            return _envelope(pdf, "D-001_map.pdf")

        def download(self, path, **params):  # pragma: no cover - must not run
            seen["download"] = path
            return b"NOPE"

    monkeypatch.setattr(server, "client", lambda: Fake())
    dest = tmp_path / "d.pdf"
    out = _tool("export_drawing")("dr-1", str(dest), variant="map", schema_id="s-9")

    assert seen["get"] == "/api/drawings/projects/proj-1/drawing/dr-1/download/"
    assert seen["params"].get("variant") == "map"
    assert seen["params"].get("schema_id") == "s-9"
    assert "download" not in seen
    assert dest.read_bytes() == pdf


# ---------------------------------------------------------------------------
# Fallback to the raw-binary /export/ endpoint
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", [404, 405, 413])
def test_download_falls_back_to_export(monkeypatch, tmp_path, status):
    seen = {}
    pdf = b"%PDF raw export bytes"

    class Fake:
        store = _Store()

        def get(self, path, **params):
            seen["get"] = path
            raise _api_error(status)

        def download(self, path, **params):
            seen["download"] = path
            seen["download_params"] = params
            return pdf

    monkeypatch.setattr(server, "client", lambda: Fake())
    dest = tmp_path / "lfd.pdf"
    out = _tool("export_large_format_drawing")("lfd-1", str(dest), variant="flagged")

    assert seen["get"].endswith("/large-format/lfd-1/download/")
    assert seen["download"].endswith("/large-format/lfd-1/export/")
    assert seen["download_params"].get("variant") == "flagged"
    assert dest.read_bytes() == pdf


def test_download_does_not_fall_back_on_other_errors(monkeypatch, tmp_path):
    seen = {}

    class Fake:
        store = _Store()

        def get(self, path, **params):
            raise _api_error(403)  # not a member: a real error, not a fallback case

        def download(self, path, **params):  # pragma: no cover - must not run
            seen["download"] = path
            return b"x"

    monkeypatch.setattr(server, "client", lambda: Fake())
    out = _tool("export_drawing")("dr-1", str(tmp_path / "d.pdf"))

    assert "download" not in seen  # a 403 must NOT trigger the raw fallback
    assert "Error:" in out and "403" in out  # surfaced as clean text via _safe


# ---------------------------------------------------------------------------
# Canvas images
# ---------------------------------------------------------------------------
def test_get_drawing_image_returns_dimensions_and_bytes(monkeypatch, tmp_path):
    png = b"\x89PNG rendered canvas"
    seen = {}

    class Fake:
        store = _Store()

        def get(self, path, **params):
            seen["get"] = path
            return _envelope(
                png, "D-001.png", content_type="image/png", width=2048, height=1536
            )

    monkeypatch.setattr(server, "client", lambda: Fake())
    dest = tmp_path / "img.png"
    out = _tool("get_drawing_image")("dr-1", str(dest))

    assert seen["get"] == "/api/drawings/projects/proj-1/drawing/dr-1/image/"
    assert dest.read_bytes() == png
    assert "2048x1536" in out  # pixel dimensions surfaced
    assert "x_position" in out  # coordinate-system note surfaced


def test_get_large_format_drawing_image_hits_lfd_image_endpoint(monkeypatch, tmp_path):
    png = b"\x89PNG lfd canvas"
    seen = {}

    class Fake:
        store = _Store()

        def get(self, path, **params):
            seen["get"] = path
            return _envelope(png, "L-1.png", content_type="image/png", width=800, height=600)

    monkeypatch.setattr(server, "client", lambda: Fake())
    dest = tmp_path / "lfd.png"
    out = _tool("get_large_format_drawing_image")("lfd-1", str(dest))

    assert seen["get"] == "/api/drawings/projects/proj-1/large-format/lfd-1/image/"
    assert dest.read_bytes() == png
    assert "800x600" in out


def test_image_tool_surfaces_malformed_envelope(monkeypatch, tmp_path):
    class Fake:
        store = _Store()

        def get(self, path, **params):
            return {"success": False, "detail": "no image yet"}

    monkeypatch.setattr(server, "client", lambda: Fake())
    out = _tool("get_drawing_image")("dr-1", str(tmp_path / "img.png"))
    assert "Error:" in out  # a bad/failed envelope is reported, not silently empty
