"""Thin HTTP client around the QC Database public API.

Handles the things every tool would otherwise repeat: attaching the bearer
token, refreshing it once on a 401, turning the documented 400/403/404 errors
into clear messages, and following pagination so a tool can just get a full list
back.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any, BinaryIO
from urllib.parse import parse_qsl, urlsplit

import httpx

from . import BASE_URL
from .auth import DEFAULT_CALLBACK_PORT, AuthError, ensure_access_token, refresh
from .config import Store


class APIError(RuntimeError):
    """A request reached the server but came back with an error status."""


# Ids are f-string-interpolated into request paths throughout the tool layer, and
# some of those ids echo other project members' content (an indirect prompt-
# injection channel). A well-formed API path is a run of url-safe segments; this
# guard rejects anything else (path traversal, an injected query/fragment, or an
# empty segment from a missing id like ``/api/documents//``) before it is sent -
# defense in depth so a crafted id can never re-target the request off the tool
# surface. Query parameters travel via ``params=``, never in the path string.
_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+")


def _check_api_path(path: str) -> None:
    if not isinstance(path, str) or not path.startswith("/"):
        raise APIError(f"Refused to send a request to a malformed path: {path!r}")
    if any(c in path for c in "?#\\") or " " in path:
        raise APIError(f"Refused a request path with a query, fragment, or space: {path!r}")
    segments = path.split("/")[1:]  # drop the leading empty from the leading '/'
    if segments and segments[-1] == "":
        segments = segments[:-1]  # a single trailing slash is fine
    for seg in segments:
        if seg in ("", ".", "..") or not _SAFE_SEGMENT.fullmatch(seg):
            raise APIError(
                f"Refused an unsafe or empty segment in request path {path!r} "
                "(a required id may be missing or malformed)."
            )


def _rewind_files(files: Any) -> None:
    """Seek every upload file handle back to the start before a retry.

    httpx does not rewind file objects between sends, so a retried multipart
    upload would otherwise transmit zero bytes.
    """
    if not files:
        return
    for item in files:
        value = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item
        fileobj = value[1] if isinstance(value, (list, tuple)) and len(value) > 1 else value
        try:
            fileobj.seek(0)
        except (AttributeError, OSError, ValueError):
            pass  # not a seekable stream; nothing we can do


def callback_port() -> int:
    raw = os.environ.get("QCDB_CALLBACK_PORT")
    if not raw:
        return DEFAULT_CALLBACK_PORT
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_CALLBACK_PORT


# Cap on how many pages we will auto-follow, so a huge project can never hang a
# tool call indefinitely. On reaching it, get_all sets `last_truncated` so a
# caller presenting counts can say so instead of understating silently.
_MAX_PAGES = 100


class QCClient:
    """HTTP client for the QC Database API.

    Two credential modes:

    * **stdio / local** (default) - ``store`` holds the on-disk token and this
      client silently refreshes it via the OAuth refresh token.
    * **hosted** - ``access_token`` is the bearer token from the current
      authenticated request. There is no refresh: the token's lifetime is the
      MCP client's problem, so a 401 simply surfaces as "re-authenticate".

    In hosted mode a shared ``http`` connection pool is passed in and this client
    does not own (or close) it.
    """

    def __init__(
        self,
        store: Any = None,
        *,
        access_token: str | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.store = store if store is not None else Store()
        self.port = callback_port()
        self._access_token = access_token
        # Set by get_all() when it stops at the page cap; callers that present
        # counts (e.g. turnover_report) can check it and flag "at least N".
        self.last_truncated = False
        if http is not None:
            self._http = http
            self._owns_http = False
        else:
            self._http = httpx.Client(base_url=BASE_URL, timeout=60.0, follow_redirects=True)
            self._owns_http = True

    # ----- core request --------------------------------------------------
    def _headers(self) -> dict[str, str]:
        if self._access_token is not None:
            return {"Authorization": f"Bearer {self._access_token}"}
        token = ensure_access_token(self.store, self.port)
        return {"Authorization": f"Bearer {token}"}

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        data: dict[str, Any] | None = None,
        files: Any | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Make one authenticated request and return parsed JSON (or None).

        ``timeout`` overrides the client default for this call - for endpoints
        that do a lot of work in one round trip (the zipmap ingest builds a
        drawing and all of its map items in a single transaction).
        """
        _check_api_path(path)
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        extra: dict[str, Any] = {} if timeout is None else {"timeout": timeout}

        def _send() -> httpx.Response:
            return self._http.request(
                method,
                path,
                params=clean_params or None,
                json=json,
                data=data,
                files=files,
                headers=self._headers(),
                **extra,
            )

        try:
            resp = _send()
            if resp.status_code == 401 and self._access_token is None:
                # stdio only: the token may have just expired server-side; force
                # one refresh and retry. In hosted mode we cannot refresh someone
                # else's token, so a 401 falls straight through to _handle.
                refresh(self.store, self.port)
                # The first send read any upload file handles to EOF; rewind them
                # so the retry does not silently upload zero bytes.
                _rewind_files(files)
                resp = _send()
        except httpx.HTTPError as exc:
            raise APIError(f"Could not reach QC Database: {exc}") from exc

        return self._handle(resp)

    def _handle(self, resp: httpx.Response) -> Any:
        if resp.is_success:
            if resp.status_code == 204 or not resp.content:
                return None
            try:
                return resp.json()
            except ValueError:
                return resp.text

        detail = self._detail(resp)
        code = resp.status_code
        if code == 401:
            self.store.clear_token()
            exc: Exception = AuthError(
                "Your session is no longer valid. Run 'login' again."
            )
        elif code == 403:
            exc = APIError(
                "Access denied (403). You are signed in but are not a member of "
                f"this project, or your app lacks the needed permission. {detail}"
            )
        elif code == 404:
            exc = APIError(
                "Not found (404). That id does not exist inside the organization "
                f"your login is pinned to. {detail}"
            )
        elif code == 400:
            exc = APIError(
                f"The request was rejected (400). Often this means an id belongs "
                f"to a different project or organization. {detail}"
            )
        else:
            exc = APIError(f"QC Database returned an error ({code}). {detail}")
        # Expose the HTTP status so callers can react to specific codes (e.g. a
        # download falling back from the base64 route to the raw export on 413).
        exc.status_code = code  # type: ignore[attr-defined]
        raise exc

    @staticmethod
    def _detail(resp: httpx.Response) -> str:
        try:
            body = resp.json()
        except ValueError:
            return (resp.text or "").strip()[:300]
        if isinstance(body, dict):
            for key in ("detail", "error_description", "error", "message"):
                if key in body:
                    return str(body[key]) + QCClient._itemized(body)
            return "; ".join(f"{k}: {v}" for k, v in body.items())[:400]
        return str(body)[:300]

    @staticmethod
    def _itemized(body: dict[str, Any]) -> str:
        """Append a per-field/per-item error list, when the body carries one.

        Validation-heavy endpoints (the zipmap ingest most of all) answer with a
        summary ``error`` plus an ``errors`` array that says *which* item or field
        failed - RFC 6901 JSON pointers, codes, and messages. Dropping that array
        would leave the assistant with "validation failed" and nothing to fix.
        """
        errors = body.get("errors")
        if not isinstance(errors, list) or not errors:
            return ""
        lines = []
        for item in errors[:20]:
            if isinstance(item, dict):
                where = item.get("pointer") or item.get("field") or item.get("item_id") or ""
                msg = item.get("message") or item.get("detail") or str(item)
                lines.append(f"  - {where}: {msg}" if where else f"  - {msg}")
            else:
                lines.append(f"  - {item}")
        if len(errors) > 20:
            lines.append(f"  - ... and {len(errors) - 20} more")
        return "\n" + "\n".join(lines)

    # ----- convenience verbs --------------------------------------------
    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def post(
        self, path: str, json: Any | None = None, *, timeout: float | None = None, **params: Any
    ) -> Any:
        return self.request("POST", path, json=json, params=params, timeout=timeout)

    def patch(self, path: str, json: Any | None = None, **params: Any) -> Any:
        return self.request("PATCH", path, json=json, params=params)

    def delete(self, path: str, **params: Any) -> Any:
        return self.request("DELETE", path, params=params)

    def download(self, path: str, **params: Any) -> bytes:
        """GET a binary file (a rendered PDF, an original upload) and return its
        raw bytes. Uses the same auth + one-shot-refresh handling as request(),
        but does not try to parse the body as JSON."""
        _check_api_path(path)
        clean_params = {k: v for k, v in params.items() if v is not None}

        def _send() -> httpx.Response:
            return self._http.get(path, params=clean_params or None, headers=self._headers())

        try:
            resp = _send()
            if resp.status_code == 401 and self._access_token is None:
                refresh(self.store, self.port)
                resp = _send()
        except httpx.HTTPError as exc:
            raise APIError(f"Could not reach QC Database: {exc}") from exc

        if not resp.is_success:
            # _handle turns the documented error statuses into clear messages;
            # on any non-success status it always raises.
            self._handle(resp)
        return resp.content

    def upload(
        self,
        path: str,
        files: list[tuple[str, tuple[str, BinaryIO, str]]],
        data: dict[str, Any] | None = None,
    ) -> Any:
        """POST a multipart/form-data body (file uploads)."""
        clean = {k: v for k, v in (data or {}).items() if v is not None}
        return self.request("POST", path, files=files, data=clean or None)

    # ----- pagination ----------------------------------------------------
    def get_all(self, path: str, **params: Any) -> list[Any]:
        """GET a list endpoint, following DRF pagination up to a sane cap.

        Follows the server's ``next`` link by reusing *its* query parameters on
        the same (trusted, relative) path, so this works for page-number and
        cursor/offset pagination alike. Sets ``self.last_truncated`` if the page
        cap is hit before the list is exhausted.
        """
        self.last_truncated = False
        results: list[Any] = []
        next_params: dict[str, Any] | None = None

        for _ in range(_MAX_PAGES):
            call_params = params if next_params is None else {**params, **next_params}
            payload = self.get(path, **call_params)

            if isinstance(payload, list):
                return payload
            if not isinstance(payload, dict):
                return results if results else ([] if payload is None else [payload])

            if "results" in payload and isinstance(payload["results"], list):
                results.extend(payload["results"])
                nxt = payload.get("next")
                if not nxt:
                    return results
                next_params = dict(parse_qsl(urlsplit(str(nxt)).query))
                if not next_params:
                    return results  # malformed 'next' - stop rather than loop
                continue

            # Non-paginated custom shapes (e.g. {"items": [...]}, {"lists": [...]},
            # {"notes": [...]}, {"schemas": [...]}, {"photos": [...]},
            # {"subsections": [...]}).
            for key in ("items", "lists", "notes", "data", "schemas", "photos", "subsections"):
                if key in payload and isinstance(payload[key], list):
                    return payload[key]
            # Fallback for an unrecognized single-key envelope like {"widgets": [...]}:
            # return the inner list rather than treating the whole wrapper as one
            # opaque row (which silently hides every real item and its id).
            if len(payload) == 1:
                (only_value,) = payload.values()
                if isinstance(only_value, list):
                    return only_value
            return [payload]

        # Fell out of the loop => still had a 'next' at the cap.
        self.last_truncated = True
        print(
            f"[qcdatabase-mcp] warning: '{path}' returned more than {_MAX_PAGES} "
            "pages; result list truncated, counts may be incomplete.",
            file=sys.stderr,
        )
        return results

    def close(self) -> None:
        if self._owns_http:
            self._http.close()
