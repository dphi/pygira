"""TKS-IP gateway web-app client — port 8080, JSON command-loop protocol.

This is the on-demand web app (`com_gira_tkipgw`), not the always-on port 80
bootstrap daemon (see config_service.activate_tks_webinterface for that).

Protocol, confirmed via live HAR capture (2026-07-04):
  1. GET /            -> body contains decodeCommand(0,6,"<sid>",0)
  2. GET /json?sid=<sid>&rid=0&data=["reload"] -> full page as a command
     array; one command is [0, 0, "body", [<html>], true]
  3. GET /json?sid=<sid>&rid=0&data=["click","<id>"] or
     ["value","<id>","<text>",...] drive the UI the same way a browser does

Login does NOT go through a button click at all — the browser submits when
the password field's commit event carries an extra flag (observed:
["value", pass_id, password, true, true, false] vs true,false,false for a
non-submitting change). There is no dedicated login endpoint.

Widget ids (e.g. "c61") are assigned fresh per session and are NOT stable,
but the CSS classes server-rendered around them ARE stable across sessions
(e.g. "aBSaveButton", "lLDCName") — so every control is looked up by class,
never by hardcoded id.
"""

import json
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

from lxml import html as lxml_html

from pygira import _http as httpx
from pygira.exceptions import AuthenticationError, OperationTimeoutError, ProtocolError

if TYPE_CHECKING:
    from lxml.html import HtmlElement

HTML_BODY_COMMAND_MIN_PARTS = 4
_PROTOCOL = "TKS-IP"

_SID_RE = re.compile(r'decodeCommand\(0,\s*6,\s*"([^"]+)"')


def _find_widget_id(html: str, css_class: str) -> str:
    """Find the id of the div wrapping the button/input under a CSS class.

    Confirmed shape: <td class="aBSaveButton"><div id="c104"><button>...
    The class lives one level above the id-bearing div, so we search the
    subtree for the first div[@id] that directly wraps a button or input.
    """
    tree = lxml_html.fromstring(html)
    containers = cast("list[HtmlElement]", tree.xpath(f'//*[@class="{css_class}"]'))
    if not containers:
        msg = f"TKS-IP widget with class {css_class!r} not found"
        raise ProtocolError(_PROTOCOL, "parse page", "missing-widget", msg)
    controls = cast("list[HtmlElement]", containers[0].xpath(".//div[@id][button or input]"))
    if not controls:
        msg = f"no control found under TKS-IP widget class {css_class!r}"
        raise ProtocolError(_PROTOCOL, "parse page", "missing-control", msg)
    widget_id = controls[0].get("id")
    assert widget_id is not None
    return widget_id


def _find_link_id(html: str, label: str) -> str:
    """Find the id of an `<a>` navigation link by its visible label text.

    Link ids are assigned by DOM order and can shift when optional menu rows
    are present, so resolve them from the current page rather than hardcoding
    an id.
    """
    tree = lxml_html.fromstring(html)
    links = cast("list[HtmlElement]", tree.xpath(f'//a[@id][normalize-space(.)="{label}"]'))
    if not links:
        msg = f"TKS-IP navigation link labelled {label!r} not found"
        raise ProtocolError(_PROTOCOL, "parse page", "missing-link", msg)
    link_id = links[0].get("id")
    assert link_id is not None
    return link_id


def _collect_html_fragments(commands: list[Any]) -> str:
    """Concatenate HTML fragments carried by command-loop responses."""
    fragments: list[str] = []
    for cmd in commands[1:]:
        if not isinstance(cmd, list):
            continue
        fragments.extend(
            arg[0]
            for arg in cmd
            if isinstance(arg, list) and len(arg) == 1 and isinstance(arg[0], str)
        )
    return "".join(fragments)


def _parse_device_info(html_blob: str) -> dict[str, str]:
    """Parse Geräteinfos name/value rows from Administration HTML."""
    tree = lxml_html.fromstring(f"<div>{html_blob}</div>")
    names = cast("list[HtmlElement]", tree.xpath('//*[@class="aDICECName"]//span'))
    values = cast("list[HtmlElement]", tree.xpath('//*[@class="aDICECValue"]//span'))
    return {
        (name.text_content() or "").rstrip(":").strip(): (value.text_content() or "").strip()
        for name, value in zip(names, values, strict=False)
    }


def _multipart_body(filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, f"multipart/form-data; boundary={boundary}"


class TksWebClient:
    """Session client for the TKS-IP gateway's on-demand web app (port 8080)."""

    def __init__(self, host: str, timeout: float = 30.0) -> None:
        """Create a client; call login() before any other method."""
        self._client = httpx.Client(base_url=f"http://{host}:8080", timeout=timeout)
        self._sid: str | None = None

    def _connect(self) -> None:
        resp = self._client.get("/")
        match = _SID_RE.search(resp.content.decode(errors="replace"))
        if not match:
            msg = "could not find session id in TKS-IP root page"
            raise ProtocolError(_PROTOCOL, "connect", "missing-session", msg)
        self._sid = match.group(1)

    def _send(self, data: list[object]) -> list[Any]:
        if self._sid is None:
            self._connect()
        resp = self._client.get(
            "/json",
            params={"sid": self._sid, "rid": "0", "data": json.dumps(data)},
        )
        resp.raise_for_status()
        return cast("list[Any]", resp.json())

    @staticmethod
    def _extract_html(commands: list) -> str:
        for cmd in commands[1:]:
            if (
                isinstance(cmd, list)
                and len(cmd) >= HTML_BODY_COMMAND_MIN_PARTS
                and cmd[0] == 0
                and cmd[1] == 0
                and cmd[2] == "body"
                and isinstance(cmd[3], list)
                and cmd[3]
            ):
                return cmd[3][0]
        msg = "no HTML body in TKS-IP command response"
        raise ProtocolError(_PROTOCOL, "reload", "missing-body", msg)

    def reload(self) -> str:
        """Send a reload and return the main content HTML."""
        return self._extract_html(self._send(["reload"]))

    def click(self, widget_id: str) -> list:
        """Send a click event for a widget id (see _find_widget_id)."""
        return self._send(["click", widget_id])

    def upload(self, endpoint: str, filename: str, data: bytes) -> None:
        """POST a file to a plain (non-JSON-loop) multipart upload endpoint."""
        body, content_type = _multipart_body(filename, data)
        resp = self._client.post(endpoint, content=body, headers={"Content-Type": content_type})
        resp.raise_for_status()

    def download(self, path: str) -> bytes:
        """GET a plain (non-JSON-loop) path and return its raw bytes."""
        resp = self._client.get(path)
        resp.raise_for_status()
        return resp.content

    def login(self, username: str, password: str, *, timeout: float = 15.0) -> None:
        """Log in.

        Submits via the password field's commit flag — there is no separate
        login-button click in the real protocol.
        """
        html = self.reload()
        user_id = _find_widget_id(html, "lLDCName")
        pass_id = _find_widget_id(html, "lLDCPassword")
        self._send(["value", user_id, username, True, False, False])
        self._send(["value", pass_id, password, True, True, False])

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(1.0)
            html = self.reload()
            try:
                _find_widget_id(html, "lLLoginButton")
            except ProtocolError:
                return  # login page no longer shown -> logged in
        msg = "TKS-IP login failed or timed out — check tks_ip credentials"
        command = "TKS-IP login"
        response = {"id": "timeout", "error": msg}
        raise AuthenticationError(command, response)

    def device_info(self) -> dict[str, str]:
        """Read the device-info panel from the Administration page."""
        html = self.reload()
        link_id = _find_link_id(html, "Geräteinfos")
        commands = self._send(["link", link_id])
        return _parse_device_info(_collect_html_fragments(commands))

    def backup_save(self, *, timeout: float = 30.0) -> bytes:
        """Trigger a configuration backup and download the resulting file."""
        html = self.reload()
        self.click(_find_widget_id(html, "aBSaveButton"))

        deadline = time.monotonic() + timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            time.sleep(1.0)
            try:
                return self.download("/files/backup.img")
            except httpx.HTTPError as exc:
                last_err = exc
        msg = f"backup file did not become available: {last_err}"
        raise OperationTimeoutError(msg)

    def backup_restore(self, data: bytes, filename: str = "backup.img") -> None:
        """Upload a backup file and trigger restoring it."""
        self.upload("/upload?id=backup", filename, data)
        html = self.reload()
        self.click(_find_widget_id(html, "aBRestoreButton"))

    def firmware_update(self, data: bytes, filename: str = "firmware.bin") -> None:
        """Upload a firmware image and trigger applying it."""
        self.upload("/update", filename, data)
        html = self.reload()
        self.click(_find_widget_id(html, "aUSUpdateButton"))
