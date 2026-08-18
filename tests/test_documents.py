"""Regression tests for the document tools: extracted data and files.

Context: the QC Database API originally omitted ``extracted_data`` from the
document *detail* serializer (it was write-only), so ``get_document`` could not
surface it even though ``get_drawing`` could. The API was fixed to include
``extracted_data`` on the document detail response. Because the MCP's document
tools are pure pass-through (``_pretty`` dumps every field the API returns), no
MCP change was needed to read it - these tests lock that in and guard the
document *file* tools so a future refactor can't silently re-break either:

* ``get_document`` surfaces every field the API returns, including
  ``extracted_data``. (A filter creeping into the shared ``_pretty`` path would
  break this *and* ``get_drawing`` - this test is the tripwire.)
* ``set_document_extracted_data`` posts the documented payload to the write
  endpoint and surfaces the saved data.
* ``download_document`` / ``upload_document`` target the correct file endpoints
  and actually move the bytes. These run in local (stdio) mode; the hosted
  object-storage transfer bridge is covered separately.

Pure unit tests: the network client is faked, so no network or auth is needed.
Run: ``pytest`` (from the repo root).
"""

from __future__ import annotations

import pytest

from qcdatabase_mcp import server


def _tool(name: str):
    """A registered tool's underlying callable (the ``_safe``-wrapped function)."""
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    return tools[name].fn


class _Store:
    """Minimal session store stand-in: just a pinned active project."""

    def get_active_project(self):
        return {"id": "proj-1", "name": "Test Project"}


@pytest.fixture(autouse=True)
def _local_mode(monkeypatch):
    # The file tools branch on hosted vs. stdio; pin these unit tests to stdio
    # (local) mode so file ops touch the real (temp) filesystem, not the hosted
    # object-storage bridge.
    monkeypatch.setattr(server.hosted, "hosted_enabled", lambda: False)


# ---------------------------------------------------------------------------
# Extracted data
# ---------------------------------------------------------------------------
def test_get_document_surfaces_extracted_data(monkeypatch):
    doc = {
        "id": "doc-1",
        "title": "MTR 12345",
        "status": "processed",
        "file": "https://files.example/doc-1.pdf",
        "file_name": "mtr.pdf",
        "metadata": {"source": "vendor"},
        "extracted_data": {
            "heat_number": "H-8842",
            "material_grade": "A106-B",
            "yield_strength_ksi": 42,
            "notes": "Conforms to specification.",
        },
    }
    seen = {}

    class Fake:
        def get(self, path):
            seen["path"] = path
            return doc

    monkeypatch.setattr(server, "client", lambda: Fake())
    out = _tool("get_document")("doc-1")

    assert seen["path"] == "/api/documents/doc-1/"
    # The whole record passes through - including the AI-extracted structured
    # data and the file fields; nothing is filtered out.
    for token in (
        "extracted_data",
        "heat_number", "H-8842",
        "material_grade", "A106-B",
        "yield_strength_ksi", "42",
        "notes", "Conforms to specification.",
        "file", "mtr.pdf",
    ):
        assert token in out, token


def test_set_document_extracted_data_posts_payload_and_echoes(monkeypatch):
    seen = {}
    saved = {"id": "doc-1", "extracted_data": {"heat_number": "H-8842"}}

    class Fake:
        def post(self, path, json=None):
            seen["path"] = path
            seen["json"] = json
            return saved

    monkeypatch.setattr(server, "client", lambda: Fake())
    out = _tool("set_document_extracted_data")("doc-1", '{"heat_number": "H-8842"}')

    assert seen["path"] == "/api/documents/doc-1/extracted-data/"
    # The tool wraps the caller's JSON string in the documented envelope.
    assert seen["json"] == {"extracted_data": {"heat_number": "H-8842"}}
    assert "H-8842" in out and "saved" in out.lower()


# ---------------------------------------------------------------------------
# Files (download mechanics live in tests/test_file_downloads.py)
# ---------------------------------------------------------------------------
def test_upload_document_posts_multipart_to_upload_endpoint(monkeypatch, tmp_path):
    src = tmp_path / "cert.pdf"
    src.write_bytes(b"%PDF-1.7 certificate")
    seen = {}

    class Fake:
        store = _Store()

        def upload(self, path, files=None, data=None):
            field, (filename, fh, _ctype) = files[0]
            seen["path"] = path
            seen["field"] = field
            seen["filename"] = filename
            seen["body"] = fh.read()
            seen["data"] = data
            return {"success": True, "documents": [{"id": "doc-9", "file_name": "cert.pdf"}]}

    monkeypatch.setattr(server, "client", lambda: Fake())
    out = _tool("upload_document")(str(src), folder_id="fld-1")

    assert seen["path"] == "/api/documents/projects/proj-1/upload/"
    assert seen["field"] == "files"  # the API expects the "files" multipart field
    assert seen["filename"] == "cert.pdf"
    assert seen["body"] == b"%PDF-1.7 certificate"
    assert seen["data"].get("folder_id") == "fld-1"
    assert "doc-9" in out
