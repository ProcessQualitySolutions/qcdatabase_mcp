# Management API handoff

Reference: supplied `QCDatabase_API_1791309159277.yaml`, **API version 3.1.0**,
using **OpenAPI 3.0.3** (the API version is not the OpenAPI dialect).

## Completed in MCP

- `move_drawing_to_package(drawing_id, destination_package_id)` requires an active
  project and UUID identifiers. Reads `GET /api/drawings/{id}/` → `Drawing`
  and `GET /api/packages/{id}/` → `Package`, checks both `project` fields, then
  sends `PATCH /api/drawings/{id}/` with **only** `{"package": "<destination UUID>"}`.
  `PatchedDrawingRequest.package` is writable and nullable; this tool deliberately
  requires an existing destination, not null/removal or a project reassignment.
  The 200 `Drawing` response is rendered in full using the existing visible
  truncation conventions. Already-assigned drawings return the current record
  without PATCH, after checking the destination too. API failures are surfaced.
- `update_drawing` retains its metadata-only interface: drawing number, title,
  line number, sheet number, revision. No upload or duplication is needed to move.
- Map-item schema discovery and record-level creation, edits, and imports remain.
  **Map-item type definitions must never be created, edited, promoted, or otherwise
  mutated by MCP.** No generic arbitrary HTTP/write tool is exposed.

Project membership and tenant constraints remain enforced by the API; the local
checks are additional safeguards, not a replacement for server authorization.
Normal drawing/package reads and drawing writes need their corresponding scopes.
There is no client-side transaction spanning reads and PATCH: backend validation
must continue rejecting foreign related objects at write time.

## Permitted but API-blocked — no tools advertised

**List and document type creation/editing are permitted by the user.** Their
absence is a missing write contract, not a policy prohibition.

| Capability | Evidence / missing contract |
|---|---|
| List types | `/api/projects/{project_id}/schemas/lists/` and `.../lists/{schema_id}/` expose GET only. List-item CRUD changes entries, not list definitions. |
| Document types | `/api/projects/{project_id}/schemas/documents/` and `.../documents/{folder_id}/` expose GET only. Need type/folder-definition writes. |
| Existing document folder reassignment | PATCH `/api/documents/{id}/` prose promises “title, tags, or folder assignment”, but `PatchedDocumentRequest` contains **neither `folder` nor `folder_id`**. Upload folder selection does not establish a move contract. |
| Package tags placed on an LFD | No documented endpoint or field. Tags on a package, document tags, drawing package assignment, and loose metadata are **not** this relationship. |

## Request to the backend owner

Please supply an updated OpenAPI contract plus successful and failing examples:

1. **For each operation:** exact HTTP methods and paths, identifier types,
   tenant/project ownership rules, required scopes and roles, writable fields
   (required/optional/nullability/defaults), response fields/statuses, and
   validation/403/404/conflict behavior. State partial-vs-replacement semantics,
   audit effects, idempotency and concurrent-update behavior.
2. **LFD package tags:** how to read, place, edit, and remove a tag; tag ID,
   LFD ID, package ID, sheet/page and position/geometry fields and units;
   multiplicity, duplicate handling, and package deletion/move effects.
   Confirm these are placements on an LFD, not tags stored on the package.
3. **List/document type creation and editing:** names/codes and uniqueness,
   schema/field-definition payloads and supported field types; tenant-wide vs.
   project-specific definitions; defaults, required fields, validation, and
   compatibility/migration behavior for existing records. For document types,
   specify extraction-schema and template associations. Do not add map-item
   type-write tools as part of this work.
4. **Document moves:** resolve the PATCH prose/schema contradiction; specify
   the actual field or dedicated operation, destination eligibility, null/remove
   support, and response. Explain whether extracted data is preserved, cleared,
   migrated, revalidated, or re-extracted; effects on pending extraction jobs,
   versions, references, history, and templates. Clarify atomicity and any
   required confirmation for destructive extraction changes.

Do not implement these operations by guessing paths or encoding relationships in
metadata. Once contracts are supplied, add focused tools and contract tests.

## Availability

Development tests and tool discovery do not establish production availability.
Republish the server, then reconnect/refresh MCP client tool discovery to load
the new tool. This change does not publish automatically or alter OAuth.
