# Hosted authentication troubleshooting

The hosted MCP server validates the caller's bearer token using QCDatabase
`GET /api/whoami/`. The public API schema describes this endpoint as scope-exempt
and returning a top-level user `id`, `tenant`, and `scopes`. Hosted OAuth token
exchange and refresh belong to the MCP client, not the local `login` flow.

## Failure categories

- Missing or rejected credentials: HTTP 401 with the standard OAuth challenge.
  An upstream 401 is negatively cached briefly; invalid credentials never reach tools.
- Identity-check permission denial: HTTP 403 without an invalid-token challenge.
  Check account access and upstream policy; reconnecting may not help.
- Timeout, rate limit, upstream server error, or malformed identity response:
  HTTP 503 with `Retry-After: 5`, without an invalid-token challenge. No tool
  executes. The failure is not cached as a rejected credential.
- Hosted `login` called with a verified credential: confirms that the caller is
  already authenticated; it does not start another OAuth flow.

Existing successful-verification caching still permits up to 60 seconds before
revocation is detected (configurable). This change does not expand that window.

## Safe diagnostics

`auth_verification` logs distinguish `network_error`, `invalid_json`,
`missing_identity`, `upstream_error` (HTTP status only), and `token_rejected`.
`mcp_auth` reports whether an unauthorized request carried a bearer header.
Never log the token, its hash, authorization code, cookies, or identity payload.
Successful cached verification need not make a new whoami request.

## Production verification still needed

Local HTTP tests cover initialization, tool discovery, identity reads, rejection,
tenant separation, cache recovery, and service-failure responses with synthetic
credentials. They do not prove a real client's token exchange or refresh works.
The reported production incident's exact cause was not established from existing
401-only logs. A published update and an authorized client reproduction are
needed to correlate these new failure categories with upstream events.

Ask the QCDatabase owner for sanitized token-exchange and whoami statuses around
a failing connection, and confirmation of resource/audience handling and refresh
rotation rules. Browser consent success alone does not prove token exchange
succeeded or that the next MCP request included a bearer token. Do not share raw
credentials or authorization callback URLs.
