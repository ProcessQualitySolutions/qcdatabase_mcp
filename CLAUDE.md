# Contributing to qcdatabase-mcp

An open-source MCP server that connects AI assistants to QCDatabase.AI. It runs
in two modes: **stdio** (local, single user) and **hosted HTTP** (multi-user
OAuth resource server — see `src/qcdatabase_mcp/hosted.py`).

## Filesystem-safety invariant (do not break)

The server must **never modify its own files** and **never leak credentials**.
Concretely, for any change you make here:

- **No tool may read or write the server's own source/installation directory.**
  This prevents self-modification. All local file access goes through
  `_open_file` / `_read_bytes` / `_save_bytes` in `server.py`, which call
  `_guard_local_path`. Route any new local file access through those helpers —
  never call `open()` / `Path.write_*` / `Path.read_*` directly in a tool. A
  module that needs file *content* (e.g. `zipmap.py`) takes bytes from a tool
  rather than reading the disk itself; archives are read in memory, never
  extracted.
- **No tool may read or write the credential store** (the per-user config dir
  from `config.config_dir()`). Tokens must stay out of any repo checkout; the
  store lives in the OS config dir and `store.json` is `.gitignore`d.
- **In hosted mode the server touches no local filesystem at all** — the disk is
  the server's, not the remote user's. `_guard_local_path` enforces this.
- **Never write credentials, tokens, or `store.json` into the repo**, and never
  add them to commits, logs, tests, or fixtures.

If a feature seems to need local file access on the server in hosted mode, it
needs a client-side file channel instead — raise it in review rather than
weakening the guard.

## Working here

- Package: `src/qcdatabase_mcp/`. Entry point + CLI: `__main__.py`.
- Keep stdio behavior unchanged unless the task is explicitly about it.
- Quick check: `python -m py_compile src/qcdatabase_mcp/*.py`.
