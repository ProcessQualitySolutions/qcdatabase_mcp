"""The QC Database MCP server.

Exposes everyday quality-control tools to an AI assistant. The design follows
the QC Database spec's strong recommendations for a daily-driver server:

* The OAuth token pins the **organization (tenant)**; you choose it in the
  browser when you log in.
* A ``set_project`` tool pins the **project** for the rest of your session, so
  you never have to repeat the project id. Project-scoped tools refuse to run
  until a project is set.
* Buy-off (marking work complete or accepted) is always the user's call. Those
  tools exist, but each result reminds you that *you* are accountable for the
  action - the server never signs off silently.

Full specification: https://qcdatabase.ai/mcp_server_spec.md
"""

from __future__ import annotations

import functools
import json
import mimetypes
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from .auth import AuthError, login as run_login
from .client import APIError, QCClient

mcp = FastMCP("qcdatabase")

_client: QCClient | None = None


def client() -> QCClient:
    global _client
    if _client is None:
        _client = QCClient()
    return _client


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
class NeedsProject(RuntimeError):
    pass


def require_project() -> str:
    """Return the pinned project id, or explain how to set one."""
    proj = client().store.get_active_project()
    if not proj or not proj.get("id"):
        raise NeedsProject(
            "No project is set for this session. Run 'set_project' first - "
            "use 'list_projects' to see what is available."
        )
    return proj["id"]


_NAME_KEYS = (
    "label", "name", "title", "drawing_number", "activity_description",
    "content", "description", "code", "item_name", "shipper_number",
    "activity_code",
)


def _name_of(item: dict[str, Any]) -> str:
    for k in _NAME_KEYS:
        v = item.get(k)
        if v:
            return str(v)[:120]
    return "(unnamed)"


def _line(item: dict[str, Any]) -> str:
    """One readable line for a resource in a list."""
    parts = [_name_of(item)]
    if item.get("status"):
        parts.append(f"[{item['status']}]")
    bits = " ".join(parts)
    out = bits
    if item.get("id"):
        out += f"\n   id: {item['id']}"
    if item.get("web_url"):
        out += f"\n   link: {item['web_url']}"
    return out


def _render_list(title: str, items: list[Any], empty: str = "Nothing found.") -> str:
    if not items:
        return f"{title}\n{empty}"
    lines = [title, f"({len(items)} found)", ""]
    for i, it in enumerate(items, 1):
        if isinstance(it, dict):
            lines.append(f"{i}. {_line(it)}")
        else:
            lines.append(f"{i}. {it}")
    return "\n".join(lines)


def _pretty(obj: Any) -> str:
    """Compact, readable JSON for a single resource, with huge blobs trimmed."""
    def trim(value: Any) -> Any:
        if isinstance(value, str) and len(value) > 600:
            return value[:600] + "... (truncated)"
        if isinstance(value, dict):
            return {k: trim(v) for k, v in value.items()}
        if isinstance(value, list):
            return [trim(v) for v in value[:50]]
        return value

    return json.dumps(trim(obj), indent=2, ensure_ascii=False)


def _parse_json_arg(name: str, raw: str) -> Any:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"'{name}' must be valid JSON. {exc}") from exc


def _open_file(path: str) -> tuple[str, Any, str]:
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"No file at: {p}")
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return p.name, p.open("rb"), ctype


def _save_bytes(save_path: str, content: bytes) -> Path:
    out = Path(save_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(content)
    return out


def _safe(fn):
    """Wrap a tool so library errors come back as clear text, not tracebacks.

    Uses functools.wraps so FastMCP still sees the original signature (it
    introspects the function to build each tool's input schema).
    """
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return fn(*args, **kwargs)
        except (AuthError, APIError, NeedsProject, FileNotFoundError, ValueError) as exc:
            return f"Error: {exc}"
    return wrapper


# ===========================================================================
# Session & authentication
# ===========================================================================
@mcp.tool()
@_safe
def login() -> str:
    """Sign in to QC Database. Opens your web browser so you can log in and pick
    which organization (company workspace) to connect. Do this once; the
    connection is then remembered. You choose the project separately with
    'set_project'."""
    c = client()
    info = run_login(c.store, c.port)
    scope = info.get("scope", "")
    return (
        "Signed in to QC Database. Your connection is pinned to the organization "
        "you selected in the browser.\n"
        f"Granted access: {scope}\n\n"
        "Next: run 'set_project' to choose the project you want to work in."
    )


@mcp.tool()
@_safe
def logout() -> str:
    """Sign out and forget the saved login on this computer."""
    client().store.clear_token()
    return "Signed out. Run 'login' to connect again."


@mcp.tool()
@_safe
def auth_status() -> str:
    """Check whether you are signed in and which project is currently active."""
    c = client()
    token = c.store.get_token()
    proj = c.store.get_active_project()
    lines = []
    if not token:
        lines.append("Signed in: no. Run 'login' to connect.")
    else:
        lines.append("Signed in: yes.")
        if token.get("scope"):
            lines.append(f"Access: {token['scope']}")
    if proj and proj.get("id"):
        lines.append(f"Active project: {proj.get('name', proj['id'])} ({proj['id']})")
    else:
        lines.append("Active project: none set. Run 'set_project'.")
    return "\n".join(lines)


@mcp.tool()
@_safe
def whoami() -> str:
    """Show who the server is signed in as: your user, the organization (tenant)
    your login is pinned to, how you authenticated, and the access (scopes) you
    were granted. A good first call to confirm the connection is healthy."""
    return _pretty(client().get("/api/whoami/"))


# ===========================================================================
# Orientation & discovery
# ===========================================================================
@mcp.tool()
@_safe
def list_tenants() -> str:
    """List the organizations (tenants) your account belongs to. Your login is
    pinned to one of them, chosen at sign-in."""
    data = client().get_all("/api/tenants/")
    return _render_list("Organizations you can access:", data)


@mcp.tool()
@_safe
def list_projects(search: str = "") -> str:
    """List the projects in your connected organization. Optionally filter by a
    search term (name, code, client, etc.)."""
    data = client().get_all("/api/projects/", search=search or None)
    return _render_list("Projects:", data, empty="No projects found.")


@mcp.tool()
@_safe
def set_project(project_id: str = "", name_or_code: str = "") -> str:
    """Choose the project to work in for the rest of this session. Almost every
    other tool uses it, so set it before doing anything else. Give either the
    exact project_id, or a name_or_code to search for (it must match exactly one
    project)."""
    c = client()
    if not project_id and not name_or_code:
        return "Give a project_id, or a name_or_code to search for. Use 'list_projects' to browse."

    chosen: dict[str, Any] | None = None
    if project_id:
        chosen = c.get(f"/api/projects/{project_id}/")
    else:
        matches = c.get_all("/api/projects/", search=name_or_code)
        exact = [
            p for p in matches
            if name_or_code.lower() in (str(p.get("name", "")).lower(), str(p.get("code", "")).lower())
        ]
        pool = exact or matches
        if len(pool) == 0:
            return f"No project matched '{name_or_code}'. Try 'list_projects'."
        if len(pool) > 1:
            return _render_list(
                f"'{name_or_code}' matched several projects - call set_project again "
                "with the exact project_id:",
                pool,
            )
        chosen = pool[0]

    if not chosen or not chosen.get("id"):
        return "Could not load that project."
    record = {"id": chosen["id"], "name": chosen.get("name") or chosen.get("code") or chosen["id"]}
    c.store.set_active_project(record)
    link = chosen.get("web_url")
    msg = f"Active project set to: {record['name']} ({record['id']}).\nAll project tools now use this project."
    if link:
        msg += f"\nLink: {link}"
    return msg


@mcp.tool()
@_safe
def get_active_project() -> str:
    """Show which project is currently active for this session."""
    proj = client().store.get_active_project()
    if not proj or not proj.get("id"):
        return "No project set. Run 'set_project'."
    return f"Active project: {proj.get('name', proj['id'])} ({proj['id']})"


@mcp.tool()
@_safe
def list_project_members() -> str:
    """List the active members of the current project (use their ids when
    assigning notes or reference requests)."""
    pid = require_project()
    data = client().get_all(f"/api/projects/{pid}/users/")
    return _render_list("Project members:", data)


@mcp.tool()
@_safe
def list_lists() -> str:
    """List the project's controlled-vocabulary lists (welders, weld types,
    materials, pipe sizes, equipment, etc.). Use 'list_list_items' to read one."""
    pid = require_project()
    data = client().get_all(f"/api/lists/projects/{pid}/")
    return _render_list("Project lists:", data)


@mcp.tool()
@_safe
def list_list_items(list_id: str) -> str:
    """Read the entries in one controlled-vocabulary list. Each entry includes a
    'pseudo_code' pill token - paste that verbatim into a map item field so the
    value stays linked to the canonical list entry instead of being free text."""
    pid = require_project()
    data = client().get_all(f"/api/lists/projects/{pid}/{list_id}/items/")
    if not data:
        return "This list has no items."
    lines = [f"List items ({len(data)} found):", ""]
    for i, it in enumerate(data, 1):
        name = _name_of(it) if isinstance(it, dict) else str(it)
        lines.append(f"{i}. {name}")
        if isinstance(it, dict):
            if it.get("id"):
                lines.append(f"   id: {it['id']}")
            if it.get("pseudo_code"):
                lines.append(f"   pill: {it['pseudo_code']}")
    return "\n".join(lines)


@mcp.tool()
@_safe
def create_list_item(list_id: str, name: str, status: str = "", data: str = "") -> str:
    """Add an entry to a controlled-vocabulary list (e.g. a new welder or material).
    'name' is required and must not contain a pipe (|). Optionally set a status
    (active/inactive/compliant/non-compliant) and 'data', a JSON object of the
    list's field values."""
    pid = require_project()
    payload: dict[str, Any] = {"name": name}
    if status:
        payload["status"] = status
    if data:
        payload["data"] = _parse_json_arg("data", data)
    result = client().post(f"/api/lists/projects/{pid}/{list_id}/items/create/", json=payload)
    return f"Created list item '{name}'.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def update_list_item(item_id: str, name: str = "", status: str = "", data: str = "") -> str:
    """Update a controlled-vocabulary list entry. Supply any of name, status, and
    'data' (a JSON object merged into the item's existing data - supplied keys
    overwrite, untouched keys are kept). A supplied name must not contain a pipe (|)."""
    pid = require_project()
    payload: dict[str, Any] = {}
    if name:
        payload["name"] = name
    if status:
        payload["status"] = status
    if data:
        payload["data"] = _parse_json_arg("data", data)
    if not payload:
        return "Nothing to update. Supply a name, status, and/or data."
    result = client().patch(f"/api/lists/projects/{pid}/items/{item_id}/", json=payload)
    return f"List item {item_id} updated.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def delete_list_item(item_id: str) -> str:
    """Delete a controlled-vocabulary list entry. It is soft-deleted and stops
    appearing in list reads."""
    pid = require_project()
    client().delete(f"/api/lists/projects/{pid}/items/{item_id}/delete/")
    return f"List item {item_id} deleted."


@mcp.tool()
@_safe
def list_map_item_schemas() -> str:
    """List the map item schemas available in this project. A schema (e.g. 'Weld')
    defines the custom fields a map item carries - you need its id to create one."""
    pid = require_project()
    data = client().get_all(f"/api/projects/{pid}/schemas/map-items/")
    return _render_list("Map item schemas:", data)


@mcp.tool()
@_safe
def list_document_folders() -> str:
    """List the document folders (document types) and their extraction schemas -
    e.g. an 'MTR' folder that extracts heat number and material grade. Use a
    folder id when uploading a document so it is filed and extracted correctly."""
    pid = require_project()
    data = client().get_all(f"/api/projects/{pid}/schemas/documents/")
    return _render_list("Document folders / types:", data)


@mcp.tool()
@_safe
def list_form_schemas() -> str:
    """List the custom inspection-form schemas defined for this project. Use a
    schema's id with 'create_form_submission' to start filling that form out."""
    pid = require_project()
    data = client().get_all(f"/api/projects/{pid}/schemas/forms/")
    return _render_list("Form schemas:", data)


# ===========================================================================
# Jobs (work orders)
# ===========================================================================
@mcp.tool()
@_safe
def list_jobs(status: str = "", search: str = "") -> str:
    """List the jobs (work orders) in the current project. Optionally filter by
    status (draft/active/in_progress/review/completed/cancelled) or a search term.
    Jobs group the test packages that make up the project's scope."""
    pid = require_project()
    data = client().get_all(
        "/api/jobs/", project=pid, status=status or None, search=search or None
    )
    return _render_list("Jobs:", data)


@mcp.tool()
@_safe
def create_job(
    name: str,
    code: str,
    description: str = "",
    status: str = "",
    assigned_to: str = "",
) -> str:
    """Create a job (work order) in the current project. 'name' and 'code' are
    required. Optionally set a description, status, and assign it to a project
    member by their user id."""
    pid = require_project()
    payload: dict[str, Any] = {"project": pid, "name": name, "code": code}
    if description:
        payload["description"] = description
    if status:
        payload["status"] = status
    if assigned_to:
        payload["assigned_to"] = assigned_to
    result = client().post("/api/jobs/", json=payload)
    return f"Created job '{name}' ({code}).\n\n{_pretty(result)}"


# ===========================================================================
# Packages (test packages)
# ===========================================================================
@mcp.tool()
@_safe
def list_packages(job: str = "", status: str = "", package_type: str = "", search: str = "") -> str:
    """List the test packages in the current project. Optionally filter by job id,
    status (draft/open/in_progress/testing/completed/rejected), package_type
    (hydro_test/pneumatic_test/weld_map/nde/turnover/custom), or a search term."""
    pid = require_project()
    data = client().get_all(
        "/api/packages/",
        project=pid,
        job=job or None,
        status=status or None,
        package_type=package_type or None,
        search=search or None,
    )
    return _render_list("Packages:", data)


@mcp.tool()
@_safe
def create_package(
    job: str,
    name: str,
    code: str,
    package_type: str = "",
    description: str = "",
    test_pressure: str = "",
    test_medium: str = "",
    line_spec: str = "",
    assigned_to: str = "",
) -> str:
    """Create a test package under a job in the current project. 'job', 'name' and
    'code' are required. Optionally set package_type (hydro_test/pneumatic_test/
    weld_map/nde/turnover/custom), a description, test parameters (test_pressure,
    test_medium), a governing line_spec id, and an assignee user id."""
    pid = require_project()
    payload: dict[str, Any] = {"project": pid, "job": job, "name": name, "code": code}
    if package_type:
        payload["package_type"] = package_type
    if description:
        payload["description"] = description
    if test_pressure:
        payload["test_pressure"] = test_pressure
    if test_medium:
        payload["test_medium"] = test_medium
    if line_spec:
        payload["line_spec"] = line_spec
    if assigned_to:
        payload["assigned_to"] = assigned_to
    result = client().post("/api/packages/", json=payload)
    return f"Created package '{name}' ({code}).\n\n{_pretty(result)}"


# ===========================================================================
# Line specifications
# ===========================================================================
@mcp.tool()
@_safe
def list_line_specs(status: str = "", search: str = "") -> str:
    """List the line specifications (per-line requirement sets) in the current
    project. Optionally filter by status (active/archived) or a search term."""
    pid = require_project()
    data = client().get_all(
        "/api/line-specs/", project=pid, status=status or None, search=search or None
    )
    return _render_list("Line specifications:", data)


@mcp.tool()
@_safe
def create_line_spec(name: str, requirements: str = "", status: str = "") -> str:
    """Create a line specification in the current project. 'name' is required;
    optionally provide the requirements text and a status (active/archived)."""
    pid = require_project()
    payload: dict[str, Any] = {"project": pid, "name": name}
    if requirements:
        payload["requirements"] = requirements
    if status:
        payload["status"] = status
    result = client().post("/api/line-specs/", json=payload)
    return f"Created line specification '{name}'.\n\n{_pretty(result)}"


# ===========================================================================
# Documents
# ===========================================================================
@mcp.tool()
@_safe
def list_documents(folder_id: str = "", status: str = "", search: str = "") -> str:
    """List uploaded documents in the current project. Optionally filter by a
    document folder id, status (uploading/processing/extracting/ready/error), or
    a search term."""
    pid = require_project()
    data = client().get_all(
        "/api/documents/",
        project=pid,
        folder=folder_id or None,
        status=status or None,
        search=search or None,
    )
    return _render_list("Documents:", data)


@mcp.tool()
@_safe
def upload_document(file_path: str, folder_id: str = "", do_not_extract: bool = False) -> str:
    """Upload a record (MTR, NDE report, certificate, procedure, etc.) to the
    current project. Optionally file it under a document folder id (recommended,
    so the right extraction schema runs). Set do_not_extract=True if you will
    supply the extracted data yourself with 'set_document_extracted_data'."""
    pid = require_project()
    name, fh, ctype = _open_file(file_path)
    try:
        data: dict[str, Any] = {}
        if folder_id:
            data["folder_id"] = folder_id
        if do_not_extract:
            data["do_not_extract"] = "true"
        result = client().upload(
            f"/api/documents/projects/{pid}/upload/",
            files=[("files", (name, fh, ctype))],
            data=data,
        )
    finally:
        fh.close()
    return f"Uploaded '{name}'.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def get_document(document_id: str) -> str:
    """Get one document's details, including its AI-extracted structured data."""
    return _pretty(client().get(f"/api/documents/{document_id}/"))


@mcp.tool()
@_safe
def set_document_extracted_data(document_id: str, extracted_data: str) -> str:
    """Write structured fields back onto a document (used after you run your own
    'bring your own AI' extraction). 'extracted_data' is a JSON object string
    matching the folder's schema."""
    payload = {"extracted_data": _parse_json_arg("extracted_data", extracted_data)}
    result = client().post(f"/api/documents/{document_id}/extracted-data/", json=payload)
    return f"Extracted data saved on document {document_id}.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def upload_document_version(document_id: str, file_path: str, do_not_extract: bool = False) -> str:
    """Upload a new revision of an existing document. The current file and its
    extracted data are archived as a prior version and the new file is swapped in.
    Set do_not_extract=True to skip server-side extraction and supply your own
    data afterwards with 'set_document_extracted_data'."""
    pid = require_project()
    name, fh, ctype = _open_file(file_path)
    try:
        data = {"do_not_extract": "true"} if do_not_extract else {}
        result = client().upload(
            f"/api/documents/projects/{pid}/document/{document_id}/version/",
            files=[("file", (name, fh, ctype))],
            data=data,
        )
    finally:
        fh.close()
    return f"Uploaded new version '{name}' of document {document_id}.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def download_document(document_id: str, save_path: str) -> str:
    """Download a document's original uploaded file to a local path."""
    pid = require_project()
    content = client().download(
        f"/api/documents/projects/{pid}/document/{document_id}/export/"
    )
    out = _save_bytes(save_path, content)
    return f"Saved document {document_id} to: {out} ({len(content)} bytes)."


# ===========================================================================
# Drawings
# ===========================================================================
@mcp.tool()
@_safe
def list_drawings(drawing_type: str = "", status: str = "", search: str = "") -> str:
    """List the drawings in the current project (use a drawing's id when creating
    map items on it). Optionally filter by drawing_type, status, or a search term."""
    pid = require_project()
    data = client().get_all(
        "/api/drawings/",
        project=pid,
        drawing_type=drawing_type or None,
        status=status or None,
        search=search or None,
    )
    return _render_list("Drawings:", data)


@mcp.tool()
@_safe
def upload_drawing(file_path: str, do_not_extract: bool = False) -> str:
    """Upload an isometric drawing (PDF) to the current project. Multi-page PDFs
    are split into one drawing per sheet. Set do_not_extract=True to skip the
    server-side AI extraction."""
    pid = require_project()
    name, fh, ctype = _open_file(file_path)
    try:
        data = {"do_not_extract": "true"} if do_not_extract else {}
        result = client().upload(
            f"/api/drawings/projects/{pid}/upload/",
            files=[("pdf_file", (name, fh, ctype))],
            data=data,
        )
    finally:
        fh.close()
    return f"Uploaded drawing '{name}'.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def upload_large_format_drawing(file_path: str, do_not_extract: bool = False) -> str:
    """Upload a large-format drawing (P&ID, plan, elevation, overview) PDF to the
    current project. Set do_not_extract=True to skip server-side AI extraction."""
    pid = require_project()
    name, fh, ctype = _open_file(file_path)
    try:
        data = {"do_not_extract": "true"} if do_not_extract else {}
        result = client().upload(
            f"/api/drawings/projects/{pid}/large-format/upload/",
            files=[("pdf_file", (name, fh, ctype))],
            data=data,
        )
    finally:
        fh.close()
    return f"Uploaded large-format drawing '{name}'.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def upload_drawing_to_package(package_id: str, file_path: str, do_not_extract: bool = False) -> str:
    """Upload an isometric drawing (PDF) straight into a specific package in the
    current project. Multi-page PDFs are split into one drawing per sheet. Set
    do_not_extract=True to skip server-side AI extraction."""
    pid = require_project()
    name, fh, ctype = _open_file(file_path)
    try:
        data = {"do_not_extract": "true"} if do_not_extract else {}
        result = client().upload(
            f"/api/drawings/projects/{pid}/packages/{package_id}/upload/",
            files=[("pdf_file", (name, fh, ctype))],
            data=data,
        )
    finally:
        fh.close()
    return f"Uploaded drawing '{name}' into package {package_id}.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def upload_drawing_version(drawing_id: str, file_path: str, do_not_extract: bool = False) -> str:
    """Upload a new revision of an existing drawing (a single-page PDF or image).
    The current image and its data are archived as a prior version and the new
    file is swapped in. Set do_not_extract=True to skip server-side extraction."""
    pid = require_project()
    name, fh, ctype = _open_file(file_path)
    try:
        data = {"do_not_extract": "true"} if do_not_extract else {}
        result = client().upload(
            f"/api/drawings/projects/{pid}/drawing/{drawing_id}/version/",
            files=[("file", (name, fh, ctype))],
            data=data,
        )
    finally:
        fh.close()
    return f"Uploaded new version '{name}' of drawing {drawing_id}.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def upload_large_format_drawing_version(lfd_id: str, file_path: str, do_not_extract: bool = False) -> str:
    """Upload a new revision of an existing large-format drawing. The current file
    and its data are archived as a prior version. Set do_not_extract=True to skip
    server-side extraction."""
    pid = require_project()
    name, fh, ctype = _open_file(file_path)
    try:
        data = {"do_not_extract": "true"} if do_not_extract else {}
        result = client().upload(
            f"/api/drawings/projects/{pid}/large-format/{lfd_id}/version/",
            files=[("file", (name, fh, ctype))],
            data=data,
        )
    finally:
        fh.close()
    return f"Uploaded new version '{name}' of large-format drawing {lfd_id}.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def export_drawing(drawing_id: str, save_path: str, variant: str = "clean", schema_id: str = "") -> str:
    """Render a drawing to a PDF and save it locally. variant='clean' (default) is
    the bare drawing; variant='map' overlays its map items - give a schema_id to
    overlay only that schema's items, or omit it for the combined map of all."""
    pid = require_project()
    content = client().download(
        f"/api/drawings/projects/{pid}/drawing/{drawing_id}/export/",
        variant=variant or None,
        schema_id=schema_id or None,
    )
    out = _save_bytes(save_path, content)
    return f"Saved drawing {drawing_id} ({variant}) to: {out} ({len(content)} bytes)."


@mcp.tool()
@_safe
def export_large_format_drawing(lfd_id: str, save_path: str, variant: str = "clean") -> str:
    """Render a large-format drawing to a PDF and save it locally. variant='clean'
    (default) is the bare drawing; variant='flagged' includes the flagged overlay."""
    pid = require_project()
    content = client().download(
        f"/api/drawings/projects/{pid}/large-format/{lfd_id}/export/",
        variant=variant or None,
    )
    out = _save_bytes(save_path, content)
    return f"Saved large-format drawing {lfd_id} ({variant}) to: {out} ({len(content)} bytes)."


# ===========================================================================
# Map items
# ===========================================================================
@mcp.tool()
@_safe
def list_map_items(drawing_id: str = "", schema_id: str = "", status: str = "", search: str = "") -> str:
    """List map items (welds, flanges, fittings...) in the current project.
    Optionally filter by drawing id, schema id, status, or a search term."""
    pid = require_project()
    data = client().get_all(
        "/api/mapping/items/",
        project=pid,
        drawing=drawing_id or None,
        schema=schema_id or None,
        status=status or None,
        search=search or None,
    )
    return _render_list("Map items:", data)


@mcp.tool()
@_safe
def create_map_item(
    drawing_id: str,
    schema_id: str,
    label: str,
    notes: str = "",
    data: str = "",
) -> str:
    """Create a map item (a weld, flange, fitting...) pinned to a drawing. Use a
    schema id from 'list_map_item_schemas'. 'data' is a JSON object of the
    schema's custom fields; for fields that map to a controlled list, put that
    list item's 'pseudo_code' pill (from 'list_list_items') as the value instead
    of free text."""
    pid = require_project()
    payload: dict[str, Any] = {
        "project": pid,
        "drawing": drawing_id,
        "schema": schema_id,
        "label": label,
    }
    if notes:
        payload["notes"] = notes
    if data:
        payload["data"] = _parse_json_arg("data", data)
    result = client().post("/api/mapping/items/", json=payload)
    return f"Created map item '{label}'.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def mark_map_item_complete(item_id: str) -> str:
    """Mark a map item complete. This is a buy-off recorded under YOUR name and
    the current time. Only do this when the user has confirmed the work is done -
    the user is responsible for this sign-off."""
    result = client().post(f"/api/mapping/items/{item_id}/mark-complete/")
    return (
        f"Map item {item_id} marked complete (recorded as your sign-off).\n\n{_pretty(result)}"
    )


@mcp.tool()
@_safe
def mark_map_item_accepted(item_id: str) -> str:
    """Mark a map item accepted (must already be complete). This is a buy-off
    recorded under YOUR name. Only do this with the user's explicit go-ahead -
    the user is accountable for the acceptance."""
    result = client().post(f"/api/mapping/items/{item_id}/mark-accepted/")
    return (
        f"Map item {item_id} marked accepted (recorded as your sign-off).\n\n{_pretty(result)}"
    )


@mcp.tool()
@_safe
def list_repair_codes() -> str:
    """List the repair codes used when adding a repair to a map item: R (Repair),
    C (Cut-out), A (Adjustment), SC (Scope Change), RW (Rework)."""
    data = client().get_all("/api/mapping/items/repair-codes/")
    if not data:
        return "No repair codes found."
    lines = ["Repair codes:", ""]
    for it in data:
        if isinstance(it, dict):
            lines.append(f"  {it.get('code', '?')} - {it.get('label', '')}")
        else:
            lines.append(f"  {it}")
    return "\n".join(lines)


@mcp.tool()
@_safe
def add_map_item_repair(item_id: str, repair_code: str) -> str:
    """Record a repair against a map item (e.g. a weld). repair_code is one of
    R, C, A, SC, RW (see 'list_repair_codes'). The repair is created as a child
    item with a system-derived label like W1.R1; you cannot add a repair to a
    repair."""
    result = client().post(
        f"/api/mapping/items/{item_id}/repair/", json={"repair_code": repair_code}
    )
    return f"Added repair '{repair_code}' to map item {item_id}.\n\n{_pretty(result)}"


# ===========================================================================
# Fillable PDF templates
# ===========================================================================
@mcp.tool()
@_safe
def list_fillable_templates() -> str:
    """List document folders that publish a fillable PDF template (RIR, inspection
    checklist, test report...) you can fill out and file."""
    pid = require_project()
    data = client().get_all(f"/api/documents/projects/{pid}/fillable-templates/")
    return _render_list("Fillable PDF templates:", data)


@mcp.tool()
@_safe
def get_fillable_template(folder_id: str) -> str:
    """Read a fillable template's form fields and its project-resolved autofill
    values, so you know what to fill in before downloading and submitting it."""
    pid = require_project()
    return _pretty(client().get(f"/api/documents/projects/{pid}/fillable-templates/{folder_id}/"))


@mcp.tool()
@_safe
def download_fillable_template(folder_id: str, save_path: str) -> str:
    """Download the blank fillable PDF for a folder to a local path so you can
    fill it in (ideally flatten it) before submitting."""
    pid = require_project()
    content = client().download(
        f"/api/documents/projects/{pid}/fillable-templates/{folder_id}/download/"
    )
    out = _save_bytes(save_path, content)
    return f"Saved blank template to: {out} ({len(content)} bytes)."


@mcp.tool()
@_safe
def submit_fillable_template(
    folder_id: str,
    file_path: str,
    file_name: str = "",
    do_not_extract: bool = False,
) -> str:
    """Submit a filled PDF back into a template's folder as a document. Re-using a
    file_name creates a new version of that document. Set do_not_extract=True if
    you will supply the extracted JSON yourself. Note: filling the PDF stores the
    signed record; to also populate structured fields, follow up with
    'set_document_extracted_data' on the returned document."""
    pid = require_project()
    name, fh, ctype = _open_file(file_path)
    try:
        data: dict[str, Any] = {}
        if file_name:
            data["file_name"] = file_name
        if do_not_extract:
            data["do_not_extract"] = "true"
        result = client().upload(
            f"/api/documents/projects/{pid}/fillable-templates/{folder_id}/submit/",
            files=[("filled_pdf", (name, fh, ctype))],
            data=data,
        )
    finally:
        fh.close()
    return f"Submitted filled PDF '{name}'.\n\n{_pretty(result)}"


# ===========================================================================
# Forms
# ===========================================================================
@mcp.tool()
@_safe
def list_form_submissions(form_schema: str = "", status: str = "") -> str:
    """List inspection-form submissions in the current project. Optionally filter
    by a form schema id or status (draft/completed)."""
    pid = require_project()
    data = client().get_all(
        f"/api/forms/projects/{pid}/submissions/",
        form_schema=form_schema or None,
        status=status or None,
    )
    return _render_list("Form submissions:", data)


@mcp.tool()
@_safe
def create_form_submission(
    form_schema: str,
    title: str = "",
    report_date: str = "",
    data: str = "",
) -> str:
    """Start a new inspection-form submission from a form schema id. 'data' is an
    optional JSON object of initial field values. It is created as a draft; use
    'complete_form_submission' to lock it once finished."""
    pid = require_project()
    payload: dict[str, Any] = {"form_schema": form_schema}
    if title:
        payload["title"] = title
    if report_date:
        payload["report_date"] = report_date
    if data:
        payload["data"] = _parse_json_arg("data", data)
    result = client().post(f"/api/forms/projects/{pid}/submissions/", json=payload)
    return f"Created form submission.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def get_form_submission(submission_id: str) -> str:
    """Get one inspection-form submission, including its current field data and
    status. Use this to read a draft before filling it in further."""
    pid = require_project()
    return _pretty(client().get(f"/api/forms/projects/{pid}/submissions/{submission_id}/"))


@mcp.tool()
@_safe
def update_form_submission(
    submission_id: str,
    title: str = "",
    report_date: str = "",
    data: str = "",
) -> str:
    """Fill in (update) a draft form submission's title, report_date, and/or field
    values. 'data' is a JSON object that replaces the submission's data. Completed
    (locked) submissions cannot be edited."""
    pid = require_project()
    payload: dict[str, Any] = {}
    if title:
        payload["title"] = title
    if report_date:
        payload["report_date"] = report_date
    if data:
        payload["data"] = _parse_json_arg("data", data)
    if not payload:
        return "Nothing to update. Give a title, report_date, and/or data."
    result = client().patch(
        f"/api/forms/projects/{pid}/submissions/{submission_id}/", json=payload
    )
    return f"Form submission {submission_id} updated.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def complete_form_submission(submission_id: str) -> str:
    """Mark a form submission complete. Completed forms are evidence that can
    satisfy reference requests. This locks the submission - only do it when the
    user confirms the form is finished."""
    pid = require_project()
    result = client().post(f"/api/forms/projects/{pid}/submissions/{submission_id}/complete/")
    return f"Form submission {submission_id} completed.\n\n{_pretty(result)}"


# ===========================================================================
# Notes
# ===========================================================================
@mcp.tool()
@_safe
def create_note(
    content: str,
    is_private: bool = False,
    severity: str = "",
    page_url: str = "",
    assigned_to: str = "",
) -> str:
    """Create a note (observation, action item, issue) on the current project.
    Optionally make it private, set a severity, anchor it to a page_url, and
    assign (@mention) it to a project member by their user id."""
    pid = require_project()
    payload: dict[str, Any] = {"content": content, "is_private": is_private}
    if severity:
        payload["severity"] = severity
    if page_url:
        payload["page_url"] = page_url
    if assigned_to:
        payload["assigned_to"] = assigned_to
    result = client().post(f"/api/notes/projects/{pid}/", json=payload)
    return f"Note created.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def list_notes(status: str = "all") -> str:
    """List the public notes feed for the current project. status can be 'all'
    (default), 'open', or 'resolved'."""
    pid = require_project()
    data = client().get_all(f"/api/notes/projects/{pid}/feed/", status=status or None)
    return _render_list("Notes:", data)


@mcp.tool()
@_safe
def resolve_note(note_id: str, resolution: str = "") -> str:
    """Mark a note resolved, with an optional resolution comment."""
    pid = require_project()
    payload = {"resolution": resolution} if resolution else None
    result = client().post(f"/api/notes/projects/{pid}/{note_id}/resolve/", json=payload)
    return f"Note {note_id} resolved.\n\n{_pretty(result)}"


# ===========================================================================
# ITP line items
# ===========================================================================
@mcp.tool()
@_safe
def list_itp_line_items(
    package: str = "",
    completed: Optional[bool] = None,
    accepted: Optional[bool] = None,
    assigned_to: str = "",
) -> str:
    """List ITP (Inspection & Test Plan) line items in the current project -
    the required inspection/test steps. Optionally filter by package id, by
    completed / accepted (true or false), and by an assignee user id."""
    pid = require_project()
    data = client().get_all(
        f"/api/packages/projects/{pid}/itp-line-items/",
        package=package or None,
        completed=completed,
        accepted=accepted,
        assigned_to=assigned_to or None,
    )
    return _render_list("ITP line items:", data)


@mcp.tool()
@_safe
def get_itp_line_item(item_id: str) -> str:
    """Get one ITP line item, including its activity, acceptance criteria, evidence
    requirements, and completed/accepted state."""
    pid = require_project()
    return _pretty(client().get(f"/api/packages/projects/{pid}/itp-line-items/{item_id}/"))


@mcp.tool()
@_safe
def create_itp_line_item(
    package: str,
    activity_description: str = "",
    activity_code: str = "",
    inspection_frequency: str = "",
    acceptance_criteria: str = "",
    governing_standard: str = "",
    assigned_to: str = "",
    due_date: str = "",
    expected_document_type: str = "",
    expected_custom_form: str = "",
    photo_required: Optional[bool] = None,
) -> str:
    """Create an ITP line item under a package (the package id is required). You can
    set the activity, inspection_frequency, acceptance_criteria, governing_standard,
    an assignee and due_date, and the evidence requirements: expected_document_type
    (a document folder id), expected_custom_form (a form schema id), and
    photo_required."""
    pid = require_project()
    payload: dict[str, Any] = {"package": package}
    if activity_description:
        payload["activity_description"] = activity_description
    if activity_code:
        payload["activity_code"] = activity_code
    if inspection_frequency:
        payload["inspection_frequency"] = inspection_frequency
    if acceptance_criteria:
        payload["acceptance_criteria"] = acceptance_criteria
    if governing_standard:
        payload["governing_standard"] = governing_standard
    if assigned_to:
        payload["assigned_to"] = assigned_to
    if due_date:
        payload["due_date"] = due_date
    if expected_document_type:
        payload["expected_document_type"] = expected_document_type
    if expected_custom_form:
        payload["expected_custom_form"] = expected_custom_form
    if photo_required is not None:
        payload["photo_required"] = photo_required
    result = client().post(f"/api/packages/projects/{pid}/itp-line-items/", json=payload)
    return f"Created ITP line item.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def update_itp_line_item(
    item_id: str,
    activity_description: str = "",
    activity_code: str = "",
    inspection_frequency: str = "",
    acceptance_criteria: str = "",
    governing_standard: str = "",
    assigned_to: str = "",
    due_date: str = "",
    expected_document_type: str = "",
    expected_custom_form: str = "",
    photo_required: Optional[bool] = None,
) -> str:
    """Update an ITP line item's editable fields (activity, assignee, due date, and
    the evidence requirements expected_document_type, expected_custom_form, and
    photo_required). Only the fields you supply are changed. This does not mark it
    complete or accepted - use the dedicated buy-off tools for that."""
    pid = require_project()
    payload: dict[str, Any] = {}
    if activity_description:
        payload["activity_description"] = activity_description
    if activity_code:
        payload["activity_code"] = activity_code
    if inspection_frequency:
        payload["inspection_frequency"] = inspection_frequency
    if acceptance_criteria:
        payload["acceptance_criteria"] = acceptance_criteria
    if governing_standard:
        payload["governing_standard"] = governing_standard
    if assigned_to:
        payload["assigned_to"] = assigned_to
    if due_date:
        payload["due_date"] = due_date
    if expected_document_type:
        payload["expected_document_type"] = expected_document_type
    if expected_custom_form:
        payload["expected_custom_form"] = expected_custom_form
    if photo_required is not None:
        payload["photo_required"] = photo_required
    if not payload:
        return "Nothing to update. Supply at least one field to change."
    result = client().patch(
        f"/api/packages/projects/{pid}/itp-line-items/{item_id}/", json=payload
    )
    return f"ITP line item {item_id} updated.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def mark_itp_complete(item_id: str) -> str:
    """Mark an ITP line item complete. This is a buy-off recorded under YOUR name.
    Only do this when the user confirms the activity is done - the user is
    responsible for the sign-off."""
    pid = require_project()
    result = client().post(f"/api/packages/projects/{pid}/itp-line-items/{item_id}/mark-complete/")
    return f"ITP line item {item_id} marked complete (recorded as your sign-off).\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def mark_itp_accepted(item_id: str) -> str:
    """Mark an ITP line item accepted (must already be complete). This is a buy-off
    recorded under YOUR name. Only do this with the user's explicit go-ahead."""
    pid = require_project()
    result = client().post(f"/api/packages/projects/{pid}/itp-line-items/{item_id}/mark-accepted/")
    return f"ITP line item {item_id} marked accepted (recorded as your sign-off).\n\n{_pretty(result)}"


# ===========================================================================
# Photos
# ===========================================================================
@mcp.tool()
@_safe
def list_photos(object_type: str, object_id: str) -> str:
    """List the photos attached to an object. object_type is one of drawing,
    formsubmission, itplineitem, job, listitem, mapitem, package, shipperlineitem."""
    pid = require_project()
    data = client().get_all(f"/api/photos/projects/{pid}/{object_type}/{object_id}/")
    return _render_list(f"Photos on {object_type} {object_id}:", data)


@mcp.tool()
@_safe
def attach_photo(object_type: str, object_id: str, file_path: str, caption: str = "") -> str:
    """Attach a photo to an object. object_type is one of drawing, formsubmission,
    itplineitem, job, listitem, mapitem, package, shipperlineitem (for example
    object_type='mapitem' with that item's id). Optionally add a caption."""
    pid = require_project()
    name, fh, ctype = _open_file(file_path)
    try:
        result = client().upload(
            f"/api/photos/projects/{pid}/{object_type}/{object_id}/upload/",
            files=[("photos", (name, fh, ctype))],
            data={"caption": caption} if caption else None,
        )
    finally:
        fh.close()
    return f"Attached photo '{name}' to {object_type} {object_id}.\n\n{_pretty(result)}"


# ===========================================================================
# References & reference requests - the heart of turnover
# ===========================================================================
@mcp.tool()
@_safe
def list_reference_requests(status: str = "open", assigned_to: str = "") -> str:
    """List reference requests - the tracked 'still-needs-proof' items. By default
    shows OPEN ones, which together are the punch list standing between the team
    and a complete turnover package. Pass status='all' (or fulfilled/cancelled)
    or an assignee user id to filter."""
    pid = require_project()
    st = None if status.lower() == "all" else (status or "open")
    data = client().get_all(
        f"/api/references/projects/{pid}/reference-requests/",
        status=st,
        assigned_to=assigned_to or None,
    )
    return _render_list("Reference requests:", data, empty="No matching reference requests.")


@mcp.tool()
@_safe
def create_reference_request(
    item_type: str,
    item_id: str,
    reference_type: str,
    document_folder: str = "",
    form_schema: str = "",
    assigned_to: str = "",
    description: str = "",
) -> str:
    """Create a reference request (record that a source item still needs proof).
    item_type is map_item, itp_item, or list_item. reference_type is 'document'
    (then give document_folder) or 'custom_form' (then give form_schema).
    Optionally assign it to a project member and add a description."""
    pid = require_project()
    payload: dict[str, Any] = {
        "reference_type": reference_type,
        "item_type": item_type,
        "item_id": item_id,
    }
    if document_folder:
        payload["document_folder"] = document_folder
    if form_schema:
        payload["form_schema"] = form_schema
    if assigned_to:
        payload["assigned_to"] = assigned_to
    if description:
        payload["description"] = description
    result = client().post(
        f"/api/references/projects/{pid}/reference-requests/", json=payload
    )
    return f"Reference request created.\n\n{_pretty(result)}"


@mcp.tool()
@_safe
def list_references(source_type: str, source_id: str) -> str:
    """List the references already attached to a source item. source_type is
    map_item, list_item, or itp_line_item."""
    pid = require_project()
    data = client().get_all(f"/api/references/projects/{pid}/{source_type}/{source_id}/")
    return _render_list(f"References on {source_type} {source_id}:", data)


@mcp.tool()
@_safe
def create_reference(
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    status: str = "",
    note: str = "",
) -> str:
    """Link a source item to its proof - this is how a turnover package gets
    completed. source_type is map_item, list_item, or itp_line_item. target_type
    is 'document' or 'form' (a form submission). Creating a matching reference
    fulfils any open reference request for that item."""
    pid = require_project()
    payload: dict[str, Any] = {"target_type": target_type, "target_id": target_id}
    if status:
        payload["status"] = status
    if note:
        payload["note"] = note
    result = client().post(
        f"/api/references/projects/{pid}/{source_type}/{source_id}/", json=payload
    )
    return (
        f"Reference created: {source_type} {source_id} -> {target_type} {target_id}.\n\n"
        f"{_pretty(result)}"
    )


@mcp.tool()
@_safe
def turnover_report() -> str:
    """Summarize how close the current project is to a complete turnover package:
    open vs. fulfilled reference requests (the 'what's missing' list), and ITP
    line items not yet completed or accepted."""
    pid = require_project()
    c = client()
    open_reqs = c.get_all(f"/api/references/projects/{pid}/reference-requests/", status="open")
    fulfilled = c.get_all(f"/api/references/projects/{pid}/reference-requests/", status="fulfilled")
    itp_incomplete = c.get_all(
        f"/api/packages/projects/{pid}/itp-line-items/", completed=False
    )
    itp_unaccepted = c.get_all(
        f"/api/packages/projects/{pid}/itp-line-items/", accepted=False
    )

    proj = c.store.get_active_project() or {}
    lines = [
        f"Turnover readiness for: {proj.get('name', pid)}",
        "=" * 48,
        f"Open reference requests (evidence still owed): {len(open_reqs)}",
        f"Fulfilled reference requests: {len(fulfilled)}",
        f"ITP line items not yet completed: {len(itp_incomplete)}",
        f"ITP line items not yet accepted: {len(itp_unaccepted)}",
    ]
    if open_reqs:
        lines += ["", "Top open reference requests:"]
        for r in open_reqs[:15]:
            if isinstance(r, dict):
                who = r.get("assigned_to_name") or r.get("assigned_to") or "unassigned"
                lines.append(f"  - {_name_of(r)} (assignee: {who})")
    if not open_reqs and not itp_incomplete and not itp_unaccepted:
        lines += ["", "Nothing outstanding here - this scope looks turnover-ready."]
    else:
        lines += ["", "Use 'list_reference_requests' to see the full punch list and "
                  "'create_reference' to close items out as you provide evidence."]
    return "\n".join(lines)


# ===========================================================================
# Shippers (received material - read only)
# ===========================================================================
@mcp.tool()
@_safe
def list_shippers(search: str = "") -> str:
    """List the shippers (incoming shipments / receiving records) in the current
    project. Optionally filter by a search term."""
    pid = require_project()
    data = client().get_all(f"/api/shippers/projects/{pid}/", search=search or None)
    return _render_list("Shippers:", data)


@mcp.tool()
@_safe
def list_shipper_line_items(shipper_id: str, search: str = "") -> str:
    """List the line items (received materials, with quantities and heat numbers)
    on one shipper. Optionally filter by a search term."""
    pid = require_project()
    data = client().get_all(
        f"/api/shippers/projects/{pid}/{shipper_id}/line-items/", search=search or None
    )
    return _render_list(f"Line items on shipper {shipper_id}:", data)


# ===========================================================================
# QR codes
# ===========================================================================
@mcp.tool()
@_safe
def generate_qr_code(url: str, save_path: str = "") -> str:
    """Generate a QR code (and short URL) for an internal QC Database app path,
    e.g. url='/projects/<id>/'. Only internal app paths are allowed. Returns the
    short URL; if save_path is given, also decodes and saves the QR image there."""
    proj = client().store.get_active_project() or {}
    payload: dict[str, Any] = {"url": url}
    if proj.get("id"):
        payload["project_id"] = proj["id"]
    result = client().post("/api/qr/generate/", json=payload)

    saved = ""
    if save_path and isinstance(result, dict):
        img = result.get("qr_image_base64") or result.get("qr_image")
        if isinstance(img, str) and img:
            import base64

            b64 = img.split(",", 1)[1] if img.startswith("data:") else img
            try:
                out = _save_bytes(save_path, base64.b64decode(b64))
                saved = f"\nSaved QR image to: {out}."
            except (ValueError, TypeError):
                saved = "\n(Could not decode the QR image from the response.)"
    return f"QR code generated.{saved}\n\n{_pretty(result)}"


def run() -> None:
    mcp.run()
