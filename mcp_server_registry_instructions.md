# MCP Registry: how this server is registered and how to update it

This server is published in the [official MCP Registry](https://registry.modelcontextprotocol.io)
(the centralized metadata directory MCP clients and marketplaces use to discover
servers) as:

- **Name:** `ai.qcdatabase/mcp`
- **Version:** `0.1.0` (first published 2026-08-28)
- **What's registered:** the hosted remote endpoint only —
  `streamable-http` at `https://mcp.qcdatabase.ai/mcp`. There is no `packages`
  entry because the package is not on PyPI.
- **Metadata source:** [`server.json`](server.json) at the repo root.

Verify the live listing:

```bash
curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=ai.qcdatabase/mcp"
```

## How the namespace is verified (do not break this)

The `ai.qcdatabase` namespace is **domain-verified via a DNS TXT record** on the
apex of `qcdatabase.ai`:

```
qcdatabase.ai. IN TXT "v=MCPv1; k=ed25519; p=<public key>"
```

- **Never delete this TXT record** — it is the proof of ownership for the
  namespace and is required for every future publish.
- The matching **Ed25519 private key** lives outside the repo at
  `~/qcdatabase-mcp-registry-key.pem` (Larry's machine). It is a credential:
  back it up securely and **never commit it** (same rule as tokens and
  `store.json` — see CLAUDE.md).
- If the key is ever lost, generate a new keypair and update the TXT record's
  `p=` value to the new public key (see the
  [authentication guide](https://modelcontextprotocol.io/registry/authentication)
  for the openssl commands).

## Publishing an update

1. Bump `version` in `server.json` (keep it in sync with `pyproject.toml`), and
   edit any other metadata that changed (description, remote URL, etc.).
2. Install the `mcp-publisher` CLI if not present
   ([install instructions](https://modelcontextprotocol.io/registry/quickstart) —
   pre-built binaries on the
   [registry releases page](https://github.com/modelcontextprotocol/registry/releases)).
3. From the repo root (Git Bash):

   ```bash
   mcp-publisher validate   # optional sanity check

   PRIVATE_KEY="$(openssl pkey -in ~/qcdatabase-mcp-registry-key.pem -noout -text | grep -A3 "priv:" | tail -n +2 | tr -d ' :\n')"
   mcp-publisher login dns --domain qcdatabase.ai --private-key "${PRIVATE_KEY}"
   mcp-publisher publish
   ```

4. Verify with the `curl` search above, then commit the `server.json` change.

## If the package is ever published to PyPI

To add a local/stdio install option to the registry entry:

1. Add a hidden ownership marker to `README.md` (the registry checks the PyPI
   package description for it):

   ```markdown
   <!-- mcp-name: ai.qcdatabase/mcp -->
   ```

2. Publish the release to PyPI.
3. Add a `packages` entry to `server.json` alongside `remotes`:

   ```json
   "packages": [
     {
       "registryType": "pypi",
       "identifier": "qcdatabase-mcp",
       "version": "<version>",
       "transport": { "type": "stdio" }
     }
   ]
   ```

4. Publish per the update steps above.

## References

- Registry overview: https://modelcontextprotocol.io/registry/about
- Publish quickstart: https://modelcontextprotocol.io/registry/quickstart
- Authentication (DNS/HTTP/GitHub): https://modelcontextprotocol.io/registry/authentication
- Remote servers: https://modelcontextprotocol.io/registry/remote-servers
- Package types: https://modelcontextprotocol.io/registry/package-types
