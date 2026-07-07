"""Minimal stdlib HTTP client — the small slice of httpx this project used.

Why: httpx is pre-1.0 (no stable API). We only used sync Client with cookie
persistence (the session-login handshake depends on it), verify=False for the
device's self-signed TLS, basic auth, and .json()/.content/.raise_for_status().
That maps cleanly onto urllib + http.cookiejar + ssl, so no third-party dep.
"""

import base64
import json as _json
import ssl
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from types import TracebackType
from typing import Literal, cast
from urllib.parse import urlencode


# ponytail: one exception type. raise_for_status (HTTP 4xx/5xx) and transport
# failures (connection refused, timeout) both raise this — matches the single
# `except httpx.HTTPError` the callers rely on.
class HTTPError(Exception):
    """Request failed (connection error or, via raise_for_status, 4xx/5xx)."""


HTTP_ERROR_STATUS = 400

_UNVERIFIED = ssl.create_default_context()
_UNVERIFIED.check_hostname = False
_UNVERIFIED.verify_mode = ssl.CERT_NONE


class Response:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content

    def json(self: "Response") -> object:
        return _json.loads(self.content)

    def raise_for_status(self) -> None:
        if self.status_code >= HTTP_ERROR_STATUS:
            msg = f"HTTP {self.status_code}"
            raise HTTPError(msg)


class Client:
    """Context-managed session. Cookies persist across requests on one instance."""

    def __init__(
        self,
        base_url: str = "",
        headers: dict | None = None,
        auth: tuple[str, str] | None = None,
        verify: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._headers = dict(headers or {})
        if auth is not None:
            token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
            self._headers.setdefault("Authorization", f"Basic {token}")
        handlers: list[urllib.request.BaseHandler] = [
            urllib.request.HTTPCookieProcessor(CookieJar()),
        ]
        if not verify:
            handlers.append(urllib.request.HTTPSHandler(context=_UNVERIFIED))
        self._opener = urllib.request.build_opener(*handlers)

    def __enter__(self: "Client") -> "Client":
        return self

    def __exit__(
        self: "Client",
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        return False

    def _url(self, path: str, params: dict[str, object] | None = None) -> str:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return url

    def _request(self, method: str, path: str, **kwargs: object) -> Response:
        params = cast("dict[str, object] | None", kwargs.get("params"))
        json_payload = cast("dict[str, object] | None", kwargs.get("json"))
        content = cast("bytes | None", kwargs.get("content"))
        headers = cast("dict[str, str] | None", kwargs.get("headers"))

        body = content
        hdrs = {**self._headers, **(headers or {})}
        if json_payload is not None:
            body = _json.dumps(json_payload).encode()
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(
            self._url(path, params),
            data=body,
            headers=hdrs,
            method=method,
        )
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return Response(resp.status, resp.read())
        except urllib.error.HTTPError as e:
            # 4xx/5xx: a response, not a failure yet — surfaced via raise_for_status.
            return Response(e.code, e.read())
        except (urllib.error.URLError, OSError) as e:
            raise HTTPError(str(e)) from e

    def get(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        return self._request("GET", path, params=params, headers=headers)

    def post(
        self,
        path: str,
        *,
        json: dict[str, object] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        return self._request("POST", path, json=json, content=content, headers=headers)

    def put(
        self,
        path: str,
        *,
        json: dict[str, object] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        return self._request("PUT", path, json=json, content=content, headers=headers)
