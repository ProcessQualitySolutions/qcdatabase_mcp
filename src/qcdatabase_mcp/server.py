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

from . import BASE_URL
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
    "content", "description", "code", "item_name",
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


# ===========================================================================
# Drawings
# ===========================================================================
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
    c = client()
    token = c._headers()  # noqa: SLF001 - reuse the auth header builder
    import httpx

    url = f"{BASE_URL}/api/documents/projects/{pid}/fillable-templates/{folder_id}/download/"
    with httpx.Client(timeout=120.0, follow_redirects=True) as raw:
        resp = raw.get(url, headers=token)
    if not resp.is_success:
        raise APIError(f"Download failed ({resp.status_code}).")
    out = Path(save_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(resp.content)
    return f"Saved blank template to: {out} ({len(resp.content)} bytes)."


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
) -> str:
    """List ITP (Inspection & Test Plan) line items in the current project -
    the required inspection/test steps. Optionally filter by package id, and by
    completed / accepted (true or false)."""
    pid = require_project()
    data = client().get_all(
        f"/api/packages/projects/{pid}/itp-line-items/",
        package=package or None,
        completed=completed,
        accepted=accepted,
    )
    return _render_list("ITP line items:", data)


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
def attach_photo(object_type: str, object_id: str, file_path: str, caption: str = "") -> str:
    """Attach a photo to an object (for example object_type='map_item' with that
    item's id). Optionally add a caption."""
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


def run() -> None:
    mcp.run()
