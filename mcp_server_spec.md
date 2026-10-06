# QC Database MCP Server Specification

**Product:** QCDatabase.AI — a multi-tenant construction quality-control platform
(test packages, ITP line items, weld/map items, drawings, documents, inspection
forms, and the reference/turnover workflow that ties evidence to work).

**Purpose of this document.** It describes the contract an MCP server integrates
against — authentication, the API surface, and the concepts a good server models —
plus the behaviours we recommend so that an AI assistant can do real QC work
safely. It is the specification the reference implementation
([`qcdatabase-mcp`](https://github.com/qcdatabase)) follows, and it is written so
that anyone can build or fork a compatible server.

**On architecture.** Requirement levels use RFC 2119 words (MUST / SHOULD / MAY).
They apply to *observable behaviour and the API contract*, not to how you build
your server. Sections about transport, hosting, storage, and scaling are
explicitly **non-normative** — pick whatever fits your deployment. The goal is an
open, easy-to-self-host integration, not a mandated stack.

---

## 1. The API

- **Base URL:** `https://qcdatabase.ai`
  (`qcdatabase.com` is the legacy product; use the `.ai` domain for all API/MCP
  work.)
- All requests and responses are JSON unless a file transfer is involved
  (`multipart/form-data` for uploads, a binary body for downloads/exports).
- Resource ids are UUIDs.

### 1.1 Discovery surfaces (public, no auth)

An MCP server SHOULD treat these as the source of truth for the live surface
rather than hard-coding assumptions:

| Purpose | URL |
|---|---|
| OpenAPI 3.0 schema (authoritative) | `GET /api/schema/` |
| Swagger UI | `GET /api/docs/` |
| ReDoc | `GET /api/redoc/` |
| OAuth authorization-server metadata (RFC 8414) | `GET /.well-known/oauth-authorization-server` |
| OAuth protected-resource metadata (RFC 9728) | `GET /.well-known/oauth-protected-resource` |

The OpenAPI schema is the exhaustive, versioned contract. This spec summarises it
and calls out the non-obvious semantics; **where they differ, the OpenAPI schema
wins.**

### 1.2 Conventions

- **Pagination.** List endpoints use DRF-style pagination: a `page` query param and
  a `{ "results": [...], "next": <url|null>, ... }` envelope. Some endpoints return
  a plain array or a custom-keyed object; a client SHOULD handle both and SHOULD
  cap how many pages it auto-follows.
- **Errors.** Standard HTTP status codes with a JSON body carrying a human-readable
  message (commonly under `detail`, `error`, `error_description`, or `message`). A
  server SHOULD surface these as clear text to the assistant, not raw tracebacks.
  Notable codes: `400` (bad/foreign id), `401` (token invalid/expired), `403`
  (authenticated but lacks project membership or scope), `404` (not found in the
  pinned tenant).

---

## 2. Authentication & authorization

The API is OAuth 2.0 with **Authorization Code + PKCE** (RFC 7636) and **Dynamic
Client Registration** (RFC 7591). There are no static client secrets; the client
is public and PKCE is mandatory.

### 2.1 Endpoints

| Step | Endpoint |
|---|---|
| Register a client | `POST /oauth/register/` |
| Authorize (user consent) | `GET /oauth/authorize/` |
| Token / refresh | `POST /oauth/token/` |

### 2.2 Flow (reference)

1. **Register** a public client (`token_endpoint_auth_method` public, PKCE
   required), declaring the redirect URI(s), grant types
   (`authorization_code`, `refresh_token`), and requested scope.
2. **Authorize** with `response_type=code`, `code_challenge` (S256), and `state`.
   The user signs in and **chooses the organization (tenant)** to connect. That
   choice **pins the resulting token to one tenant.**
3. **Exchange** the code (with the PKCE `code_verifier`) for an access token and a
   refresh token. Refresh-token rotation MAY be enabled; if a refresh response
   omits a new refresh token, keep the previous one.

A **local (stdio) server** typically drives this with a browser + a loopback
redirect (`http://127.0.0.1:<port>/callback`). A **hosted server** instead lets the
MCP *client* perform the flow and present the token per request — see §8.

### 2.3 Tenant pinning

Every token is bound to exactly one tenant. A server MUST NOT assume it can reach
another organization with the same token; switching tenants requires a new
sign-in. This is a core isolation guarantee and SHOULD be surfaced to the user
(e.g. via a `whoami` capability).

### 2.4 Scopes

Access is scoped per domain, `read` and (where applicable) `write`. A server
SHOULD request **least privilege** — the narrowest set that supports its use case.
The reference "daily-driver" client requests:

```
tenants:read
projects:read projects:write
jobs:read jobs:write
packages:read packages:write
drawings:read drawings:write
mapping:read mapping:write
locks:read locks:write
documents:read documents:write
forms:read forms:write
notes:read notes:write
references:read references:write
photos:read photos:write
linespecs:read linespecs:write
lists:read
shippers:read
manual:read
```

Semantic search over a domain requires that domain's `:read` scope; the user
manual requires `manual:read` (see §6, §7). Scope is enforced server-side; a `403`
means the token's scope (or the user's project membership) is insufficient. The
scope set is fixed at client registration, so **adding a capability that needs a
new scope requires re-registration / re-consent** for existing users.

---

## 3. Concepts an MCP server should model

These are the ideas that make an assistant useful and safe on top of the raw
endpoints.

- **Tenant (organization) pinning** — from the token; see §2.3.
- **Active project (session).** Almost every endpoint is project-scoped. A server
  SHOULD let the user pin a "current project" once and apply it to subsequent
  calls, rather than requiring a project id on every request. Project-scoped tools
  SHOULD refuse to act until a project is chosen. How that session state is stored
  is an implementation choice (see §8.3).
- **Buy-off accountability.** Marking work **complete** or **accepted** (map items,
  ITP line items) is a sign-off recorded under the acting user and timestamp. A
  server MUST treat these as the user's decision: perform them only on explicit
  confirmation and make clear in the result that the user is accountable. Never
  sign off implicitly.
- **Controlled-vocabulary "pills."** List items expose a `pseudo_code` token. When
  a field references a controlled list, a server SHOULD write that pill verbatim
  (rather than free text) so the value stays linked to the canonical list entry.
- **Schema-first map items / source-system import.** Map item schemas
  (`/api/projects/{id}/schemas/map-items/`) define which fields each map item type
  carries. A server SHOULD fetch the relevant schema *before* creating map items,
  so it places onto the correct schema and populates the right fields — and it
  SHOULD present this as the normal workflow to the assistant. This matters most
  when importing from a source system that already holds the geometry and QC
  attributes: CAD/CAE exports, **PCF** piping files (which carry joint type,
  material, and weight/sch for pipe welds), and **.weldb** boilermaker
  replacement-panel files (which carry material, tube wall thickness, and the
  weld's rectangular position). A server SHOULD preserve the source's **geometry
  form**: PCF welds are single **point** welds (one x/y), whereas .weldb welds are
  **rectangular** (a second point marks the opposite corner). Conflating the two —
  placing a rectangular weld as a bare point, or vice versa — is a common,
  avoidable data-quality loss, and fetching the schema up front is what surfaces
  the distinction before any items are placed.
- **Bring-your-own extraction.** Documents/drawings run server-side AI extraction
  by default; callers MAY opt out (`do_not_extract`) and later write structured
  fields back. This lets an assistant supply its own extraction.
- **Semantic vs. exact retrieval.** List endpoints filter on exact fields; the
  search endpoints (§6) rank by meaning. A server SHOULD offer both and choose
  based on whether the user's ask is structured ("open hydro packages") or fuzzy
  ("welds that failed X-ray near line 12").

---

## 4. Capability surface (by domain)

### Reference implementation management boundaries

`move_drawing_to_package` reads drawing and destination package details, verifies
both belong to the active project, and PATCHes only `package` on the drawing.
No-op assignments do not write. `update_drawing` remains metadata-only (number,
title, line number, sheet number, revision).

MCP MUST NOT create, edit, promote, or otherwise mutate map-item type definitions.
Schema discovery and record-level edits/imports using existing schema IDs remain
available. This restriction does **not** prohibit list/document type creation or
editing: those are permitted but lack write contracts in the supplied API 3.1.0
reference. Document folder moves and LFD package-tag placement are also blocked
on contracts, not implemented via metadata or guessed endpoints.
See [Management API handoff](docs/management-api-handoff.md) for evidence,
the document PATCH prose/schema contradiction, and backend requirements.

### Domain overview

The following domains are available; consult the OpenAPI schema for the exhaustive
list of paths, parameters, and payloads. Paths below are representative, not
complete.

- **Orientation:** `whoami`; tenants (`/api/tenants/`); projects
  (`/api/projects/…`, members at `/api/projects/{id}/users/`); per-project schemas
  for map items, document folders, and forms
  (`/api/projects/{id}/schemas/…`).
- **Controlled-vocabulary lists:** `/api/lists/projects/{project_id}/…` — read
  lists and items (each item carrying a `pseudo_code` pill); create/update items,
  and delete one — a **soft** delete that hides the entry from list reads while
  keeping the record (and who removed it) intact.
- **Jobs & packages:** `/api/jobs/…`, `/api/packages/…` — work orders and the test
  packages under them.
- **Line specifications:** `/api/line-specs/…`.
- **Documents:** `/api/documents/…` — upload (optionally filed to a folder whose
  schema drives extraction), read extracted data, upload new versions, download the
  original, and set extracted data. Fillable-PDF templates live under
  `/api/documents/projects/{project_id}/fillable-templates/…`.
- **Drawings:** `/api/drawings/…` — isometric and large-format uploads, versions,
  rendered exports (clean, map-overlay, or flagged variants), and rendered **canvas
  images** (PNG). File downloads prefer a proxy-safe base64 file-envelope endpoint
  (a signed object-storage link cannot be fetched from a hosted server) and fall
  back to the raw-binary export for oversized files. A canvas image reports its
  pixel width/height, and a map item's `x_position`/`y_position` are absolute pixels
  in that same top-left-origin space — so the image can be dropped into HTML with
  items placed at those coordinates with no rescaling.
- **Map items:** `/api/mapping/…` — pins (welds, flanges, fittings) on drawings,
  with custom schema data, completion/acceptance buy-off, and repair records
  (`repair-codes`, `repair`). Fetch the map item **schema first** (see §3) and
  preserve the source's geometry form (point welds from PCF vs. rectangular welds
  from .weldb); this makes the server a strong companion to CAD, PCF, and .weldb
  systems.
- **Zipmap ingest:** `POST /api/mapping/projects/{project_id}/zipmaps/` — one
  transaction that creates a drawing (PNG display layer + PDF), its map items
  across one or more schemas, and optionally the drawing's extracted-data record,
  from a [zipmap](https://github.com/ProcessQualitySolutions/zipmaps) interchange
  document (`.zipmap.json`). No server-side AI runs: the sender's AI produced the
  map. A **`package_id` is required** (a missing one is a `422`), each dataset
  names a server-side **`schema_id`**, and item coordinates are pixels of the
  embedded PNG. `mode=replace` soft-deletes a same-numbered live drawing in that
  package first; `mode=append` (default) always creates a new one. Validation is
  all-or-nothing, returning RFC 6901 pointers per failure. A server SHOULD bind a
  zipmap's local type names to schema ids (`/api/projects/{id}/schemas/map-items/`)
  and confirm the scope package *before* sending the document.
- **Quality-hold locks:** `/api/locks/projects/{project_id}/…` — quality hold /
  witness points on a map item or ITP line item (fit-up, FME, final-closure
  inspections, etc.). A lock holds an item until an authorized inspector clears it,
  so it cannot be turned in with the hold in place. Lock *types* are the named hold
  definitions; *locks* are the holds placed on items (place, unlock — which keeps
  the record — reassign, and delete: a **soft** delete that withdraws the hold and
  frees its type to be re-held, while the lock itself is retained in the audit
  trail — unlocking, not deleting, is what asserts an inspection happened). This
  is a construction quality gate, **not** a
  security/access-control mechanism; treat placing and clearing holds as the user's
  decision (see §7). Lock-*type* management (create/edit/archive, and the
  permission matrix for who may place or clear each type) is permission-sensitive
  project setup: the reference server deliberately does **not** expose it, leaving
  it to the web Project Admin UI, and a server SHOULD redirect such requests there
  rather than automating them.
- **Inspection forms:** `/api/forms/projects/{project_id}/submissions/…` — create,
  fill (draft), and complete (lock) submissions.
- **Notes:** `/api/notes/projects/{project_id}/…` — observations/issues, assignment,
  resolution.
- **ITP line items:** `/api/packages/projects/{project_id}/itp-line-items/…` — the
  required inspection/test steps, their evidence requirements, and complete/accept
  buy-off.
- **Photos:** `/api/photos/projects/{project_id}/{object_type}/{object_id}/…` —
  attach/list photos on drawings, map items, packages, ITP items, etc.
- **References & turnover:** `/api/references/projects/{project_id}/…` — reference
  requests (the "still needs proof" punch list) and references (links from a source
  item to its proof document/form). Creating a matching reference fulfils an open
  request. This is the heart of assembling a complete, traceable turnover package.
- **Shippers (receiving):** `/api/shippers/projects/{project_id}/…` — incoming
  material records and their line items (read-only).
- **Utilities:** QR generation for internal app paths (`/api/qr/generate/`).

---

## 5. Semantic search

Meaning-based retrieval ranked by cosine similarity over embeddings. Every search
endpoint is `GET`, takes the same parameters, and returns the same envelope shape.

**Parameters**

| Param | In | Required | Notes |
|---|---|---|---|
| `q` | query | yes | Natural-language query. |
| `limit` | query | no | Results to return. Default `5`, max `25`. |
| `project_id` | path | yes* | *All except the user manual are project-scoped. |

**Response envelope**

```json
{
  "query": "welds that failed x-ray",
  "count": 2,
  "results": [
    { "...record fields...": "…", "similarity": 0.88 },
    { "...record fields...": "…", "similarity": 0.71 }
  ]
}
```

Each result carries the domain's normal read fields plus a `similarity` score in
`[0, 1]` (1 = most relevant). Results are scoped to the pinned tenant and (for
project endpoints) to a project the user is a member of.

**Endpoints**

| Domain | Path | Scope |
|---|---|---|
| Documents | `GET /api/documents/projects/{project_id}/search/` | `documents:read` |
| Drawings | `GET /api/drawings/projects/{project_id}/search/` | `drawings:read` |
| Large-format drawings | `GET /api/drawings/projects/{project_id}/large-format/search/` | `drawings:read` |
| Jobs | `GET /api/jobs/projects/{project_id}/search/` | `jobs:read` |
| Packages | `GET /api/packages/projects/{project_id}/search/` | `packages:read` |
| List items | `GET /api/lists/projects/{project_id}/search/` | `lists:read` |
| Map items | `GET /api/mapping/projects/{project_id}/search/` | `mapping:read` |
| Form submissions | `GET /api/forms/projects/{project_id}/search/` | `forms:read` |
| Notes | `GET /api/notes/projects/{project_id}/search/` | `notes:read` |
| Shippers | `GET /api/shippers/projects/{project_id}/search/` | `shippers:read` |
| User manual | `GET /api/manual/search/` | `manual:read` (see §6) |

A server MAY expose these as one "search across `<type>`" capability or as
per-domain capabilities; either is fine.

---

## 6. User manual (product help)

Global product documentation — **not** tenant- or project-scoped, so every
authenticated caller sees the same content. It lets an assistant answer "how do
I…?" / "what does X do?" questions about using QC Database itself. Read-only; all
endpoints require `manual:read`.

| Purpose | Endpoint |
|---|---|
| Sections with nested articles (table of contents; articles include full markdown) | `GET /api/manual/` |
| Semantic search over articles | `GET /api/manual/search/` |
| Flat list of every article (full markdown) | `GET /api/manual/subsections/` |

`GET /api/manual/` and `/api/manual/subsections/` also accept `ordering` and
`search` query params. `GET /api/manual/search/` uses `q` (required) and `limit`
(default 5, max 25) and returns the standard search envelope (§5); each article
result includes `title`, `description`, the full markdown `content`, the parent
`section` id, and a `similarity` score.

Recommended behaviour: expose the semantic search as the primary "ask how QC
Database works" capability (return article content so the model can answer), and
optionally the table of contents for browsing. A server SHOULD bound how much
article text it returns at once.

---

## 7. Safety & trust invariants (normative)

These protect the user and their data regardless of how the server is built.

- **Buy-off is the user's.** Complete/accept actions MUST require explicit user
  confirmation and MUST be attributed honestly; never sign off silently (§3).
- **Respect tenant & project scope.** A server MUST NOT attempt to cross the
  token's tenant boundary, and SHOULD keep the user's active project explicit so
  actions can't land in the wrong project by accident.
- **Least privilege.** Request only the scopes the server needs (§2.4).
- **Credential handling.** Tokens MUST be stored outside any code repository /
  working tree (a local server SHOULD use the per-user OS config directory) and
  MUST NOT be logged, embedded in tool output, or committed. A hosted server
  SHOULD NOT persist end-user tokens at all — see §8.
- **No self-modification / bounded filesystem access.** Tools MUST NOT read or
  write the server's **installation** — its own code, its dependencies, the
  interpreter/virtualenv, and (from a source checkout) the repo root — or its
  credential storage, whatever path is supplied. This blocks self-modification
  (a write into a dependency runs on next start just the same) and token
  exfiltration. Local file transfer tools MUST only touch files the user
  explicitly points to, SHOULD refuse to overwrite an existing file (a download
  payload can carry bytes another user uploaded), and SHOULD refuse all local
  filesystem access when the "local" disk is not the user's (i.e. in a hosted
  deployment).
- **Treat ids as untrusted input.** Ids interpolated into API request paths often
  originate from records that echo other users' content (an indirect
  prompt-injection channel). A server MUST NOT let a crafted id re-target a
  request off the intended endpoint: URL-encode path segments or reject ids that
  are empty or contain path separators / traversal sequences.

---

## 8. Transport & hosting (non-normative)

How you run the server is up to you. Two patterns are common; a server MAY
implement either or both.

### 8.1 Local (stdio)

The server is launched by a desktop AI app and speaks MCP over stdio. It serves a
single local user; the OAuth flow uses a browser + loopback redirect (§2.2), and
the token is persisted in the per-user OS config directory. This is the simplest
mode and needs no hosting.

### 8.2 Hosted (Streamable HTTP as an OAuth resource server)

To serve many users over the network, the recommended pattern is to act as an
**OAuth 2.0 resource server**:

- Publish protected-resource metadata (RFC 9728) pointing at QCDatabase.AI as the
  authorization server, so MCP clients can discover where to sign in.
- Accept the user's access token as `Authorization: Bearer …` on each request,
  **verify** it (e.g. by calling `whoami`, which both validates the token and
  identifies the user), and act as that user for the request. Verification SHOULD
  **fail closed** — a response that does not resolve to an identifiable user is
  not a verified token.
- Do not store end-user tokens; identity travels with each request. If
  verification results are cached to spare the API a round-trip per call, key the
  cache on a hash of the token (never the token itself), cache failures briefly
  too (so garbage tokens can't amplify load against the API), and note that the
  positive-cache TTL is also the window in which an upstream-revoked token still
  works.
- Apply the standard Streamable-HTTP protections (validate `Host`/`Origin` to
  prevent DNS rebinding, and only trust loopback `Host`/`Origin` when actually
  bound to loopback) and terminate TLS in front of the server.

**Resource indicator.** Audience binding (RFC 8707) is intentionally left **open**
so forks can host on their own domain without special authorization-server setup.
A deployment MAY tighten this by having the authorization server issue tokens bound
to its MCP URL, but a compatible server MUST NOT *require* it.

### 8.3 Multi-user session state

The only cross-request state a hosted server needs is small, per-user, and
disposable — chiefly the user's **active project** (losing it just means the user
re-selects). This MAY be kept in process memory (fine for a single instance or
sticky-session routing) or in any shared store (e.g. Redis) for multiple replicas.
No particular technology is required. Per-user state MUST be isolated by a
**globally-unique** identity (e.g. tenant id + user id, so two users can never
collide onto one session) and MUST NOT be keyed on the raw token. Token-
verification caches, if any, are a local optimization that need not be shared.

---

## 9. Reference implementation

`qcdatabase-mcp` (this repository) implements the above: stdio + hosted HTTP,
tenant/project pinning, buy-off prompts, controlled-vocabulary pills, semantic
search, the user manual, the filesystem-safety invariants, and a pluggable session
store. It is a reference, not the definition — the API contract in §§1–7 is what
compatibility depends on.

## 10. Versioning

This document tracks the live API but is not itself the contract; the OpenAPI
schema at `/api/schema/` is authoritative and versioned (see its `info.version`).
When the two disagree, follow the schema and update this document.
