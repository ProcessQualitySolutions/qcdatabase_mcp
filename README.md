> WARNING! Our apologies, but some of the modifications we made to allow remote hosting of this MCP server have broken the stdio tools. We are working on a fix ASAP. Please check back here later.


# QC Database MCP Server

A small program that lets an AI assistant (like Claude) do your everyday
**QC Database** work for you — set the project you're working on, upload records,
read what the AI pulled off a document, and find and close out the items still
needed for a complete, traceable **turnover package**.

It runs **on your own computer**. Your AI assistant talks to it; it talks to
[QCDatabase.AI](https://qcdatabase.ai) on your behalf, using your own login.

Built on the official [Model Context Protocol](https://modelcontextprotocol.io)
Python library and the QC Database MCP specification — published at
[qcdatabase.ai/mcp_server_spec.md](https://qcdatabase.ai/mcp_server_spec.md) and
kept in this repo as [`mcp_server_spec.md`](mcp_server_spec.md).

---

## What you can ask your assistant to do

Once it's connected, you can talk to it in plain language. For example:

- *"Connect to QC Database."* → signs you in (opens your browser once).
- *"Work on the Riverside Unit 2 project."* → pins that project for the session.
- *"Upload this MTR to the Mill Test Reports folder."*
- *"What's still missing for turnover on this project?"*
- *"Show me the open reference requests assigned to me."*
- *"Find welds that failed X-ray near line 12."* → meaning-based (semantic) search
  across your project data, ranked by relevance.
- *"How do I create a test package in QC Database?"* → answers from the built-in
  QC Database user manual, so you can learn the app without leaving your assistant.
- *"Attach this photo to that weld."*
- *"Mark this inspection complete."* (it will remind you that the sign-off is
  recorded under your name — that's your call, not the AI's).

---

## Before you start

You need **Python 3.10 or newer** on your computer.

- **Windows:** install from the Microsoft Store (search "Python") or from
  [python.org/downloads](https://www.python.org/downloads/). During the
  python.org install, tick **"Add Python to PATH."**
- **Mac:** `brew install python` or get it from
  [python.org/downloads](https://www.python.org/downloads/).

To check it's installed, open a terminal (PowerShell on Windows, Terminal on Mac)
and run:

```
python --version
```

You should see `Python 3.10` or higher. (On some Macs the command is `python3`.)

---

## Install

From a terminal, in this folder:

```
pip install .
```

That's it. This installs the server and a command called `qcdatabase-mcp`.

> If `pip` isn't found, try `python -m pip install .` (or `python3 -m pip install .`).

---

## Install in Claude Desktop

Claude Desktop launches this server for you and shows its tools in your chats.
Set it up once:

### 1. Install the Claude Desktop app

If you don't have it, download it from
[claude.ai/download](https://claude.ai/download) and sign in.

### 2. Open the config file

In Claude Desktop:

1. Open **Settings**
   (Windows: menu **☰ → File → Settings**; Mac: **Claude → Settings**).
2. Go to the **Developer** tab.
3. Click **Edit Config**.

This opens (and, if needed, creates) a file named `claude_desktop_config.json`.
You can also open it directly:

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`

### 3. Add the QC Database server

Put this in the file. If the file already has other servers, just add the
`"qcdatabase"` block inside the existing `"mcpServers"` section.

**Windows:**

```json
{
  "mcpServers": {
    "qcdatabase": {
      "command": "python",
      "args": ["-m", "qcdatabase_mcp"]
    }
  }
}
```

**Mac** (Python is usually `python3` there):

```json
{
  "mcpServers": {
    "qcdatabase": {
      "command": "python3",
      "args": ["-m", "qcdatabase_mcp"]
    }
  }
}
```

Save the file.

> **If Claude can't start it** (you see "python not found" or the server fails),
> use the full path to Python instead of just `"python"`. Find it by running
> `where python` (Windows) or `which python3` (Mac) in a terminal, then paste
> that path as the `"command"`. For example on Windows:
>
> ```json
> {
>   "mcpServers": {
>     "qcdatabase": {
>       "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
>       "args": ["-m", "qcdatabase_mcp"]
>     }
>   }
> }
> ```
>
> (Use double backslashes `\\` in Windows paths inside JSON.)

### 4. Restart Claude Desktop

**Fully quit and reopen** the app (on Windows, also close it from the system
tray; on Mac, **Quit** with Cmd-Q — closing the window isn't enough). The new
server only loads on a fresh start.

### 5. Confirm it loaded

Start a new chat and look for the tools/connector indicator (a slider or
tool icon near the message box). Click it and you should see **qcdatabase** with
its tools listed. If you don't, see [Troubleshooting](#troubleshooting).

Then ask Claude to **"connect to QC Database"** to sign in — see below.

---

## First time you use it

1. **Sign in.** Ask the assistant to *connect to QC Database* (the `login`
   tool). Your web browser opens; log in and **pick which company workspace**
   you want to connect. That choice locks the connection to that one
   organization. You only do this once — it's remembered.

2. **Pick your project.** Ask it to *work on* a project by name, or list your
   projects first. Almost every action happens "inside" a project, so the server
   asks you to choose one before it will do project work. You can switch
   projects any time.

3. **Get to work.** Upload documents and drawings, check what's missing for
   turnover, create and close reference requests, add notes and photos, and so
   on.

---

## What it can do (tool groups)

- **Session:** `login`, `logout`, `auth_status`, `whoami`
- **Find your way around:** `list_tenants`, `list_projects`, `set_project`,
  `get_active_project`, `list_project_members`, `list_lists`, `list_list_items`,
  `list_map_item_schemas`, `get_map_item_schema`, `list_document_folders`,
  `list_form_schemas`
- **Controlled-vocabulary lists:** `create_list_item`, `update_list_item`,
  `delete_list_item` (a soft delete — the entry stops appearing in list reads but
  stays on record)
- **Jobs & packages:** `list_jobs`, `create_job`, `list_packages`,
  `create_package`, `list_line_specs`, `create_line_spec`
- **Upload records:** `upload_document`, `upload_document_version`,
  `download_document`, `upload_drawing`, `upload_large_format_drawing`,
  `upload_drawing_to_package`, `upload_drawing_version`,
  `upload_large_format_drawing_version`, `attach_photo`
- **Drawings:** `list_drawings`, `get_drawing` (incl. pixel width/height — the
  HTML5-canvas coordinate space for map items), `export_drawing`,
  `export_large_format_drawing`
- **Fillable PDF forms:** `list_fillable_templates`, `get_fillable_template`,
  `download_fillable_template`, `submit_fillable_template`
- **Structured data:** `get_document`, `set_document_extracted_data`,
  `list_documents`, `list_map_items`, `get_map_item`, `create_map_item`
  (place welds/flanges by pixel coordinates), `bulk_create_map_items`,
  `bulk_update_map_items` (batch up to 500 items on one drawing/schema). **Always
  call `list_map_item_schemas` first** — see the CAD/PCF/.weldb note below
- **Zipmaps (a whole mapped drawing in one upload):** `inspect_zipmap`,
  `upload_zipmap` — see [Zipmaps](#zipmaps-a-whole-mapped-drawing-in-one-upload)
- **Repairs:** `list_repair_codes`, `add_map_item_repair`
- **Inspection forms & notes:** `list_form_submissions`, `create_form_submission`,
  `get_form_submission`, `update_form_submission`, `complete_form_submission`,
  `create_note`, `list_notes`, `resolve_note`
- **Photos:** `list_photos`, `attach_photo`
- **ITP & sign-offs (your call):** `list_itp_line_items`, `get_itp_line_item`,
  `create_itp_line_item`, `update_itp_line_item`, `mark_map_item_complete`,
  `mark_map_item_accepted`, `mark_itp_complete`, `mark_itp_accepted`
- **Quality-hold locks (witness/hold points — the user's call):** `list_lock_types`
  (read only), `list_locks`, `get_lock`, `add_lock` (place a hold point — fit-up,
  tack-up, weld-cleanliness, FME, final-closure, boiler-tube FME sponge-in/sponge-out,
  etc. — on a map item or ITP line item so it can't be turned in until inspected),
  `unlock_lock`, `assign_lock`, `delete_lock` (withdraws a hold placed in error — a
  soft delete: kept in the audit trail, no longer holding the item; use `unlock_lock`
  for a hold that was actually satisfied). A lock is a construction quality gate,
  **not** a security control; place or clear one only at the explicit request of its
  owner/author or an authorized inspector. **Creating or editing lock *types*** (the
  named hold definitions, and who may place/clear each) is intentionally **not**
  exposed here — that permission-sensitive setup is done in the web app's Project
  Admin, and the assistant will point you there
- **Turnover (the important part):** `list_reference_requests`,
  `create_reference_request`, `list_references`, `create_reference`,
  `turnover_report`
- **Receiving:** `list_shippers`, `list_shipper_line_items`
- **Semantic search (meaning-based, ranked):** `semantic_search` — natural-language
  search across the project's documents, drawings, large-format drawings, jobs,
  packages, list items, map items, form submissions, notes, and shippers
- **How QC Database works:** `search_user_manual` (ask "how do I…?" questions and
  read the product help articles), `list_user_manual` (browse the help contents)
- **Utilities:** `generate_qr_code`

---

## A companion to CAD, PCF, and .weldb systems

This server is a natural partner to the systems that already describe your
geometry — CAD/CAE exports, **PCF** piping files, and **.weldb** boilermaker
replacement-panel files. Those sources carry most of what a good map item needs,
so the assistant can turn them into accurate weld maps — **if** it follows two
habits:

1. **Fetch the schema first.** Always call `list_map_item_schemas` *before*
   `create_map_item` / `bulk_create_map_items`. That picks the right schema and
   reveals its exact fields, so source values land where they belong instead of
   being guessed or free-texted:
   - **PCF** (piping) files carry most of what a pipe weld needs — **joint type,
     material, weight/sch**.
   - **.weldb** files carry **material, tube wall thickness**, and the weld's
     **rectangular position** on the drawing — enough to build a weld map
     automatically.
2. **Match the source's geometry — point vs. rectangular welds.** This is the big
   data-quality lever. A **PCF** pipe weld is a single **point** weld (one x/y). A
   **.weldb** weld has a **rectangular** position — give the second point
   (`x_position_2`/`y_position_2`) as the opposite corner so the extent is
   preserved. Collapsing a rectangular weld to a bare point (or spreading a point
   weld into a box) silently corrupts the map. Fetching the schema up front is
   what makes this distinction obvious before any items are placed.

---

## Zipmaps: a whole mapped drawing in one upload

A **[zipmap](https://github.com/ProcessQualitySolutions/zipmaps)** is a plain zip
that packages **one drawing** (a PNG, optionally the source single-page PDF)
together with **every map item already placed on it** and the JSON Schemas that
describe those item types. It is the "bring your own AI" hand-off: your model,
CAD export, or takeoff tool produces the map; QC Database ingests it.

`upload_zipmap` replaces the old three-step dance (upload the drawing → wait →
bulk-create items against it) with **one transactional request**. The drawing, all
of its map items across every schema, and its extracted data are created together
— or nothing is. No server-side AI runs on it; the map is yours.

Ask for it in plain language: *"upload this zipmap into the Unit 2 hydro
package."* Behind that, the assistant does three things first:

1. **`inspect_zipmap`** — looks inside without uploading: the drawing size, how
   many items of each type, the data fields those items use, and which types are
   **not yet bound** to a QC Database map item schema.
2. **`list_map_item_schemas` / `get_map_item_schema`** — a zipmap names its types
   locally (`weld`, `heat`); QC Database identifies schemas by id. Any unbound type
   must be paired with the schema it belongs on (`schema_ids={"weld": "<id>"}`),
   and comparing the type's fields against the schema's fields is how that match is
   confirmed. Producers can skip this by writing the id into the archive's
   `schemata/<type>.schema.json` up front.
3. **`list_packages`** — the new drawing has to be filed somewhere, and the API
   **requires** a package id. Pick (or `create_package`) the scope package first.

Coordinates travel as **pixels of the zipmap's PNG** (top-left origin, y down) —
exactly the space QC Database maps in — so nothing is rescaled or flipped. If the
project uses per-schema auto-numbering, its labels win over the zipmap's item ids
and the result reports each new item against the id it came from.

Re-sending a corrected map? `mode="replace"` first **soft-deletes** any live
drawing in that package with the same drawing number (retained in the audit trail,
gone from drawing lists, and its map items go with it) instead of leaving a
duplicate. The default, `mode="append"`, always creates a new drawing.

Everything that can be checked locally is checked **before** the upload — a
missing PNG, a type with no schema id, a coordinate outside the drawing, PDF-space
coordinates that would misplace every item — so a bad map fails in a second with a
list of what to fix, not after a multi-megabyte round trip.

> Zipmaps are read from your own computer, so this works with the local (stdio)
> server. A hosted server never touches your disk (see
> [Filesystem safety](#filesystem-safety-design-invariant)).

---

## A note on sign-offs

Marking work **complete** or **accepted** is a buy-off — the system records it
under **your** name and the current time. The assistant *can* do these for you,
but every one of those tools tells you plainly that **you are responsible for the
action**. The server never signs anything off silently. Treat those as your
decisions, made with your go-ahead.

---

## Privacy & safety

- The server runs locally and only talks to `https://qcdatabase.ai`.
- Your login is **pinned to one organization** (chosen at sign-in) and your
  session is **pinned to one project** — so the assistant can't reach into a
  different workspace or project by accident.
- The server only requests the permissions an everyday user needs; admin
  actions are out of scope by design.
- You can disconnect any connected app at any time from your QC Database account.

Your login is stored on your own computer in a per-user folder:

- **Windows:** `%LOCALAPPDATA%\qcdatabase-mcp\store.json`
- **Mac:** `~/Library/Application Support/qcdatabase-mcp/store.json`
- **Linux:** `~/.config/qcdatabase-mcp/store.json`

Run the `logout` tool (or delete that file) to forget the login.

### Filesystem safety (design invariant)

This is a hard rule the server must always uphold — for its own safety and for
anyone contributing to this open-source repo:

- **The server never modifies its own files.** No tool can read from or write to
  the server's whole installation — its own source, its dependencies
  (`site-packages`), the virtualenv, or (from a checkout) the repo root — whatever
  path it is given, so the assistant can never edit the server's or a
  dependency's code (no self-modification). Downloads also refuse to **overwrite
  an existing file**; choose a new path.
- **The server never touches its credential store beyond the token flow.** No
  tool can read or write the per-user config directory, so a token can never be
  exfiltrated or overwritten through a tool, and it can never be committed to the
  repo (it lives outside any checkout; `store.json` is also `.gitignore`d).
- **The only local files a tool ever touches are the user's own documents**, and
  only in **stdio** mode — the upload tools read a file you point them at, and the
  download/export tools write to a path you choose. In **hosted** mode the server
  refuses all local filesystem access, because the disk is the server's, not
  yours (uploads/downloads there would need a client-side file channel instead).

These rules are enforced in code by `_guard_local_path` in `server.py`.

---

## Troubleshooting

- **"qcdatabase" doesn't show up in Claude Desktop after restarting.**
  1. Make sure you **fully quit** the app (system tray on Windows, Cmd-Q on Mac)
     and reopened it.
  2. Check the config file is valid JSON — a missing comma or brace stops every
     server from loading. Paste it into a JSON checker if unsure.
  3. If it still doesn't load, set `"command"` to the full path to Python (see
     the install note above).
  4. Look at the Claude Desktop logs for an error: Windows
     `%APPDATA%\Claude\logs\`, Mac `~/Library/Logs/Claude/`.
- **"Not logged in."** Ask the assistant to *connect to QC Database* and finish
  the sign-in in your browser.
- **"No project is set."** Ask it to *work on* a project, or to *list projects*.
- **A newer feature says "Access denied (403)" (e.g. the user manual, or the
  quality-hold locks).** If you first signed in with an older version, your saved
  app registration predates that feature's permission. Delete your `store.json`
  (see **Privacy & safety** for its location) and sign in again — that
  re-registers the app with the current permissions.
- **The browser didn't open during login.** The assistant will show you a link —
  open it manually to finish, then sign in again.
- **The sign-in page can't connect / port already in use.** The login uses a
  local web address on port `8765`. If another program is using it, set a
  different free port before launching the app by adding an environment variable
  `QCDB_CALLBACK_PORT` (for example `8766`), then sign in again.
- **It can't find `python`.** Reinstall Python with "Add to PATH" ticked
  (Windows), or use `python3` in the config.

---

## For developers

```
pip install -e .            # editable install
python -m qcdatabase_mcp    # run the stdio server directly

pip install -e '.[dev]'     # editable install + test deps
pytest                      # run the test suite
```

Layout:

```
src/qcdatabase_mcp/
  __main__.py   # entry point + CLI (chooses stdio vs. hosted HTTP)
  server.py     # FastMCP server + all tools + local filesystem guard
  hosted.py     # multi-user hosting: OAuth resource server, token verify, sessions
  client.py     # HTTP client: bearer auth, auto-refresh, errors, pagination, path safety
  auth.py       # OAuth2 (PKCE + dynamic client registration) login & refresh (stdio)
  config.py     # local token / project storage (stdio)
tests/
  test_security.py  # guard, token verifier, path safety, pagination, refresh, ...
mcp_server_spec.md  # the API + behaviour spec this server implements
CLAUDE.md           # contributor invariants (filesystem safety, credential handling)
```

Environment variables (stdio mode):

- `QCDB_CALLBACK_PORT` — local OAuth callback port (default `8765`).
- `QCDB_CONFIG_DIR` — override where tokens/settings are stored.

### Hosting it for many users (`mcp.qcdatabase.ai`)

By default the server speaks MCP over **stdio** — one local user, sign-in via the
`login` tool. Pass `--http` to run the **multi-user hosted** server instead, which
serves the **Streamable HTTP** transport at `/mcp`, plus a public **home page**
at `/` (connection instructions for humans) and a **health check** at `/health`
(for load balancers and uptime monitors).

**Try it on your own machine** (creates a `.venv`, installs, runs on
`http://127.0.0.1:8000`):

```
./scripts/run-local.sh        # Mac/Linux
.\scripts\run-local.ps1       # Windows (PowerShell)
scripts\run-local.bat         # Windows (double-clickable / cmd)
```

Then open <http://127.0.0.1:8000/> in a browser.

**Deploy it on a server** so that `git pull` + restart is a complete deploy —
the full walkthrough (systemd unit, nginx/Caddy configs, verification
checklist, troubleshooting) is in [`deploy/README.md`](deploy/README.md):

```
cp deploy/example.env .env    # set QCDB_MCP_RESOURCE_URL=https://mcp.example.com
./scripts/run-server.sh       # binds 127.0.0.1:8000; put a TLS proxy in front
```

> **Behind nginx you MUST forward the original Host header**
> (`proxy_set_header Host $host;`) — the provided `deploy/nginx.conf` does.
> Without it, the DNS-rebinding protection rejects every authenticated request
> with `421`, which looks like "OAuth works but no tool call ever does". Caddy
> (`deploy/Caddyfile`) does the right thing by default.

In hosted mode the server is an **OAuth 2.0 resource server** (per the MCP
authorization spec) — there are no shared secrets and nothing is stored on disk:

- Each MCP client signs its **own user** in against QCDatabase.AI (discovered from
  the `/.well-known/oauth-protected-resource` document this server publishes) and
  sends that user's access token as `Authorization: Bearer …` on every request.
- The server **verifies** the token against `/api/whoami/`, identifies the user,
  and acts as them. Unauthenticated requests get `401` with a `WWW-Authenticate`
  header pointing clients at the authorization server, so sign-in is automatic.
- **Per-user session state** (the active project) is kept in memory keyed by a
  globally-unique identity (tenant + user id, never the raw token), so many people
  share one deployment without ever seeing each other's work. Verification fails
  closed — a token that doesn't resolve to a user is rejected. (State resets on
  restart — users just re-run `set_project`.)
- **DNS-rebinding protection** validates the `Host`/`Origin` of every request
  (forged `Host` → `421`, forged `Origin` → `403`).

The `login` / `logout` tools become no-ops in hosted mode (sign-in is the client's
job); everything else works identically to stdio.

Hosted-mode configuration (flags override env):

- `QCDB_MCP_HTTP=1` — same as `--http`.
- `--host` / `QCDB_MCP_HOST`, `--port` / `QCDB_MCP_PORT` — bind (default
  `127.0.0.1:8000`).
- `--resource-url` / `QCDB_MCP_RESOURCE_URL` — this server's public URL and OAuth
  resource id; **required** when binding a non-loopback host.
- `--issuer-url` / `QCDB_MCP_ISSUER_URL` — the OAuth authorization server (default
  `https://qcdatabase.ai`).
- `QCDB_MCP_ALLOWED_HOSTS` / `QCDB_MCP_ALLOWED_ORIGINS` — extra allow-list entries
  (comma-separated), e.g. when fronted by additional hostnames.
- `QCDB_MCP_TOKEN_CACHE_TTL` — seconds a verified token is trusted before
  re-checking `/api/whoami/` (default `60`). This is also the window in which a
  token revoked upstream keeps working here; lower it to shrink that window.
- `QCDB_MCP_TOKEN_NEG_CACHE_TTL` — seconds a *failed* verification is remembered
  (default `5`), which blunts garbage-token amplification against the API.
- `QCDB_MCP_STATELESS` — serve each request without a server-held MCP session
  (default `1`). Leave on: stateful sessions break on restarts, redeploys, and
  multi-replica setups ("session not found"). Set `0` only if you need
  server-initiated messages within a session.
- `QCDB_MCP_JSON_RESPONSE` — answer POSTs with plain JSON instead of an SSE
  stream (default `1`). Leave on: buffering proxies (nginx's default) stall SSE
  and the client times out; none of this server's tools stream partial results.

**TLS.** Terminate TLS at a reverse proxy in front of the server (never expose
plain HTTP publicly) and forward the real `Host` header.

**Resource indicator (kept open).** This server accepts any valid QCDatabase-
issued token and takes its resource id from `QCDB_MCP_RESOURCE_URL`, so anyone can
fork it and host on their own domain without special AS configuration. For strict
RFC 8707 audience binding, an operator *may* configure their authorization server
to issue tokens bound to their MCP URL — but it is not required.

**Scaling across replicas.** The only shared state is each user's pinned project —
small, non-secret, and disposable (lost pins just mean re-running `set_project`).
Pick whichever fits your deployment:

- **Single instance (default).** In-memory store, nothing to run. Best for most
  self-hosters.
- **Sticky sessions.** Several replicas behind a load balancer that routes each
  user to the same replica; still in-memory, but a replica restart drops its
  users' pins.
- **Shared store (Redis).** Set `QCDB_MCP_REDIS_URL` and install the extra
  (`pip install 'qcdatabase-mcp[redis]'`). All replicas share state and it
  survives restarts. Pins expire after `QCDB_MCP_SESSION_TTL` seconds idle
  (default 8h). To plug in a different backend, implement the two-method store in
  `hosted.py` (`get_active_project` / `set_active_project`) and return it from
  `build_session_store`.

(The token-verification cache is intentionally per-replica — each rebuilds it from
`/api/whoami/`, so it needs no sharing.)

Licensed under the MIT License (see `LICENSE`).
