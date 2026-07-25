"""Tiny respx-compatible mock over pygira._http (the stdlib HTTP shim).

Replaces respx (which only intercepts httpx). Patches _http.Client._request and
serves a route table keyed on URL (query string ignored, like respx defaults).
Implements just the surface the tests use: @mock / `with mock`, get/post/put,
Response(json=/content=/text=), Route.mock(return_value=/side_effect=),
route.called, route.calls.last.request.read()/.headers, module-level calls.
"""

import functools
import json as _json
from collections.abc import Callable, Sequence
from types import TracebackType
from typing import ParamSpec, TypeVar, cast
from unittest.mock import patch
from urllib.parse import urlencode

from pygira import _http

P = ParamSpec("P")
R = TypeVar("R")
HTTP_ERROR_STATUS = 400


class Response:
    def __init__(  # noqa: PLR0913 - mirrors the HTTP response test surface
        self,
        status_code: int = 200,
        *,
        json: object = None,
        content: bytes | None = None,
        text: str | None = None,
        headers: dict[str, str] | None = None,
        request: "Request | None" = None,
    ) -> None:
        self.status_code = status_code
        if content is not None:
            self.content = content
        elif text is not None:
            self.content = text.encode()
        elif json is not None:
            self.content = _json.dumps(json).encode()
        else:
            self.content = b""
        self.headers = headers or {}
        self.request = request

    def json(self) -> object:
        return _json.loads(self.content)

    def raise_for_status(self) -> None:
        if self.status_code >= HTTP_ERROR_STATUS:
            msg = f"HTTP {self.status_code}"
            raise _http.HTTPError(msg)


class Request:
    def __init__(
        self,
        method: str = "GET",
        url: str = "",
        headers: dict[str, str] | None = None,
        content: bytes = b"",
    ) -> None:
        self.method = method
        self.url = url
        self.headers = headers or {}
        self._content = content

    def read(self) -> bytes:
        return self._content


class _Calls(list["_Call"]):
    @property
    def last(self) -> "_Call":
        return self[-1]


class _Call:
    def __init__(self, request: Request) -> None:
        self.request = request


class Route:
    def __init__(self) -> None:
        self._single = None
        self._queue: list[Response] | None = None
        self._fn: Callable[[Request], Response] | None = None
        self.calls = _Calls()

    def mock(
        self,
        return_value: Response | None = None,
        side_effect: Callable[[Request], Response] | Sequence[Response] | None = None,
    ) -> "Route":
        if callable(side_effect):
            self._fn = side_effect
        elif side_effect is not None:
            self._queue = list(side_effect)
        else:
            self._single = return_value
        return self

    @property
    def called(self) -> bool:
        return len(self.calls) > 0

    def _respond(self, request: Request) -> Response | None:
        if self._fn is not None:
            return self._fn(request)
        if self._queue is not None:
            return self._queue.pop(0)
        return self._single


_routes: dict[tuple[str, str], Route] = {}
calls = _Calls()


def _key(method: str, url: str) -> tuple[str, str]:
    return (method, url.split("?", 1)[0])


def _register(method: str, url: str) -> Route:
    route = Route()
    _routes[_key(method, url)] = route
    return route


def get(url: str) -> Route:
    return _register("GET", url)


def post(url: str) -> Route:
    return _register("POST", url)


def put(url: str) -> Route:
    return _register("PUT", url)


def _fake_request(self: _http.Client, method: str, path: str, **kwargs: object) -> Response:
    params = cast("dict[str, object] | None", kwargs.get("params"))
    json_payload = kwargs.get("json")
    content = cast("bytes | None", kwargs.get("content"))
    headers = cast("dict[str, str] | None", kwargs.get("headers"))

    url = path if path.startswith("http") else f"{self.base_url}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    body = content
    hdrs = {**self._headers, **(headers or {})}
    if json_payload is not None:
        body = _json.dumps(json_payload).encode()
        hdrs.setdefault("Content-Type", "application/json")
    request = Request(method, url, hdrs, body or b"")

    route = _routes.get(_key(method, url))
    if route is None:
        msg = f"No mocked route for {method} {url}"
        raise AssertionError(msg)
    route.calls.append(_Call(request))
    calls.append(_Call(request))
    resp = route._respond(request)
    if resp is None:
        msg = f"No response queued for {method} {url}"
        raise AssertionError(msg)
    return resp


class _Mock:
    """Usable as a decorator (@mock) and a context manager (with mock:)."""

    def _start(self) -> None:
        _routes.clear()
        calls.clear()
        self._patch = patch.object(_http.Client, "_request", _fake_request)
        self._patch.start()

    def _stop(self) -> None:
        self._patch.stop()

    def __call__(self, func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            self._start()
            try:
                return func(*args, **kwargs)
            finally:
                self._stop()

        return wrapper

    def __enter__(self) -> "_Mock":
        self._start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self._stop()
        return False


mock = _Mock()
