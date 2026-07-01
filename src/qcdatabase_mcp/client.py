"""Thin HTTP client around the QC Database public API.

Handles the things every tool would otherwise repeat: attaching the bearer
token, refreshing it once on a 401, turning the documented 400/403/404 errors
into clear messages, and following pagination so a tool can just get a full list
back.
"""

from __future__ import annotations

import os
from typing import Any, BinaryIO

import httpx

from . import BASE_URL
from .auth import DEFAULT_CALLBACK_PORT, AuthError, ensure_access_token, refresh
from .config import Store


class APIError(RuntimeError):
    """A request reached the server but came back with an error status."""


def callback_port() -> int:
    raw = os.environ.get("QCDB_CALLBACK_PORT")
    if not raw:
        return DEFAULT_CALLBACK_PORT
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_CALLBACK_PORT


# Cap on how many pages we will auto-follow, so a huge project can never hang a
# tool call indefinitely. 20 pages is far more than a person reads in one go.
_MAX_PAGES = 20


class QCClient:
    def __init__(self, store: Store | None = None) -> None:
        self.store = store or Store()
        self.port = callback_port()
        self._http = httpx.Client(base_url=BASE_URL, timeout=60.0, follow_redirects=True)

    # ----- core request --------------------------------------------------
    def _headers(self) -> dict[str, str]:
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
    ) -> Any:
        """Make one authenticated request and return parsed JSON (or None)."""
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        def _send() -> httpx.Response:
            return self._http.request(
                method,
                path,
                params=clean_params or None,
                json=json,
                data=data,
                files=files,
                headers=self._headers(),
            )

        try:
            resp = _send()
            if resp.status_code == 401:
                # Token may have just been revoked/expired server-side; try one
                # forced refresh, then retry the call.
                refresh(self.store, self.port)
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
            raise AuthError("Your session is no longer valid. Run 'login' again.")
        if code == 403:
            raise APIError(
                "Access denied (403). You are signed in but are not a member of "
                f"this project, or your app lacks the needed permission. {detail}"
            )
        if code == 404:
            raise APIError(
                "Not found (404). That id does not exist inside the organization "
                f"your login is pinned to. {detail}"
            )
        if code == 400:
            raise APIError(
                f"The request was rejected (400). Often this means an id belongs "
                f"to a different project or organization. {detail}"
            )
        raise APIError(f"QC Database returned an error ({code}). {detail}")

    @staticmethod
    def _detail(resp: httpx.Response) -> str:
        try:
            body = resp.json()
        except ValueError:
            return (resp.text or "").strip()[:300]
        if isinstance(body, dict):
            for key in ("detail", "error_description", "error", "message"):
                if key in body:
                    return str(body[key])
            return "; ".join(f"{k}: {v}" for k, v in body.items())[:400]
        return str(body)[:300]

    # ----- convenience verbs --------------------------------------------
    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Any | None = None, **params: Any) -> Any:
        return self.request("POST", path, json=json, params=params)

    def patch(self, path: str, json: Any | None = None, **params: Any) -> Any:
        return self.request("PATCH", path, json=json, params=params)

    def delete(self, path: str, **params: Any) -> Any:
        return self.request("DELETE", path, params=params)

    def download(self, path: str, **params: Any) -> bytes:
        """GET a binary file (a rendered PDF, an original upload) and return its
        raw bytes. Uses the same auth + one-shot-refresh handling as request(),
        but does not try to parse the body as JSON."""
        clean_params = {k: v for k, v in params.items() if v is not None}

        def _send() -> httpx.Response:
            return self._http.get(path, params=clean_params or None, headers=self._headers())

        try:
            resp = _send()
            if resp.status_code == 401:
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
        """GET a list endpoint, following DRF pagination up to a sane cap."""
        results: list[Any] = []
        page = 1
        while page <= _MAX_PAGES:
            payload = self.get(path, page=page, **params)
            if isinstance(payload, list):
                return payload
            if not isinstance(payload, dict):
                return results
            if "results" in payload:
                results.extend(payload["results"])
                if not payload.get("next"):
                    break
                page += 1
                continue
            # Non-paginated custom shapes (e.g. {"items": [...]}, {"notes": [...]},
            # {"schemas": [...]}, {"photos": [...]}).
            for key in ("items", "notes", "data", "schemas", "photos"):
                if key in payload and isinstance(payload[key], list):
                    return payload[key]
            return [payload]
        return results

    def close(self) -> None:
        self._http.close()
