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
_MGR_FN_COMMAND_MIN_PARTS = 2
_PROTOCOL = "TKS-IP"

_SID_RE = re.compile(r'decodeCommand\(0,\s*6,\s*"([^"]+)"')

# Decoded from the live web app's browser JS (managerMapping/functionMapping
# arrays in com.gira.wgt.web.min.js, see research/tks-ip-v1/api-surface.md):
# manager 0 is CommandManager. fn 18 is sessionClosed — the browser's own
# reaction is CommandManager.reloadPage(), i.e. a fresh GET / for a new
# session. fn 19 is invalidSiteID, which the browser treats as terminal
# (stops polling, shows "Tab closed...").
_SESSION_CLOSED = (0, 18)
_INVALID_SITE_ID = (0, 19)


def _scan_session_signal(commands: list[Any]) -> bool:
    """Return True if a recoverable sessionClosed signal is present.

    Raises ProtocolError for the terminal invalidSiteID signal.
    """

    def _is(cmd: object, signal: tuple[int, int]) -> bool:
        return (
            isinstance(cmd, list)
            and len(cmd) >= _MGR_FN_COMMAND_MIN_PARTS
            and (cmd[0], cmd[1]) == signal
        )

    for cmd in commands[1:]:
        if _is(cmd, _INVALID_SITE_ID):
            msg = "TKS-IP reported invalidSiteID — this session cannot be recovered"
            raise ProtocolError(_PROTOCOL, "command loop", "invalid-site-id", msg)
    return any(_is(cmd, _SESSION_CLOSED) for cmd in commands[1:])


def _find_widget_id(html: str, css_class: str) -> str:
    """Find the id of the div wrapping the button/input under a CSS class.

    Confirmed shape: <td class="aBSaveButton"><div id="c104"><button>...
    The class lives above the id-bearing control container. Firmware may add
    decorative divs between that container and the button or input, so match
    descendants rather than requiring the control to be a direct child.
    """
    tree = lxml_html.fromstring(html)
    containers = cast("list[HtmlElement]", tree.xpath(f'//*[@class="{css_class}"]'))
    if not containers:
        msg = f"TKS-IP widget with class {css_class!r} not found"
        raise ProtocolError(_PROTOCOL, "parse page", "missing-widget", msg)
    controls = cast(
        "list[HtmlElement]",
        containers[0].xpath(".//div[@id][descendant::button or descendant::input]"),
    )
    if not controls:
        msg = f"no control found under TKS-IP widget class {css_class!r}"
        raise ProtocolError(_PROTOCOL, "parse page", "missing-control", msg)
    widget_id = controls[0].get("id")
    assert widget_id is not None
    return widget_id


def _find_link_id(html: str, label: str) -> str:
    """Find the id of an `<a>` navigation link by its visible label text.

    Link ids (e.g. "l8") are assigned by DOM order, which shifts depending on
    which optional menu rows a given device has configured, so — like widget
    ids — they must be looked up per page load rather than hardcoded. Unlike
    buttons, these menu links carry no distinguishing CSS class, only label
    text (confirmed via live HAR capture, de-DE locale only — en-GB label
    text is unconfirmed).
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
    """Concatenate every HTML fragment argument in a command response.

    Content commands (replaceContent, appendEntry, ...) each carry their
    fragment as a one-element list, e.g. `[0, 21, "#c128", ["<div>...</div>"]]`.
    For read-only parsing we don't need to replay these into a DOM tree at the
    right place — concatenating every fragment and querying by CSS class is
    enough to find labelled name/value pairs anywhere in the response.
    """
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
    """Parse Geräteinfos name/value rows out of concatenated Administration HTML."""
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
            # A running application can require the browser's state bootstrap
            # cookie before it issues a new command-loop session.
            state = self._client.get("/state", params={"callback": "setState"})
            state.raise_for_status()
            cookie_sid = self._client._cookie_value("SID")
            if cookie_sid:
                self._sid = cookie_sid
                return
            resp = self._client.get("/")
            match = _SID_RE.search(resp.content.decode(errors="replace"))
        if not match:
            msg = "could not find session id in TKS-IP root page"
            raise ProtocolError(_PROTOCOL, "connect", "missing-session", msg)
        self._sid = match.group(1)

    def _send(self, data: list[object], *, _reconnected: bool = False) -> list[Any]:
        if self._sid is None:
            self._connect()
        resp = self._client.get(
            "/json",
            params={"sid": self._sid, "rid": "0", "data": json.dumps(data)},
        )
        resp.raise_for_status()
        commands = cast("list[Any]", resp.json())
        if _scan_session_signal(commands):
            if _reconnected:
                msg = "TKS-IP session closed repeatedly — could not re-establish a session"
                raise ProtocolError(_PROTOCOL, "command loop", "session-closed", msg)
            self._sid = None
            return self._send(data, _reconnected=True)
        return commands

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

    def poll(self, *, _reconnected: bool = False) -> list[Any]:
        """Poll the command loop, keeping the temporary web session alive."""
        if self._sid is None:
            self._connect()
        resp = self._client.get("/json", params={"sid": self._sid or "", "rid": "0"})
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            msg = "non-list response from TKS-IP command poll"
            raise ProtocolError(_PROTOCOL, "poll", "invalid-response", msg)
        if _scan_session_signal(payload):
            if _reconnected:
                msg = "TKS-IP session closed repeatedly — could not re-establish a session"
                raise ProtocolError(_PROTOCOL, "command loop", "session-closed", msg)
            self._sid = None
            return self.poll(_reconnected=True)
        return payload

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
        """Read the read-only device-info panel from the Administration page.

        Navigates via the "Geräteinfos" menu link rather than assuming the
        panel lives on the post-login landing page — reaching it requires
        this explicit navigation (see research/tks-ip-v1/api-surface.md).
        """
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
                self.poll()
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
