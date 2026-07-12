# Deploying the hosted QC Database MCP server

Goal: `git pull && sudo systemctl restart qcdatabase-mcp` is a complete deploy.

The server itself listens on loopback and speaks plain HTTP; a reverse proxy in
front of it terminates TLS at your public domain (e.g. `mcp.qcdatabase.ai`).

```
Claude ── https://mcp.qcdatabase.ai ──> nginx/Caddy ── http://127.0.0.1:8000 ──> qcdatabase-mcp --http
```

## 1. Get the code and configure it

```bash
sudo git clone https://github.com/<you>/qcdatabase_mcp /opt/qcdatabase_mcp
cd /opt/qcdatabase_mcp
cp deploy/example.env .env
nano .env    # set QCDB_MCP_RESOURCE_URL to your public URL
```

Sanity-check it starts (Ctrl-C to stop):

```bash
./scripts/run-server.sh
```

The startup banner shows the resource id, MCP endpoint, home page URL, and
transport settings the server resolved — if anything looks wrong, fix `.env`
before going further.

## 2. Run it under systemd

```bash
sudo cp deploy/qcdatabase-mcp.service /etc/systemd/system/
sudo nano /etc/systemd/system/qcdatabase-mcp.service   # set User= and paths
sudo systemctl daemon-reload
sudo systemctl enable --now qcdatabase-mcp
journalctl -u qcdatabase-mcp -f     # watch the log
```

## 3. Put a TLS proxy in front

Pick one:

- **Caddy** (easiest — automatic TLS, correct defaults): `deploy/Caddyfile`
- **nginx**: `deploy/nginx.conf`

> **The #1 cause of a broken deployment:** the proxy not forwarding the
> original `Host` header. nginx's default sends the *upstream* address
> (`127.0.0.1:8000`) instead, and this server's DNS-rebinding protection then
> rejects every authenticated request with **421 Misdirected Request** — so
> OAuth sign-in appears to work, but every tool call fails. The provided
> nginx config includes the required `proxy_set_header Host $host;`.
> Caddy forwards Host correctly out of the box.

## 4. Verify from your laptop

```bash
# Home page (HTML) and health check:
curl -s https://mcp.qcdatabase.ai/health
# -> {"status":"ok","server":"qcdatabase-mcp",...}

# OAuth discovery document MCP clients use to find the sign-in server:
curl -s https://mcp.qcdatabase.ai/.well-known/oauth-protected-resource
# -> {"resource":"https://mcp.qcdatabase.ai/","authorization_servers":["https://qcdatabase.ai/"],...}

# The MCP endpoint must answer 401 (not 404/421/502) with a WWW-Authenticate header:
curl -si -X POST https://mcp.qcdatabase.ai/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -d '{}' | head -5
```

Then connect for real: Claude → **Settings → Connectors → Add custom
connector** → `https://mcp.qcdatabase.ai/mcp` → sign in via OAuth.

## 5. Deploy updates

```bash
cd /opt/qcdatabase_mcp
git pull
sudo systemctl restart qcdatabase-mcp
```

`run-server.sh` reinstalls the package into the venv on every start, so a pull
plus a restart always runs the current checkout (including dependency changes).
Because hosted mode is stateless by default, a restart doesn't invalidate
connected clients; users keep working (a pinned project survives per its TTL —
in-memory pins reset on restart, Redis-backed ones don't).

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Sign-in works, every tool call fails with **421** | Proxy not forwarding `Host` | `proxy_set_header Host $host;` in nginx (see above) |
| Connects, then hangs / times out with no response | Proxy buffering SSE responses | Keep `QCDB_MCP_JSON_RESPONSE=1` (the default), or `proxy_buffering off` |
| "Session not found" / 404 after a restart or behind a load balancer | Stateful sessions | Keep `QCDB_MCP_STATELESS=1` (the default) |
| `QCDB_MCP_RESOURCE_URL must be set...` at startup | Public URL not configured | Set it in `.env` |
| Client can't find the sign-in server | Discovery document unreachable | `curl /.well-known/oauth-protected-resource` through the proxy — it must return 200 |
| **502** from the proxy | Server not running / wrong upstream port | `journalctl -u qcdatabase-mcp`; check `QCDB_MCP_PORT` matches the proxy config |
| Reached under a second hostname → 421 | Host not in the allow-list | Add it to `QCDB_MCP_ALLOWED_HOSTS` |
