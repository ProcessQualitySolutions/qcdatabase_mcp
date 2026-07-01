# QC Database MCP Server

A small program that lets an AI assistant (like Claude) do your everyday
**QC Database** work for you — set the project you're working on, upload records,
read what the AI pulled off a document, and find and close out the items still
needed for a complete, traceable **turnover package**.

It runs **on your own computer**. Your AI assistant talks to it; it talks to
[QCDatabase.AI](https://qcdatabase.ai) on your behalf, using your own login.

Built on the official [Model Context Protocol](https://modelcontextprotocol.io)
Python library and the public
[QC Database MCP specification](https://qcdatabase.ai/mcp_server_spec.md).

---

## What you can ask your assistant to do

Once it's connected, you can talk to it in plain language. For example:

- *"Connect to QC Database."* → signs you in (opens your browser once).
- *"Work on the Riverside Unit 2 project."* → pins that project for the session.
- *"Upload this MTR to the Mill Test Reports folder."*
- *"What's still missing for turnover on this project?"*
- *"Show me the open reference requests assigned to me."*
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
  `list_map_item_schemas`, `list_document_folders`, `list_form_schemas`
- **Controlled-vocabulary lists:** `create_list_item`, `update_list_item`,
  `delete_list_item`
- **Jobs & packages:** `list_jobs`, `create_job`, `list_packages`,
  `create_package`, `list_line_specs`, `create_line_spec`
- **Upload records:** `upload_document`, `upload_document_version`,
  `download_document`, `upload_drawing`, `upload_large_format_drawing`,
  `upload_drawing_to_package`, `upload_drawing_version`,
  `upload_large_format_drawing_version`, `attach_photo`
- **Drawings:** `list_drawings`, `export_drawing`, `export_large_format_drawing`
- **Fillable PDF forms:** `list_fillable_templates`, `get_fillable_template`,
  `download_fillable_template`, `submit_fillable_template`
- **Structured data:** `get_document`, `set_document_extracted_data`,
  `list_documents`, `list_map_items`, `create_map_item`
- **Repairs:** `list_repair_codes`, `add_map_item_repair`
- **Inspection forms & notes:** `list_form_submissions`, `create_form_submission`,
  `get_form_submission`, `update_form_submission`, `complete_form_submission`,
  `create_note`, `list_notes`, `resolve_note`
- **Photos:** `list_photos`, `attach_photo`
- **ITP & sign-offs (your call):** `list_itp_line_items`, `get_itp_line_item`,
  `create_itp_line_item`, `update_itp_line_item`, `mark_map_item_complete`,
  `mark_map_item_accepted`, `mark_itp_complete`, `mark_itp_accepted`
- **Turnover (the important part):** `list_reference_requests`,
  `create_reference_request`, `list_references`, `create_reference`,
  `turnover_report`
- **Receiving:** `list_shippers`, `list_shipper_line_items`
- **Utilities:** `generate_qr_code`

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
pip install -e .          # editable install
python -m qcdatabase_mcp  # run the stdio server directly
```

Layout:

```
src/qcdatabase_mcp/
  __main__.py   # entry point (stdio server)
  server.py     # FastMCP server + all tools
  client.py     # HTTP client: bearer auth, auto-refresh, errors, pagination
  auth.py       # OAuth2 (PKCE + dynamic client registration) login & refresh
  config.py     # local token / project storage
```

Environment variables:

- `QCDB_CALLBACK_PORT` — local OAuth callback port (default `8765`).
- `QCDB_CONFIG_DIR` — override where tokens/settings are stored.

Licensed under the MIT License (see `LICENSE`).
