"""Read a zipmap and flatten it into the `.zipmap.json` interchange document.

A **zipmap** (https://github.com/ProcessQualitySolutions/zipmaps) is a plain zip
archive that packages ONE construction drawing (a PNG, optionally the source
single-page PDF) together with the map items somebody's AI - or CAD export, or
takeoff tool - already placed on it, plus the JSON Schemas describing each item
type:

    example.zipmap
    |-- manifest.json
    |-- extracted_data.json     (optional)
    |-- schemata/<type>.schema.json
    |-- img/drawing.png         (required; the coordinate space)
    |-- img/<type>.json         (items of that type, in PNG pixels)
    +-- pdf/drawing.pdf         (optional; the print master)

QC Database ingests the *interchange* form of that archive - a single JSON
object (`.zipmap.json`) with the PNG and PDF base64-encoded inline and the item
datasets keyed by the **server-side** map item schema id - in one transactional
POST that creates the drawing, its map items, and its extracted-data record.

This module does the flattening, so the assistant can hand the server a
`.zipmap` file and have it land as a mapped drawing. It is deliberately pure:
it works over an in-memory ``{member name: bytes}`` mapping supplied by
``server.py`` (which owns every local file read and its safety guard) and never
touches the filesystem itself.

Two things the archive cannot know are resolved here:

* **schema ids.** A zipmap names its types locally ("weld", "heat"). Each type
  must be bound to a QC Database map item schema id before upload - either
  written into ``schemata/<type>.schema.json`` (``{"zipmap": {"schema_id":
  "..."}}`` or ``$id``) by the producer, or supplied by the caller.
* **the package.** The API requires the scope package the created drawing is
  filed into; that never travels inside a zipmap.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import struct
import zipfile
from io import BytesIO
from typing import Any, Iterable

# The interchange version this module emits. 1.0 archives remain valid; 1.1
# added the optional PDF print master and the extracted-data record.
JSON_FORMAT_VERSION = "1.1"

MANIFEST = "manifest.json"
EXTRACTED_DATA = "extracted_data.json"
IMG_DIR = "img"
PDF_DIR = "pdf"
SCHEMATA_DIR = "schemata"
DRAWING_PNG = "drawing.png"
DRAWING_PDF = "drawing.pdf"

# Every item carries these four, always in PNG pixels with a top-left origin.
COORD_FIELDS = ("x", "y", "x2", "y2")

# Metadata the manifest may carry through to the document.
META_FIELDS = ("title", "drawing_number", "revision")

# Only these members are ever read out of an archive or folder. Anything else in
# the zip is ignored, so an archive can carry extra material without surprising
# the upload.
_IMG_DATA = re.compile(rf"^{IMG_DIR}/(?P<stem>[^/]+)\.json$")
_SCHEMA_FILE = re.compile(rf"^{SCHEMATA_DIR}/(?P<stem>[^/]+)\.schema\.json$")

# Guard rails on how much we will pull into memory from one archive. The whole
# document is base64-encoded into a single request body, so anything near this
# is already too big to POST comfortably.
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 500


class ZipmapError(ValueError):
    """The zipmap could not be read or is not valid for upload."""


def wanted_member(name: str) -> bool:
    """Is this archive/folder member one the interchange document needs?"""
    if name in (MANIFEST, EXTRACTED_DATA):
        return True
    if name in (f"{IMG_DIR}/{DRAWING_PNG}", f"{PDF_DIR}/{DRAWING_PDF}"):
        return True
    return bool(_IMG_DATA.match(name) or _SCHEMA_FILE.match(name))


# ---------------------------------------------------------------------------
# Reading an archive
# ---------------------------------------------------------------------------
def read_archive(raw: bytes) -> dict[str, bytes]:
    """Return the wanted members of a ``.zipmap`` archive, read from memory.

    Nothing is extracted to disk, so a crafted member name cannot escape
    anywhere; names are matched against a fixed whitelist and everything else in
    the zip is skipped. Reads are capped so a zip bomb cannot exhaust memory.
    """
    try:
        zf = zipfile.ZipFile(BytesIO(raw))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ZipmapError(
            f"That file is not a readable zip archive ({exc}). A .zipmap is a plain "
            "zip; if you have a .zipmap.json document, pass that instead."
        ) from exc

    members: dict[str, bytes] = {}
    total = 0
    with zf:
        for info in zf.infolist():
            if info.is_dir() or not wanted_member(info.filename):
                continue
            if len(members) >= MAX_MEMBERS:
                raise ZipmapError(f"Archive has more than {MAX_MEMBERS} data members.")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ZipmapError(
                    "Archive contents exceed "
                    f"{MAX_TOTAL_BYTES // (1024 * 1024)} MB uncompressed - too large "
                    "to upload in one request."
                )
            try:
                members[info.filename] = zf.read(info)
            except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
                raise ZipmapError(f"Could not read '{info.filename}' from the archive: {exc}") from exc
    return members


def member_names(members: Iterable[str]) -> list[str]:
    """The item-type names (``img/<type>.json`` stems) present, sorted."""
    stems = [m.group("stem") for name in members if (m := _IMG_DATA.match(name))]
    return sorted(stems)


# ---------------------------------------------------------------------------
# PNG geometry
# ---------------------------------------------------------------------------
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(raw: bytes) -> tuple[int, int]:
    """Return (width, height) from a PNG's IHDR, or raise if it is not a PNG."""
    if len(raw) < 24 or not raw.startswith(_PNG_SIGNATURE):
        raise ZipmapError(
            f"{IMG_DIR}/{DRAWING_PNG} is not a PNG file (a zipmap's drawing image "
            "must be PNG)."
        )
    if raw[12:16] != b"IHDR":
        raise ZipmapError(f"{IMG_DIR}/{DRAWING_PNG} has no IHDR header - the PNG is corrupt.")
    width, height = struct.unpack(">II", raw[16:24])
    if width <= 0 or height <= 0:
        raise ZipmapError(f"{IMG_DIR}/{DRAWING_PNG} declares a zero dimension ({width}x{height}).")
    return int(width), int(height)


# ---------------------------------------------------------------------------
# Schema ids
# ---------------------------------------------------------------------------
def schema_id_of(schema: Any) -> str | None:
    """Read the server-side schema id a zipmap schema file declares, if any."""
    if not isinstance(schema, dict):
        return None
    hint = schema.get("zipmap")
    if isinstance(hint, dict):
        sid = hint.get("schema_id")
        if isinstance(sid, str) and sid.strip():
            return sid.strip()
    sid = schema.get("$id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None


def _load_json(members: dict[str, bytes], name: str) -> Any:
    raw = members.get(name)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ZipmapError(f"{name}: cannot parse JSON ({exc}).") from exc


def _bound_schema_id(
    members: dict[str, bytes], stem: str, overrides: dict[str, str]
) -> str | None:
    """Resolve a type's schema id: caller override, then the schema file."""
    override = overrides.get(stem)
    if isinstance(override, str) and override.strip():
        return override.strip()
    return schema_id_of(_load_json(members, f"{SCHEMATA_DIR}/{stem}.schema.json"))


# ---------------------------------------------------------------------------
# Inspection (what is in this zipmap, and what is still unbound)
# ---------------------------------------------------------------------------
def inspect(members: dict[str, bytes], overrides: dict[str, str] | None = None) -> dict[str, Any]:
    """Summarize a zipmap without building (or uploading) the document.

    Returns the drawing metadata, the image size, and one entry per item type
    with its item count, resolved schema id (or None when nothing has bound it
    yet), and the data field names its items actually use - which is what an
    assistant needs in order to match the type against a QC Database map item
    schema's fields.
    """
    overrides = overrides or {}
    manifest = _load_json(members, MANIFEST) or {}
    if not isinstance(manifest, dict):
        manifest = {}

    png = members.get(f"{IMG_DIR}/{DRAWING_PNG}")
    width = height = None
    if png is not None:
        width, height = png_size(png)

    types: list[dict[str, Any]] = []
    for stem in member_names(members):
        data = _load_json(members, f"{IMG_DIR}/{stem}.json")
        items = data.get("items") if isinstance(data, dict) else None
        fields: set[str] = set()
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    fields.update(k for k in item if k not in COORD_FIELDS and k != "id")
        types.append(
            {
                "type": stem,
                "count": len(items) if isinstance(items, list) else 0,
                "schema_id": _bound_schema_id(members, stem, overrides),
                "fields": sorted(fields),
                "space": data.get("space") if isinstance(data, dict) else None,
            }
        )

    return {
        "title": manifest.get("title"),
        "drawing_number": manifest.get("drawing_number"),
        "revision": manifest.get("revision"),
        "image": {"width": width, "height": height, "bytes": len(png) if png else 0},
        "has_pdf": f"{PDF_DIR}/{DRAWING_PDF}" in members,
        "has_extracted_data": EXTRACTED_DATA in members,
        "types": types,
        "unbound": [t["type"] for t in types if not t["schema_id"]],
    }


# ---------------------------------------------------------------------------
# Building the interchange document
# ---------------------------------------------------------------------------
def build_document(
    members: dict[str, bytes],
    *,
    schema_ids: dict[str, str] | None = None,
    from_archive: bool = True,
    include_pdf: bool = True,
    include_extracted_data: bool = True,
    extracted_data: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Flatten a zipmap's members into the `.zipmap.json` document.

    Returns ``(document, info)``. Raises ZipmapError with everything that is
    wrong - a missing PNG, an unbound type, a coordinate outside the image -
    rather than letting the server reject a multi-megabyte upload.
    """
    schema_ids = schema_ids or {}
    png = members.get(f"{IMG_DIR}/{DRAWING_PNG}")
    if png is None:
        raise ZipmapError(
            f"This zipmap has no {IMG_DIR}/{DRAWING_PNG}. Every zipmap must carry the "
            "drawing image - a .zipmapt template (schemata only) cannot be uploaded."
        )
    width, height = png_size(png)

    manifest = _load_json(members, MANIFEST) or {}
    if not isinstance(manifest, dict):
        manifest = {}

    errors: list[str] = []
    datasets: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    stems = member_names(members)
    if not stems:
        errors.append(
            f"No item data files ({IMG_DIR}/<type>.json) - there is nothing to map. "
            "Upload the drawing with 'upload_drawing_to_package' instead."
        )

    for stem in stems:
        data = _load_json(members, f"{IMG_DIR}/{stem}.json")
        if not isinstance(data, dict):
            errors.append(f"{IMG_DIR}/{stem}.json: must be a JSON object.")
            continue
        items = data.get("items")
        if not isinstance(items, list):
            errors.append(f'{IMG_DIR}/{stem}.json: "items" must be an array.')
            continue

        space = data.get("space")
        if space is not None and space != IMG_DIR:
            errors.append(
                f'{IMG_DIR}/{stem}.json: "space" is {space!r}; only the pixel ("img") '
                "layer can be uploaded - PDF-space coordinates would misplace every item."
            )
        for dim, actual in (("width", width), ("height", height)):
            declared = data.get(dim)
            if isinstance(declared, (int, float)) and not isinstance(declared, bool):
                if int(declared) != actual:
                    errors.append(
                        f"{IMG_DIR}/{stem}.json: declares {dim} {declared!r} but the PNG "
                        f"is {width}x{height} - the coordinates are in a different space."
                    )

        schema_id = _bound_schema_id(members, stem, schema_ids)
        if not schema_id:
            errors.append(
                f"'{stem}': no QC Database schema id. Pick the map item schema this type "
                "maps to (use 'list_map_item_schemas') and pass it in schema_ids, e.g. "
                f'{{"{stem}": "<schema id>"}}.'
            )

        errors.extend(_check_items(stem, items, width, height))
        if schema_id:
            datasets.append({"schema_id": schema_id, "map_items": items})
            counts[schema_id] = counts.get(schema_id, 0) + len(items)

    if errors:
        raise ZipmapError(
            "This zipmap cannot be uploaded yet:\n  - " + "\n  - ".join(errors)
        )

    doc: dict[str, Any] = {"zipmap_json": JSON_FORMAT_VERSION}
    for field in META_FIELDS:
        if manifest.get(field):
            doc[field] = manifest[field]
    doc["image"] = {"format": "png", "width": width, "height": height}

    pdf_raw = members.get(f"{PDF_DIR}/{DRAWING_PDF}") if include_pdf else None
    if pdf_raw is not None:
        _check_pdf_bytes(pdf_raw)
        doc["pdf"] = _pdf_block(manifest, from_archive)

    extracted = extracted_data
    if extracted is None and include_extracted_data:
        got = _load_json(members, EXTRACTED_DATA)
        if got is not None and not isinstance(got, dict):
            raise ZipmapError(f"{EXTRACTED_DATA}: must be a JSON object.")
        extracted = got
    if extracted is not None:
        doc["extracted_data"] = extracted

    # The base64 blobs go last: each is one enormous line and everything worth
    # reading by eye belongs above them (key order means nothing to the API).
    doc["b64"] = base64.b64encode(png).decode("ascii")
    if pdf_raw is not None:
        doc["pdf_b64"] = base64.b64encode(pdf_raw).decode("ascii")
    doc["map_item_datasets"] = datasets

    info = {
        "image": {"width": width, "height": height, "bytes": len(png)},
        "pdf_bytes": len(pdf_raw) if pdf_raw is not None else 0,
        "has_extracted_data": extracted is not None,
        "counts": counts,
        "items": sum(counts.values()),
        "title": doc.get("title"),
        "drawing_number": doc.get("drawing_number"),
        "revision": doc.get("revision"),
    }
    return doc, info


def _check_items(stem: str, items: list[Any], width: int, height: int) -> list[str]:
    """Validate an item list's coordinates against the PNG's pixel bounds."""
    errors: list[str] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{IMG_DIR}/{stem}.json items[{i}]: must be an object.")
            continue
        where = f"{IMG_DIR}/{stem}.json items[{i}]"
        if item.get("id") is not None:
            where += f" (id={item['id']})"
        bad = False
        for field in COORD_FIELDS:
            value = item.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f'{where}: coordinate "{field}" is missing or not a number.')
                bad = True
        if bad:
            continue
        for field, limit in (("x", width), ("x2", width), ("y", height), ("y2", height)):
            if not 0 <= item[field] <= limit:
                errors.append(
                    f"{where}: {field}={item[field]} is outside the drawing "
                    f"(0..{limit} px)."
                )
        if len(errors) > 40:
            errors.append("... (further coordinate errors not listed)")
            break
    return errors


def _check_pdf_bytes(raw: bytes) -> None:
    if not raw.startswith(b"%PDF-"):
        raise ZipmapError(f"{PDF_DIR}/{DRAWING_PDF} is not a PDF file.")
    if b"%%EOF" not in raw[-2048:]:
        raise ZipmapError(f"{PDF_DIR}/{DRAWING_PDF} looks truncated (no %%EOF marker).")


def _pdf_block(manifest: dict[str, Any], from_archive: bool) -> dict[str, Any]:
    """Describe the embedded PDF the way ``image`` describes the embedded PNG."""
    block: dict[str, Any] = {"format": "pdf"}
    dims = manifest.get("pdf")
    if isinstance(dims, dict):
        for field in ("width", "height"):
            value = dims.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                block[field] = value
    if from_archive:
        # A .zipmap archive only exists because its writer proved the PDF is
        # single-page; claiming "pages": 1 on an unverified file would be the one
        # lie the receiver cannot cheaply catch, so a loose folder omits it.
        block["pages"] = 1
    return block


# ---------------------------------------------------------------------------
# Reading an already-flattened .zipmap.json
# ---------------------------------------------------------------------------
def load_document(raw: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse and sanity-check a ``.zipmap.json`` interchange document."""
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ZipmapError(f"Cannot parse that .zipmap.json file ({exc}).") from exc
    if not isinstance(doc, dict):
        raise ZipmapError("A .zipmap.json document must be a JSON object.")

    b64 = doc.get("b64")
    if not isinstance(b64, str) or not b64.strip():
        raise ZipmapError('This document has no "b64" drawing image.')
    try:
        png = base64.b64decode("".join(b64.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ZipmapError(f'"b64" is not valid base64 ({exc}).') from exc
    width, height = png_size(png)

    datasets = doc.get("map_item_datasets")
    if not isinstance(datasets, list):
        raise ZipmapError('"map_item_datasets" must be an array.')

    errors: list[str] = []
    counts: dict[str, int] = {}
    for i, dataset in enumerate(datasets):
        if not isinstance(dataset, dict):
            errors.append(f"map_item_datasets[{i}]: must be an object.")
            continue
        sid = dataset.get("schema_id")
        if not isinstance(sid, str) or not sid.strip():
            errors.append(f'map_item_datasets[{i}]: "schema_id" is required.')
            sid = None
        items = dataset.get("map_items")
        if not isinstance(items, list):
            errors.append(f'map_item_datasets[{i}]: "map_items" must be an array.')
            continue
        errors.extend(_check_items(sid or f"dataset{i}", items, width, height))
        if sid:
            counts[sid] = counts.get(sid, 0) + len(items)
    if errors:
        raise ZipmapError(
            "This .zipmap.json cannot be uploaded yet:\n  - " + "\n  - ".join(errors)
        )

    pdf_b64 = doc.get("pdf_b64")
    info = {
        "image": {"width": width, "height": height, "bytes": len(png)},
        "pdf_bytes": len(pdf_b64) // 4 * 3 if isinstance(pdf_b64, str) else 0,
        "has_extracted_data": isinstance(doc.get("extracted_data"), dict),
        "counts": counts,
        "items": sum(counts.values()),
        "title": doc.get("title"),
        "drawing_number": doc.get("drawing_number"),
        "revision": doc.get("revision"),
    }
    return doc, info
