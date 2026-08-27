"""Tests for zipmap flattening - the `.zipmap` -> `.zipmap.json` conversion.

The upload is one all-or-nothing transaction against the API, so everything that
can be caught locally (a missing PNG, a type with no QC Database schema id, a
coordinate outside the drawing) must be caught before a multi-megabyte POST.

Run: ``pytest`` (from the repo root, with ``pip install -e .`` or PYTHONPATH=src).
"""

from __future__ import annotations

import base64
import io
import json
import struct
import zipfile
import zlib

import httpx
import pytest

from qcdatabase_mcp import zipmap as zm
from qcdatabase_mcp.client import APIError, QCClient


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _png(width: int = 800, height: int = 600) -> bytes:
    """A minimal but genuinely valid 8-bit greyscale PNG."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


WELD_ITEMS = [
    {"id": "W-101", "x": 120, "y": 300, "x2": 160, "y2": 245, "size": '2"', "material": "A106-B"},
    {"id": "W-102", "x": 470, "y": 300, "x2": 515, "y2": 360, "size": '2"', "material": "A106-B"},
]


def _members(**overrides: bytes) -> dict[str, bytes]:
    members = {
        "manifest.json": json.dumps(
            {
                "zipmap": "1.1",
                "title": "Demo Loop",
                "drawing_number": "DEMO-001",
                "revision": "A",
                "source": "img",
                "image": {"file": "drawing.png", "width": 800, "height": 600},
                "types": ["weld"],
            }
        ).encode(),
        "img/drawing.png": _png(),
        "img/weld.json": json.dumps(
            {"space": "img", "width": 800, "height": 600, "schema": "weld", "items": WELD_ITEMS}
        ).encode(),
        "schemata/weld.schema.json": json.dumps(
            {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"}
        ).encode(),
    }
    members.update(overrides)
    return {k: v for k, v in members.items() if v is not None}


def _archive(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, raw in members.items():
            zf.writestr(name, raw)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# reading the archive
# ---------------------------------------------------------------------------
def test_read_archive_keeps_only_wanted_members():
    members = _members()
    members["../../store.json"] = b"{}"          # traversal attempt
    members["notes/readme.txt"] = b"hello"       # unrelated payload
    members["pdf/drawing.pdf"] = b"%PDF-1.4\n%%EOF"

    got = zm.read_archive(_archive(members))

    assert set(got) == {
        "manifest.json",
        "img/drawing.png",
        "img/weld.json",
        "schemata/weld.schema.json",
        "pdf/drawing.pdf",
    }


def test_read_archive_rejects_non_zip():
    with pytest.raises(zm.ZipmapError):
        zm.read_archive(b"not a zip at all")


# ---------------------------------------------------------------------------
# schema id binding
# ---------------------------------------------------------------------------
def test_unbound_type_is_reported_not_uploaded():
    members = zm.read_archive(_archive(_members()))

    found = zm.inspect(members)
    assert found["unbound"] == ["weld"]
    assert found["types"][0]["fields"] == ["material", "size"]

    with pytest.raises(zm.ZipmapError) as exc:
        zm.build_document(members)
    assert "no QC Database schema id" in str(exc.value)


def test_schema_id_from_schema_file_and_from_override():
    bound = _members(
        **{
            "schemata/weld.schema.json": json.dumps(
                {"zipmap": {"schema_id": "from-file"}, "$id": "ignored-when-hint-present"}
            ).encode()
        }
    )
    doc, _ = zm.build_document(zm.read_archive(_archive(bound)))
    assert doc["map_item_datasets"][0]["schema_id"] == "from-file"

    doc, _ = zm.build_document(
        zm.read_archive(_archive(bound)), schema_ids={"weld": "from-caller"}
    )
    assert doc["map_item_datasets"][0]["schema_id"] == "from-caller"


def test_json_schema_dollar_schema_is_not_a_schema_id():
    # "$schema" declares the JSON Schema dialect; only "$id" is an identity.
    assert zm.schema_id_of({"$schema": "http://json-schema.org/draft-07/schema#"}) is None
    assert zm.schema_id_of({"$id": "wsc_01H2XYZ"}) == "wsc_01H2XYZ"


# ---------------------------------------------------------------------------
# the document itself
# ---------------------------------------------------------------------------
def test_build_document_shape():
    png = _png()
    members = zm.read_archive(
        _archive(
            _members(
                **{
                    "extracted_data.json": json.dumps({"line_number": "DEMO-CW-001"}).encode(),
                    "pdf/drawing.pdf": b"%PDF-1.4\nbody\n%%EOF",
                }
            )
        )
    )

    doc, info = zm.build_document(members, schema_ids={"weld": "wsc_1"})

    assert doc["zipmap_json"] == "1.1"
    assert doc["title"] == "Demo Loop"
    assert doc["drawing_number"] == "DEMO-001"
    assert doc["revision"] == "A"
    assert doc["image"] == {"format": "png", "width": 800, "height": 600}
    assert doc["pdf"]["pages"] == 1          # the archive's writer proved single-page
    assert doc["extracted_data"] == {"line_number": "DEMO-CW-001"}
    assert base64.b64decode(doc["b64"]) == png
    assert base64.b64decode(doc["pdf_b64"]).startswith(b"%PDF-")
    assert doc["map_item_datasets"] == [{"schema_id": "wsc_1", "map_items": WELD_ITEMS}]
    assert info["items"] == 2


def test_folder_source_omits_unverified_page_count():
    doc, _ = zm.build_document(
        _members(**{"pdf/drawing.pdf": b"%PDF-1.4\n%%EOF"}),
        schema_ids={"weld": "wsc_1"},
        from_archive=False,
    )
    assert "pages" not in doc["pdf"]


def test_extracted_data_override_wins():
    members = zm.read_archive(
        _archive(_members(**{"extracted_data.json": json.dumps({"a": 1}).encode()}))
    )
    doc, _ = zm.build_document(
        members, schema_ids={"weld": "wsc_1"}, extracted_data={"b": 2}
    )
    assert doc["extracted_data"] == {"b": 2}


# ---------------------------------------------------------------------------
# validation that must happen before the upload
# ---------------------------------------------------------------------------
def test_missing_png_is_refused():
    members = _members()
    del members["img/drawing.png"]
    with pytest.raises(zm.ZipmapError) as exc:
        zm.build_document(members, schema_ids={"weld": "wsc_1"})
    assert "drawing.png" in str(exc.value)


def test_coordinate_outside_the_drawing_is_refused():
    bad = _members(
        **{
            "img/weld.json": json.dumps(
                {
                    "space": "img",
                    "width": 800,
                    "height": 600,
                    "schema": "weld",
                    "items": [{"id": "W-1", "x": 10, "y": 900, "x2": 20, "y2": 30}],
                }
            ).encode()
        }
    )
    with pytest.raises(zm.ZipmapError) as exc:
        zm.build_document(bad, schema_ids={"weld": "wsc_1"})
    assert "outside the drawing" in str(exc.value)


def test_missing_coordinate_is_refused():
    bad = _members(
        **{
            "img/weld.json": json.dumps(
                {"space": "img", "schema": "weld", "items": [{"id": "W-1", "x": 10, "y": 20}]}
            ).encode()
        }
    )
    with pytest.raises(zm.ZipmapError) as exc:
        zm.build_document(bad, schema_ids={"weld": "wsc_1"})
    assert 'coordinate "x2"' in str(exc.value)


def test_pdf_space_layer_is_refused():
    # PDF-point coordinates against a pixel image would misplace every item.
    bad = _members(
        **{
            "img/weld.json": json.dumps(
                {"space": "pdf", "width": 612, "height": 792, "schema": "weld", "items": []}
            ).encode()
        }
    )
    with pytest.raises(zm.ZipmapError) as exc:
        zm.build_document(bad, schema_ids={"weld": "wsc_1"})
    assert "img" in str(exc.value)


def test_dimension_mismatch_is_refused():
    bad = _members(
        **{
            "img/weld.json": json.dumps(
                {"space": "img", "width": 1600, "height": 1200, "schema": "weld", "items": []}
            ).encode()
        }
    )
    with pytest.raises(zm.ZipmapError) as exc:
        zm.build_document(bad, schema_ids={"weld": "wsc_1"})
    assert "different space" in str(exc.value)


# ---------------------------------------------------------------------------
# already-flattened documents
# ---------------------------------------------------------------------------
def test_load_document_round_trip():
    members = zm.read_archive(_archive(_members()))
    doc, _ = zm.build_document(members, schema_ids={"weld": "wsc_1"})

    back, info = zm.load_document(json.dumps(doc).encode())

    assert back["map_item_datasets"] == doc["map_item_datasets"]
    assert info["counts"] == {"wsc_1": 2}
    assert info["image"] == {"width": 800, "height": 600, "bytes": len(_png())}


def test_load_document_requires_a_schema_id_per_dataset():
    doc = {
        "b64": base64.b64encode(_png()).decode(),
        "map_item_datasets": [{"map_items": []}],
    }
    with pytest.raises(zm.ZipmapError) as exc:
        zm.load_document(json.dumps(doc).encode())
    assert "schema_id" in str(exc.value)


def test_load_document_rejects_a_non_png_payload():
    doc = {"b64": base64.b64encode(b"%PDF-1.4").decode(), "map_item_datasets": []}
    with pytest.raises(zm.ZipmapError):
        zm.load_document(json.dumps(doc).encode())


# ---------------------------------------------------------------------------
# server-side rejection: the pointers say WHICH item failed, so keep them
# ---------------------------------------------------------------------------
def test_validation_pointers_survive_into_the_error_message():
    resp = httpx.Response(
        422,
        json={
            "error": "zipmap validation failed",
            "errors": [
                {
                    "pointer": "/map_item_datasets/0/map_items/3/x",
                    "code": "out_of_bounds",
                    "message": "x=9000 outside image width",
                    "item_id": "W-104",
                },
                {"pointer": "/package_id", "code": "required", "message": "package_id is required"},
            ],
        },
        request=httpx.Request("POST", "https://qcdatabase.ai/api/mapping/projects/p/zipmaps/"),
    )
    with pytest.raises(APIError) as exc:
        QCClient._handle(QCClient.__new__(QCClient), resp)

    text = str(exc.value)
    assert "zipmap validation failed" in text
    assert "/map_item_datasets/0/map_items/3/x: x=9000 outside image width" in text
    assert "/package_id: package_id is required" in text


# ---------------------------------------------------------------------------
# reading a zipmap off the local disk (server side; the guard still applies)
# ---------------------------------------------------------------------------
def _server(monkeypatch):
    monkeypatch.delenv("QCDB_MCP_HTTP", raising=False)
    from qcdatabase_mcp import server

    return server


def _write_folder(root, members):
    for name, raw in members.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def test_zipmap_members_reads_an_archive_and_a_folder(monkeypatch, tmp_path):
    server = _server(monkeypatch)
    expected = set(_members())

    archive = tmp_path / "demo.zipmap"
    archive.write_bytes(_archive(_members()))
    members, from_archive = server._zipmap_members(str(archive))
    assert from_archive is True
    assert set(members) == expected

    folder = tmp_path / "demo"
    _write_folder(folder, _members())
    (folder / "notes.txt").write_text("ignored")
    members, from_archive = server._zipmap_members(str(folder))
    assert from_archive is False
    assert set(members) == expected


def test_zipmap_members_refuses_a_folder_that_is_not_a_zipmap(monkeypatch, tmp_path):
    server = _server(monkeypatch)
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        server._zipmap_members(str(tmp_path / "empty"))


def test_zipmap_members_consumes_upload_handle_in_hosted_mode(monkeypatch):
    server = _server(monkeypatch)
    monkeypatch.setenv("QCDB_MCP_HTTP", "1")
    archive = _archive(_members())

    class Uploads:
        @staticmethod
        def resolve_upload(handle):
            assert handle == "upload-handle"
            return "demo.zipmap", io.BytesIO(archive), "application/octet-stream"

    monkeypatch.setattr(server, "_uploads", Uploads())
    members, from_archive = server._zipmap_members("upload-handle")
    assert from_archive is True
    assert set(members) == set(_members())


def test_read_bytes_consumes_upload_handle_in_hosted_mode(monkeypatch):
    server = _server(monkeypatch)
    monkeypatch.setenv("QCDB_MCP_HTTP", "1")

    class Uploads:
        @staticmethod
        def resolve_upload(handle):
            assert handle == "upload-handle"
            return "map.zipmap", io.BytesIO(b"zipmap bytes"), "application/octet-stream"

    monkeypatch.setattr(server, "_uploads", Uploads())
    assert server._read_bytes("upload-handle") == b"zipmap bytes"


@pytest.mark.parametrize(
    ("filename", "handle"),
    [
        ("flattened.zipmap.json", "opaque-handle"),
        ("upload-without-an-extension", "another-handle"),
    ],
)
def test_upload_zipmap_recognizes_hosted_json_by_name_or_content(
    monkeypatch, filename, handle
):
    server = _server(monkeypatch)
    monkeypatch.setenv("QCDB_MCP_HTTP", "1")
    document, _ = zm.build_document(
        zm.read_archive(_archive(_members())), schema_ids={"weld": "wsc_1"}
    )
    raw = json.dumps(document).encode()
    consumed = []

    class Uploads:
        @staticmethod
        def resolve_upload(got):
            consumed.append(got)
            return filename, io.BytesIO(raw), "application/json"

    class Store:
        @staticmethod
        def get_active_project():
            return {"id": "proj-1"}

    class Client:
        store = Store()
        posted = None

        def post(self, path, json=None, **kwargs):
            assert path == "/api/mapping/projects/proj-1/zipmaps/"
            self.posted = json
            return {
                "drawing_id": "drawing-1",
                "package_id": "package-1",
                "mode": "append",
                "map_items_created": 2,
            }

    api = Client()
    monkeypatch.setattr(server, "_uploads", Uploads())
    monkeypatch.setattr(server, "client", lambda: api)

    result = server.upload_zipmap(handle, "package-1")

    assert "Uploaded zipmap" in result
    assert consumed == [handle]  # upload handles are single-use
    assert api.posted["document"]["zipmap_json"] == "1.1"


def test_inspect_zipmap_recognizes_hosted_archive_by_magic(monkeypatch):
    server = _server(monkeypatch)
    monkeypatch.setenv("QCDB_MCP_HTTP", "1")
    raw = _archive(_members())

    class Uploads:
        @staticmethod
        def resolve_upload(handle):
            return "upload-without-an-extension", io.BytesIO(raw), "application/octet-stream"

    monkeypatch.setattr(server, "_uploads", Uploads())
    result = server.inspect_zipmap("opaque-handle")
    assert "Zipmap: upload-without-an-extension" in result
    assert "type 'weld'" in result


def test_inspect_zipmap_archive_bytes_override_misleading_json_name(monkeypatch):
    server = _server(monkeypatch)
    monkeypatch.setenv("QCDB_MCP_HTTP", "1")
    raw = _archive(_members())

    class Uploads:
        @staticmethod
        def resolve_upload(handle):
            return "actually-an-archive.json", io.BytesIO(raw), "application/json"

    monkeypatch.setattr(server, "_uploads", Uploads())
    result = server.inspect_zipmap("opaque-handle")
    assert "Zipmap: actually-an-archive.json" in result
    assert "type 'weld'" in result


def test_inspect_local_json_expands_home_directory(monkeypatch, tmp_path):
    server = _server(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    document, _ = zm.build_document(
        zm.read_archive(_archive(_members())), schema_ids={"weld": "wsc_1"}
    )
    (tmp_path / "demo.zipmap.json").write_text(json.dumps(document))

    result = server.inspect_zipmap("~/demo.zipmap.json")

    assert "flattened .zipmap.json document" in result
    assert "schema wsc_1" in result
