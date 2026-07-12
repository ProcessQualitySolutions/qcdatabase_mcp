"""Public web pages for the hosted HTTP server (the home page and /health).

Only used in hosted mode. Both routes are unauthenticated on purpose: the home
page tells a visiting human how to connect their MCP client, and /health lets a
load balancer or uptime monitor probe the process. Neither exposes any user
data, token, or configuration secret.

The HTML lives in this module as a string constant - the server never reads
files at request time (see the filesystem-safety invariant in CLAUDE.md).
"""

from __future__ import annotations

import html

from mcp.server.fastmcp import FastMCP

from . import __version__
from . import hosted

_HOME_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QCDatabase MCP &mdash; Remote AI Infrastructure</title>
<style>
  :root {{
    --bg: #0d1117; --panel: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #9198a1; --accent: #4493f8; --ok: #3fb950;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    line-height: 1.6;
  }}
  .wrap {{ max-width: 860px; margin: 0 auto; padding: 0 24px; }}
  header {{
    border-bottom: 1px solid var(--border);
    padding: 20px 0;
  }}
  header .wrap {{ display: flex; align-items: center; justify-content: space-between; }}
  .brand {{ font-size: 18px; font-weight: 700; letter-spacing: .2px; }}
  .brand span {{ color: var(--accent); }}
  .status {{
    display: inline-flex; align-items: center; gap: 8px;
    font-size: 13px; color: var(--muted);
    border: 1px solid var(--border); border-radius: 999px; padding: 4px 12px;
  }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }}
  .dot.ok {{ background: var(--ok); }}
  .hero {{ padding: 64px 0 40px; }}
  .hero h1 {{ font-size: 34px; line-height: 1.25; margin-bottom: 14px; }}
  .hero p {{ color: var(--muted); font-size: 17px; max-width: 640px; }}
  section {{ padding: 28px 0; }}
  h2 {{ font-size: 20px; margin-bottom: 16px; }}
  .card {{
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; padding: 20px 22px; margin-bottom: 14px;
  }}
  ol.steps {{ list-style: none; counter-reset: step; }}
  ol.steps li {{
    counter-increment: step; display: flex; gap: 14px;
    padding: 10px 0; align-items: baseline;
  }}
  ol.steps li::before {{
    content: counter(step); flex: 0 0 26px; height: 26px;
    display: inline-flex; align-items: center; justify-content: center;
    background: var(--accent); color: #fff; border-radius: 50%;
    font-size: 13px; font-weight: 700;
  }}
  code {{
    background: #0a0d12; border: 1px solid var(--border); border-radius: 6px;
    padding: 2px 8px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 14px; color: var(--accent); word-break: break-all;
  }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }}
  .grid .card {{ margin: 0; }}
  .grid h3 {{ font-size: 15px; margin-bottom: 6px; }}
  .grid p {{ color: var(--muted); font-size: 14px; }}
  .note {{ color: var(--muted); font-size: 14px; margin-top: 12px; }}
  footer {{
    border-top: 1px solid var(--border); margin-top: 48px;
    padding: 24px 0 40px; color: var(--muted); font-size: 13px;
  }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <div class="brand">QC<span>Database</span> MCP</div>
    <div class="status" id="status"><span class="dot" id="dot"></span><span id="status-text">Checking&hellip;</span></div>
  </div>
</header>

<main class="wrap">
  <div class="hero">
    <h1>Connect Claude to your QC Database</h1>
    <p>This is a hosted Model Context Protocol server. Point your AI assistant at it
    and it can work inside your QC Database projects &mdash; using your own login,
    with nothing stored on this server.</p>
  </div>

  <section>
    <h2>How to connect</h2>
    <div class="card">
      <ol class="steps">
        <li>In the Claude app, open <strong>Settings &rarr; Connectors &rarr; Add custom connector</strong>.</li>
        <li>Paste the server URL: <code>{endpoint}</code></li>
        <li>Sign in when prompted &mdash; you authenticate directly with your
            <a href="{issuer}">QCDatabase.AI</a> account via OAuth.</li>
      </ol>
      <p class="note">Works with any MCP client that supports remote servers with
      OAuth (Claude, Claude Code, and others). No API key needed.</p>
    </div>
  </section>

  <section>
    <h2>Enterprise-grade isolation</h2>
    <div class="card">
      <p>Every request carries the signed-in user's own OAuth token; the server
      verifies it against QCDatabase.AI and acts strictly as that user. Credentials
      are never written to disk, sessions are keyed per user and tenant, and your
      connection is pinned to the organization you chose at sign-in &mdash; many
      people can share this deployment without ever seeing each other's work.</p>
    </div>
  </section>

  <section>
    <h2>Capabilities</h2>
    <div class="grid">
      <div class="card"><h3>Projects &amp; jobs</h3><p>Browse projects, pin one for your session, and work with jobs, test packages, and line specs.</p></div>
      <div class="card"><h3>Quality records</h3><p>Read documents and AI-extracted data, manage weld maps, inspection forms, notes, and photos.</p></div>
      <div class="card"><h3>Semantic search</h3><p>Meaning-based search across documents, drawings, welds, packages, and more &mdash; ranked by relevance.</p></div>
      <div class="card"><h3>Turnover</h3><p>Track reference requests, build references, and run the turnover report to see what's still missing.</p></div>
    </div>
    <p class="note">File upload/download tools are unavailable on this remote server
    &mdash; the disk here isn't yours. Run the local (stdio) server for those.</p>
  </section>
</main>

<footer>
  <div class="wrap">
    &copy; 2026 <a href="{issuer}">QCDatabase.AI</a> &mdash; construction quality
    control infrastructure. Server v{version}.
  </div>
</footer>

<script>
  fetch('/health').then(function (r) {{ return r.json(); }}).then(function () {{
    document.getElementById('dot').className = 'dot ok';
    document.getElementById('status-text').textContent = 'Online';
  }}).catch(function () {{
    document.getElementById('status-text').textContent = 'Unreachable';
  }});
</script>
</body>
</html>
"""


def home_page_html() -> str:
    """Render the home page for the current configuration."""
    return _HOME_TEMPLATE.format(
        endpoint=html.escape(hosted.resource_url() + "/mcp"),
        issuer=html.escape(hosted.issuer_url()),
        version=html.escape(__version__),
    )


def health_payload() -> dict:
    """What /health reports. Static facts only - no user data, no secrets."""
    return {
        "status": "ok",
        "server": "qcdatabase-mcp",
        "version": __version__,
        "mcp_endpoint": hosted.resource_url() + "/mcp",
        "authorization_server": hosted.issuer_url(),
    }


def register_pages(mcp: FastMCP) -> None:
    """Attach the public routes to the hosted FastMCP app."""
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse

    @mcp.custom_route("/", methods=["GET"])
    async def home(_request: Request) -> HTMLResponse:
        return HTMLResponse(home_page_html())

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(health_payload())
