"""Public web pages for the hosted HTTP server (the home page and /health).

Only used in hosted mode. Both routes are unauthenticated on purpose: the home
page tells a visiting human how to connect their MCP client, and /health lets a
load balancer or uptime monitor probe the process. Neither exposes any user
data, token, or configuration secret.

The page mirrors the design of https://mcp.qcdatabase.ai (QC Database's own
hosted instance). Everything - including the logo, embedded as a data URI - is
served from string constants in this module: the server never reads files at
request time (see the filesystem-safety invariant in CLAUDE.md).
"""

from __future__ import annotations

import html

from mcp.server.fastmcp import FastMCP

from . import __version__
from . import hosted

# The QCDatabase MCP logo (100x100 PNG, 1.7 KB), embedded so the page needs no
# separate asset route.
_ICON_B64 = (
    "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"
)

_HOME_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1" />
    <title>QCDatabase MCP — Remote AI Infrastructure</title>
    <meta name="description" content="Connect your AI assistant to QCDatabase. Secure, remote Model Context Protocol (MCP) server for construction quality control data." />
    <meta name="robots" content="index, follow" />
    <meta property="og:title" content="QCDatabase MCP — Remote AI Infrastructure" />
    <meta property="og:description" content="Connect your AI assistant to QCDatabase. Secure, remote Model Context Protocol (MCP) server for construction quality control data." />
    <meta property="og:type" content="website" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="QCDatabase MCP — Remote AI Infrastructure" />
    <meta name="twitter:description" content="Connect your AI assistant to QCDatabase. Secure, remote Model Context Protocol (MCP) server for construction quality control data." />
    <link rel="icon" type="image/png" href="data:image/png;base64,__ICON_B64__" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet" />
    <style>
      :root {
        --bg: hsl(210 20% 98%);
        --fg: hsl(222 47% 11%);
        --muted: hsl(215.4 16.3% 46.9%);
        --border: hsl(214 32% 91%);
        --accent: hsl(24 100% 50%);
        --card: #ffffff;
        --radius: 0.5rem;
        --sans: "Inter", system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
        --mono: "Space Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
      }
      * { box-sizing: border-box; }
      html { scroll-behavior: smooth; }
      body {
        margin: 0;
        font-family: var(--sans);
        color: var(--fg);
        background-color: var(--bg);
        background-image:
          linear-gradient(hsl(214 32% 91% / 0.6) 1px, transparent 1px),
          linear-gradient(90deg, hsl(214 32% 91% / 0.6) 1px, transparent 1px);
        background-size: 44px 44px;
        line-height: 1.6;
        -webkit-font-smoothing: antialiased;
      }
      a { color: inherit; text-decoration: none; }
      .wrap { max-width: 72rem; margin: 0 auto; padding: 0 1.5rem; }

      /* Header */
      header {
        position: sticky; top: 0; z-index: 50;
        background: hsl(210 20% 98% / 0.8);
        backdrop-filter: blur(12px);
        border-bottom: 1px solid var(--border);
      }
      .nav { height: 4rem; display: flex; align-items: center; justify-content: space-between; }
      .brand { display: flex; align-items: center; gap: 0.5rem; font-weight: 700; font-size: 1.125rem; letter-spacing: -0.02em; }
      .brand img { width: 1.75rem; height: 1.75rem; }
      .brand .dim { color: var(--muted); font-weight: 400; }
      .status {
        display: inline-flex; align-items: center; gap: 0.5rem;
        padding: 0.375rem 0.75rem; border-radius: var(--radius);
        background: #fff; border: 1px solid var(--border);
        font-size: 0.875rem; font-weight: 500; box-shadow: 0 1px 2px rgb(0 0 0 / 0.04);
      }
      .dot { width: 0.5rem; height: 0.5rem; border-radius: 999px; background: #eab308; }
      .status.online .dot { background: #10b981; box-shadow: 0 0 8px rgb(16 185 129 / 0.5); }
      .status.offline .dot { background: #f43f5e; }
      .status.checking .dot { animation: pulse 1.4s ease-in-out infinite; }
      @keyframes pulse { 50% { opacity: 0.35; } }

      /* Hero */
      .hero { padding: 6rem 0 5rem; }
      .badge {
        display: inline-flex; align-items: center; gap: 0.5rem;
        padding: 0.375rem 0.75rem; border-radius: 999px;
        background: hsl(215 28% 90% / 0.6); border: 1px solid hsl(215 20% 80% / 0.6);
        color: hsl(222 40% 25%); font-size: 0.75rem; font-weight: 600;
        text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 2rem;
      }
      .badge svg { color: var(--accent); }
      h1 { font-size: clamp(2.25rem, 5vw, 3.75rem); line-height: 1.1; letter-spacing: -0.025em; margin: 0 0 1.25rem; max-width: 46rem; }
      .lede { font-size: 1.25rem; color: var(--muted); max-width: 40rem; margin: 0 0 2.5rem; }
      .btns { display: flex; flex-wrap: wrap; gap: 1rem; }
      .btn {
        display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem;
        padding: 0.875rem 1.5rem; border-radius: var(--radius); font-weight: 500;
        transition: all 0.15s ease; cursor: pointer; border: 1px solid transparent;
      }
      .btn-primary { background: hsl(222 47% 11%); color: #fff; box-shadow: 0 4px 6px rgb(0 0 0 / 0.08); }
      .btn-primary:hover { background: hsl(222 47% 18%); }
      .btn-secondary { background: #fff; color: hsl(222 30% 30%); border-color: var(--border); box-shadow: 0 1px 2px rgb(0 0 0 / 0.04); }
      .btn-secondary:hover { background: hsl(210 20% 96%); }

      /* Sections */
      section h2 { font-size: clamp(1.75rem, 3vw, 2.25rem); letter-spacing: -0.02em; margin: 0 0 1.25rem; }
      .section-pad { padding: 6rem 0; }

      /* Connect (dark) */
      .connect { background: hsl(222 47% 6%); color: hsl(210 40% 96%); border-top: 1px solid hsl(222 30% 18%); border-bottom: 1px solid hsl(222 30% 18%); }
      .connect-grid { display: grid; grid-template-columns: 1fr; gap: 3rem; align-items: center; }
      @media (min-width: 900px) { .connect-grid { grid-template-columns: 1fr 1.1fr; gap: 5rem; } }
      .connect .lede2 { color: hsl(215 20% 65%); font-size: 1.125rem; margin: 0 0 2.5rem; }
      .steps { display: flex; flex-direction: column; gap: 2rem; }
      .step { display: flex; gap: 1rem; }
      .step-n { flex: 0 0 auto; width: 2rem; height: 2rem; border-radius: 999px; background: hsl(222 30% 16%); border: 1px solid hsl(222 25% 26%); display: flex; align-items: center; justify-content: center; font-family: var(--mono); font-size: 0.875rem; color: hsl(215 20% 75%); }
      .step h3 { margin: 0 0 0.375rem; font-size: 1.05rem; color: hsl(210 40% 92%); }
      .step p { margin: 0; color: hsl(215 20% 65%); font-size: 0.9rem; }
      .kbd { font-family: var(--mono); font-size: 0.78rem; background: hsl(222 30% 16%); border: 1px solid hsl(222 25% 26%); padding: 0.1rem 0.4rem; border-radius: 0.25rem; color: hsl(210 30% 80%); }
      .url-card { background: hsl(222 40% 10% / 0.6); padding: 2rem; border-radius: 0.75rem; border: 1px solid hsl(222 30% 18%); box-shadow: 0 20px 40px rgb(0 0 0 / 0.3); }
      .url-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1.5rem; font-size: 0.875rem; }
      .url-head .lbl { display: flex; align-items: center; gap: 0.5rem; color: hsl(215 20% 72%); font-weight: 500; }
      .pill { display: inline-flex; align-items: center; gap: 0.4rem; color: #34d399; font-size: 0.75rem; background: hsl(160 80% 40% / 0.1); border: 1px solid hsl(160 70% 45% / 0.25); padding: 0.25rem 0.6rem; border-radius: 999px; font-weight: 500; }
      .pill .dot { background: #34d399; }
      .url-row { display: flex; align-items: center; gap: 0.75rem; background: hsl(222 45% 4%); color: #f8fafc; padding: 1rem; border-radius: var(--radius); border: 1px solid hsl(222 30% 16%); }
      .url-row code { font-family: var(--mono); font-size: 0.95rem; color: #34d399; flex: 1; overflow-x: auto; white-space: nowrap; }
      .copy-btn { flex: 0 0 auto; background: none; border: none; color: hsl(215 20% 65%); cursor: pointer; padding: 0.4rem; border-radius: 0.25rem; display: inline-flex; }
      .copy-btn:hover { background: hsl(222 30% 16%); color: #fff; }
      .note { margin: 1rem 0 0; font-size: 0.75rem; color: hsl(215 16% 55%); display: flex; gap: 0.5rem; align-items: flex-start; }

      /* Grids of cards */
      .center-head { max-width: 40rem; margin: 0 auto 4rem; text-align: center; }
      .center-head p { color: var(--muted); font-size: 1.125rem; margin: 0; }
      .icon-tile { width: 4rem; height: 4rem; margin: 0 auto 1.5rem; background: hsl(210 20% 95%); border: 1px solid var(--border); border-radius: 1rem; display: flex; align-items: center; justify-content: center; color: hsl(222 30% 30%); }
      .cards { display: grid; grid-template-columns: 1fr; gap: 1.5rem; }
      @media (min-width: 640px) { .cards.two { grid-template-columns: 1fr 1fr; } .cards.three { grid-template-columns: repeat(2, 1fr); } .cards.four { grid-template-columns: repeat(2, 1fr); } }
      @media (min-width: 960px) { .cards.three { grid-template-columns: repeat(3, 1fr); } .cards.four { grid-template-columns: repeat(4, 1fr); } }
      .card { background: var(--card); padding: 1.75rem; border-radius: 0.75rem; border: 1px solid var(--border); box-shadow: 0 1px 2px rgb(0 0 0 / 0.03); transition: border-color 0.15s, box-shadow 0.15s; }
      .card:hover { border-color: hsl(214 25% 82%); box-shadow: 0 4px 12px rgb(0 0 0 / 0.05); }
      .card .cap-icon { width: 2.5rem; height: 2.5rem; border-radius: var(--radius); background: hsl(210 20% 96%); border: 1px solid hsl(214 32% 93%); display: flex; align-items: center; justify-content: center; color: var(--accent); margin-bottom: 1.25rem; }
      .card h3 { margin: 0 0 0.5rem; font-size: 1.05rem; }
      .card p { margin: 0; color: var(--muted); font-size: 0.9rem; }
      .white-section { background: #fff; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }

      /* Limitations */
      .warn { background: hsl(45 90% 96%); border: 1px solid hsl(43 74% 80% / 0.7); padding: 1.5rem; border-radius: 0.75rem; }
      .warn-row { display: flex; gap: 1rem; align-items: flex-start; }
      .warn-icon { flex: 0 0 auto; background: hsl(43 80% 88% / 0.6); padding: 0.5rem; border-radius: var(--radius); color: hsl(30 70% 38%); display: inline-flex; }
      .warn h3 { margin: 0 0 0.375rem; font-size: 1.05rem; color: hsl(30 60% 26%); }
      .warn p { margin: 0; font-size: 0.875rem; color: hsl(30 40% 32%); }

      /* Footer */
      footer { border-top: 1px solid var(--border); background: #fff; padding: 2.5rem 0; margin-top: auto; }
      .foot { display: flex; flex-direction: column; gap: 1rem; align-items: center; justify-content: space-between; }
      @media (min-width: 720px) { .foot { flex-direction: row; } }
      .foot .copy { display: flex; align-items: center; gap: 0.5rem; color: hsl(222 20% 40%); font-size: 0.875rem; font-weight: 500; }
      .foot .tag { color: var(--muted); font-size: 0.875rem; }
      .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
    </style>
  </head>
  <body>
    <header>
      <div class="wrap nav">
        <div class="brand">
          <img src="data:image/png;base64,__ICON_B64__" alt="QCDatabase MCP logo" />
          <span>QCDatabase<span class="dim">MCP</span></span>
        </div>
        <div id="status" class="status checking" role="status" aria-live="polite">
          <span class="dot"></span><span id="status-text">Checking…</span>
        </div>
      </div>
    </header>

    <main>
      <!-- Hero -->
      <section class="hero">
        <div class="wrap">
          <span class="badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>
          Remote Infrastructure
          </span>
          <h1>Connect Claude to your QCDatabase.</h1>
          <p class="lede">A hosted, remote Model Context Protocol (MCP) server enabling AI assistants to securely read projects, quality data, and references from anywhere — including mobile.</p>
          <div class="btns">
            <a class="btn btn-primary" href="#connect">How to connect
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
            </a>
            <a class="btn btn-secondary" href="#capabilities">Explore capabilities</a>
          </div>
        </div>
      </section>

      <!-- How to Connect -->
      <section id="connect" class="connect section-pad">
        <div class="wrap connect-grid">
          <div>
            <h2>How to Connect</h2>
            <p class="lede2">Configure your AI client to use this remote server. The connection uses your existing QCDatabase.AI account via secure OAuth.</p>
            <div class="steps">
              <div class="step">
                <div class="step-n">1</div>
                <div><h3>Open your AI Client</h3><p>In the Claude desktop or mobile app, navigate to <span class="kbd">Settings &gt; Connectors</span>.</p></div>
              </div>
              <div class="step">
                <div class="step-n">2</div>
                <div><h3>Add Custom Connector</h3><p>Select “Add custom connector” and paste the server URL provided here.</p></div>
              </div>
              <div class="step">
                <div class="step-n">3</div>
                <div><h3>Authenticate</h3><p>On your first request, follow the prompt to sign in and authorize the connection for your specific organization.</p></div>
              </div>
            </div>
          </div>
          <div class="url-card">
            <div class="url-head">
              <span class="lbl">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>
                Server URL
              </span>
              <span class="pill"><span class="dot"></span>Ready to connect</span>
            </div>
            <div class="url-row">
              <code id="server-url">__MCP_ENDPOINT__</code>
              <button class="copy-btn" id="copy-btn" aria-label="Copy server URL">
                <svg id="copy-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              </button>
            </div>
            <p class="note">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:0 0 auto;margin-top:2px"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
              Your credentials are never stored on this server. Standard OAuth tokens remain strictly on your local device.
            </p>
          </div>
        </div>
      </section>

      <!-- Security -->
      <section class="white-section section-pad">
        <div class="wrap">
          <div class="center-head">
            <div class="icon-tile">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>
            </div>
            <h2>Enterprise-grade isolation</h2>
            <p>The MCP server acts as a secure, stateless proxy between your AI assistant and the QCDatabase API. Every session is strictly isolated to the authenticated user.</p>
          </div>
          <div class="cards three">
            <div class="card"><h3>Bring Your Own Account</h3><p>Any QCDatabase user can connect using their existing credentials. The AI's session inherits your precise role, permissions, and organization access.</p></div>
            <div class="card"><h3>Standard OAuth</h3><p>Authentication is handled directly by QCDatabase.AI. This intermediate server never sees, intercepts, or stores your passwords.</p></div>
            <div class="card"><h3>Stateless Architecture</h3><p>No data or session tokens are persisted on this infrastructure. Your quality data flows securely in transit from the API directly to your client.</p></div>
          </div>
        </div>
      </section>

      <!-- Capabilities -->
      <section id="capabilities" class="section-pad">
        <div class="wrap">
          <div style="margin-bottom:3.5rem">
            <h2>Capabilities</h2>
            <p style="color:var(--muted);font-size:1.125rem;max-width:40rem;margin:0">Once connected, your AI assistant can seamlessly query your organization's quality control data to assist with analysis, reporting, and reference checks.</p>
          </div>
          <div class="cards four">
            <div class="card">
              <div class="cap-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 18v-2a4 4 0 0 1 4-4h12a4 4 0 0 1 4 4v2"/><path d="M10 6a2 2 0 1 1 4 0v2h-4z"/><path d="M4 12V9a2 2 0 0 1 2-2h1"/><path d="M20 12V9a2 2 0 0 0-2-2h-1"/></svg></div>
              <h3>Active Projects</h3><p>Browse your available projects and securely set an active context for subsequent AI queries.</p>
            </div>
            <div class="card">
              <div class="cap-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg></div>
              <h3>Quality Records</h3><p>Read and analyze quality records, mappings, and inspection data instantly.</p>
            </div>
            <div class="card">
              <div class="cap-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg></div>
              <h3>Reference Lookups</h3><p>Search and retrieve technical references and material specifications on demand.</p>
            </div>
            <div class="card">
              <div class="cap-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="m9 15 2 2 4-4"/></svg></div>
              <h3>Turnover Packages</h3><p>Prepare, read, and summarize turnover information for final engineering handover.</p>
            </div>
          </div>
        </div>
      </section>

      <!-- Limitations -->
      <section class="wrap" style="padding-bottom:6rem">
        <div class="cards two">
          <div class="warn">
            <div class="warn-row">
              <span class="warn-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></span>
              <div><h3>Known Limitation: Document Uploads</h3><p>While most features operate seamlessly via remote connection, uploading new documents and drawings requires local desktop file access. This capability is currently unavailable from mobile clients or remote connections. All other read and analyze operations function as expected.</p></div>
            </div>
          </div>
          <div class="warn">
            <div class="warn-row">
              <span class="warn-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></span>
              <div><h3>Known Limitation: Turnover Package Compiler</h3><p>All turnover data is fully accessible remotely, but compiling a complete turnover package — with generated table of contents, all references, and all attachments bundled together — is web only. Custom turnover tooling can still be built as skills on top of this MCP server.</p></div>
            </div>
          </div>
        </div>
      </section>
    </main>

    <footer>
      <div class="wrap foot">
        <div class="copy">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>
          <span>&copy; <span id="year">2026</span> QCDatabase.AI</span>
        </div>
        <div class="tag">Infrastructure for Construction Quality Control · v__VERSION__</div>
      </div>
    </footer>

    <script>
      // Year
      document.getElementById("year").textContent = new Date().getFullYear();

      // Copy-to-clipboard for the server URL
      (function () {
        var btn = document.getElementById("copy-btn");
        var url = document.getElementById("server-url").textContent.trim();
        var icon = document.getElementById("copy-icon");
        var checkPath = '<polyline points="20 6 9 17 4 12"/>';
        var copyPath = '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>';
        btn.addEventListener("click", function () {
          navigator.clipboard.writeText(url).then(function () {
            icon.innerHTML = checkPath;
            icon.style.color = "#34d399";
            setTimeout(function () { icon.innerHTML = copyPath; icon.style.color = ""; }, 2000);
          });
        });
      })();

      // Live status indicator — this server answering /health means it is up.
      (function () {
        var el = document.getElementById("status");
        var text = document.getElementById("status-text");
        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, 5000);
        fetch("/health", { signal: controller.signal })
          .then(function () { clearTimeout(timer); el.className = "status online"; text.textContent = "Operational"; })
          .catch(function () { el.className = "status offline"; text.textContent = "Unavailable"; });
      })();
    </script>
  </body>
</html>
"""


def home_page_html() -> str:
    """Render the home page for the current configuration."""
    return (
        _HOME_TEMPLATE
        .replace("__MCP_ENDPOINT__", html.escape(hosted.resource_url() + "/mcp"))
        .replace("__VERSION__", html.escape(__version__))
        .replace("__ICON_B64__", _ICON_B64)
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
