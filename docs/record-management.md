# Record management through MCP

## Drawing fields

`update_drawing` updates an existing drawing in the active project using
`PATCH /api/drawings/{id}/`. Use `list_drawings` or `get_drawing` to find the ID.

Supported fields: `drawing_number`, `title`, `line_number`, `sheet_number`, and
`revision`. Omitted or null arguments leave fields unchanged. Empty strings clear
drawing number, line number, or sheet number. Blank titles/revisions and values
exceeding the API's documented lengths are rejected before any request.

The tool reads the drawing to verify project ownership before writing. The API
remains responsible for authorization and concurrent changes. No schema/type
editing or package reassignment is exposed.

## Blocked API contracts

Checked the public OpenAPI at
https://qcdatabase.ai/api/schema/?format=json and the current human-readable
https://qcdatabase.ai/mcp_server_spec.md on 2026-09-23.

- **Package tags:** `PatchedPackageRequest` exposes `metadata` but no tag field.
  No package tag endpoint or authoritative tag storage convention was found.
  An arbitrary `metadata.tags` key is not evidence of a supported package tag.
  Needed: documented tag representation, read/add/remove operations, permissions,
  and concurrency behavior that preserves other tags and metadata.
- **Document folder moves:** `PATCH /api/documents/{id}/` describes folder
  assignment, but `PatchedDocumentRequest` declares neither `folder` nor
  `folder_id`, and no dedicated move endpoint is published.
  Needed: supported destination field or endpoint, same-project folder validation,
  and documented effects on extracted data, extraction history, and references.

These two tools are intentionally not registered until their contracts are
confirmed or the upstream API is extended. No live record writes were used to
probe whether undocumented fields happen to work.

Type/schema editing stays excluded. Publishing and authenticated production
verification are separate from local tests.