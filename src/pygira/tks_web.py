"""TKS-IP gateway web-app client — port 8080, JSON command-loop protocol.

This is the on-demand web app (`com_gira_tkipgw`), not the always-on port 80
bootstrap daemon (see config_service.activate_tks_webinterface for that).

Protocol, confirmed via live HAR capture (2026-07-04):
  1. GET /state then GET / -> body contains decodeCommand(0,6,"<sid>",0)
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
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from lxml import html as lxml_html

from pygira import _http as httpx
from pygira.exceptions import AuthenticationError, OperationTimeoutError, ProtocolError

if TYPE_CHECKING:
    from lxml.html import HtmlElement

HTML_BODY_COMMAND_MIN_PARTS = 4
_MGR_FN_COMMAND_MIN_PARTS = 2
_TEXTBOX_VALUE_COMMAND_MIN_PARTS = 4
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
_POLL_INTERVAL_SECONDS = 0.5
_NETWORK_FIELD_LABELS = {
    "IP-Adresse": "ip_address",
    "Subnetzmaske": "subnet_mask",
    "Nameserver": "nameserver",
    "Standardgateway": "default_gateway",
}


@dataclass
class _PageSnapshot:
    html: str
    commands: list[Any]


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


def _find_button_id(html: str, label: str) -> str:
    """Find a dynamic button-container id by its visible text."""
    tree = lxml_html.fromstring(html)
    controls = cast(
        "list[HtmlElement]",
        tree.xpath(
            '//div[@id][button[normalize-space(.)=$label]]',
            label=label,
        ),
    )
    if not controls:
        msg = f"TKS-IP button labelled {label!r} not found"
        raise ProtocolError(_PROTOCOL, "parse page", "missing-button", msg)
    control_id = controls[0].get("id")
    assert control_id is not None
    return control_id


def _find_assistant_action_id(html: str, label: str) -> str:
    """Find the launch button in a labelled overview-menu row."""
    tree = lxml_html.fromstring(html)
    controls = cast(
        "list[HtmlElement]",
        tree.xpath(
            '//tr[td[1][normalize-space(.)=$label]]'
            '//div[@id][button[normalize-space(.)="Gira Assistent starten"]]',
            label=label,
        ),
    )
    if not controls:
        msg = f"TKS-IP assistant action labelled {label!r} not found"
        raise ProtocolError(_PROTOCOL, "parse page", "missing-assistant", msg)
    control_id = controls[0].get("id")
    assert control_id is not None
    return control_id


def _menu_action(html: str, label: str) -> list[object]:
    """Resolve a menu label to its link or assistant-button event."""
    try:
        return ["link", _find_link_id(html, label)]
    except ProtocolError:
        return ["click", _find_assistant_action_id(html, label)]


def _contains_menu_action(html_blob: str, label: str) -> bool:
    if not html_blob:
        return False
    try:
        _menu_action(html_blob, label)
    except ProtocolError:
        return False
    return True


def _find_tab_selection(html: str, tabbar_class: str, label: str) -> tuple[str, str]:
    """Find the dynamic tabbar and tab ids for a visible tab label."""
    tree = lxml_html.fromstring(f"<div>{html}</div>")
    tabbars = cast(
        "list[HtmlElement]",
        tree.xpath(
            '//*[contains(concat(" ", normalize-space(@class), " "), $class_name)]'
            "//div[@id][ul]",
            class_name=f" {tabbar_class} ",
        ),
    )
    tabs = cast(
        "list[HtmlElement]",
        tree.xpath('//li[@id][normalize-space(.)=$label]', label=label),
    )
    if not tabbars or not tabs:
        msg = f"TKS-IP tab labelled {label!r} not found"
        raise ProtocolError(_PROTOCOL, "parse page", "missing-tab", msg)
    tabbar_id = tabbars[0].get("id")
    tab_id = tabs[0].get("id")
    assert tabbar_id is not None and tab_id is not None
    return tabbar_id, tab_id


def _html_fragments(command: list[Any]) -> list[str]:
    return [
        item
        for arg in command
        if isinstance(arg, list)
        for item in arg
        if isinstance(item, str) and item.lstrip().startswith("<")
    ]


def _collect_html_fragments(commands: list[Any]) -> str:
    """Concatenate every HTML fragment argument in a command response.

    Content commands (replaceContent, appendEntry, ...) carry one or more
    fragments in a list, e.g. `[0, 21, "#c128", ["<div>...</div>"]]`. The
    Administration shell uses a two-fragment variant for its main content.
    For read-only parsing we don't need to replay these into a DOM tree at the
    right place — concatenating every HTML-looking fragment and querying by
    CSS class is enough to find labelled fields anywhere in the response.
    """
    fragments: list[str] = []
    for cmd in commands[1:]:
        if not isinstance(cmd, list):
            continue
        fragments.extend(_html_fragments(cmd))
    return "".join(fragments)


def _contains_link(html_blob: str, label: str) -> bool:
    if not html_blob:
        return False
    try:
        _find_link_id(html_blob, label)
    except ProtocolError:
        return False
    return True


def _contains_widget(html_blob: str, css_class: str) -> bool:
    if not html_blob:
        return False
    try:
        _find_widget_id(html_blob, css_class)
    except ProtocolError:
        return False
    return True


def _contains_class(html_blob: str, css_class: str) -> bool:
    if not html_blob:
        return False
    tree = lxml_html.fromstring(f"<div>{html_blob}</div>")
    return bool(
        tree.xpath(
            '//*[contains(concat(" ", normalize-space(@class), " "), $class_name)]',
            class_name=f" {css_class} ",
        ),
    )


def _parse_device_info(html_blob: str) -> dict[str, str]:
    """Parse Geräteinfos name/value rows out of concatenated Administration HTML."""
    if not html_blob:
        return {}
    tree = lxml_html.fromstring(f"<div>{html_blob}</div>")
    names = cast("list[HtmlElement]", tree.xpath('//*[@class="aDICECName"]//span'))
    values = cast("list[HtmlElement]", tree.xpath('//*[@class="aDICECValue"]//span'))
    return {
        (name.text_content() or "").rstrip(":").strip(): (value.text_content() or "").strip()
        for name, value in zip(names, values, strict=False)
    }


def _selected_option_text(container: "HtmlElement") -> str | None:
    options = cast("list[HtmlElement]", container.xpath(".//select/option"))
    selected = next(
        (
            option
            for option in options
            if option.get("selected") is not None
            or "ui-state-active" in (option.get("class") or "").split()
        ),
        None,
    )
    option = selected if selected is not None else (options[0] if options else None)
    return (option.text_content() or "").strip() if option is not None else None


def _textbox_values(commands: list[Any]) -> dict[str, str]:
    """Return TextboxManager.setValue values keyed by their dynamic selector."""
    return {
        command[2]: command[3]
        for command in commands
        if isinstance(command, list)
        and len(command) >= _TEXTBOX_VALUE_COMMAND_MIN_PARTS
        and command[:2] == [26, 32]
        and isinstance(command[2], str)
        and isinstance(command[3], str)
    }


def _control_selector(container: "HtmlElement") -> str | None:
    controls = cast(
        "list[HtmlElement]",
        container.xpath(
            ".//input/ancestor::div[@id][1] | .//select/ancestor::div[@id][1]",
        ),
    )
    control_id = controls[0].get("id") if controls else None
    return f"#{control_id}" if control_id else None


def _parse_date_time_info(html_blob: str, commands: list[Any]) -> dict[str, object]:
    """Parse the current, read-only date/time settings from an Administration page."""
    tree = lxml_html.fromstring(f"<div>{html_blob}</div>")
    values = _textbox_values(commands)

    def container(css_class: str) -> "HtmlElement | None":
        matches = cast("list[HtmlElement]", tree.xpath(f'//*[@class="{css_class}"]'))
        return matches[0] if matches else None

    def textbox(css_class: str) -> str | None:
        owner = container(css_class)
        selector = _control_selector(owner) if owner is not None else None
        return values.get(selector) if selector else None

    timezone = container("aDTTZCombo")
    ntp_server = container("aDTAutoCombo")
    automatic = container("aDTAutoRadio")
    hour = textbox("aDTMHour")
    minute = textbox("aDTMMinute")
    return {
        "timezone": _selected_option_text(timezone) if timezone is not None else None,
        "automatic": bool(automatic is not None and automatic.xpath(".//input[@checked]")),
        "ntp_server": _selected_option_text(ntp_server) if ntp_server is not None else None,
        "date": textbox("aDTMDatePicker"),
        "time": f"{hour}:{minute}" if hour is not None and minute is not None else None,
    }


def _parse_network_info(html_blob: str, commands: list[Any]) -> dict[str, object]:
    """Parse current network/video settings without submitting the form."""
    tree = lxml_html.fromstring(f"<div>{html_blob}</div>")
    values = _textbox_values(commands)

    def first(css_class: str) -> "HtmlElement | None":
        matches = cast("list[HtmlElement]", tree.xpath(f'//*[@class="{css_class}"]'))
        return matches[0] if matches else None

    def textbox(owner: "HtmlElement | None") -> str | None:
        selector = _control_selector(owner) if owner is not None else None
        return values.get(selector) if selector else None

    gateway_id = first("a2NCGID")
    network_name = first("a2NCNetworkName")
    manual_entries = cast("list[HtmlElement]", tree.xpath('//*[@class="a2NManualEntry"]'))
    manual_values: dict[str, str | None] = {}
    for entry in manual_entries:
        label = " ".join(entry.text_content().split())
        key = next(
            (
                field_key
                for prefix, field_key in _NETWORK_FIELD_LABELS.items()
                if label.startswith(prefix)
            ),
            None,
        )
        if key is not None:
            manual_values[key] = textbox(entry)
    radios = cast("list[HtmlElement]", tree.xpath('//*[@class="a2NRadio"]//input'))
    video_radios = cast("list[HtmlElement]", tree.xpath('//*[@class="a2VRadio"]//input'))
    return {
        "gateway_id": _selected_option_text(gateway_id) if gateway_id is not None else None,
        "network_name": textbox(network_name),
        "dhcp": bool(radios and radios[0].get("checked") is not None),
        **{key: manual_values.get(key) for key in _NETWORK_FIELD_LABELS.values()},
        "video_resolution": next(
            (
                resolution
                for radio, resolution in zip(video_radios, ("VGA", "QVGA"), strict=False)
                if radio.get("checked") is not None
            ),
            None,
        ),
    }


def _parse_sip_clients(html_blob: str, commands: list[Any]) -> dict[str, object]:
    """Parse configured SIP clients while deliberately discarding passwords."""
    tree = lxml_html.fromstring(f"<div>{html_blob}</div>")
    values = _textbox_values(commands)
    entries = cast(
        "list[HtmlElement]",
        tree.xpath(
            '//*[contains(concat(" ", normalize-space(@class), " "), '
            '" ssipPTableEntry ")][descendant::input]',
        ),
    )
    selected_row = next(
        (
            command[3]
            for command in commands
            if isinstance(command, list)
            and len(command) >= _TEXTBOX_VALUE_COMMAND_MIN_PARTS
            and command[:2] == [25, 30]
            and isinstance(command[3], str)
        ),
        None,
    )

    def owner(css_class: str) -> "HtmlElement | None":
        matches = cast("list[HtmlElement]", tree.xpath(f'//*[@class="{css_class}"]'))
        return matches[0] if matches else None

    def textbox(container: "HtmlElement | None") -> str | None:
        selector = _control_selector(container) if container is not None else None
        return values.get(selector) if selector else None

    username = textbox(owner("ssipPATUserName"))
    password = textbox(owner("ssipPATPassword"))
    warning = owner("ssipPAWCheck")
    clients: list[dict[str, object]] = []
    for entry in entries:
        name = textbox(entry)
        if name is None:
            continue
        row = cast("list[HtmlElement]", entry.xpath("ancestor::tr[@id][1]"))
        row_selector = f"#{row[0].get('id')}" if row else None
        is_selected = row_selector == selected_row or (selected_row is None and not clients)
        clients.append(
            {
                "name": name,
                "selected": is_selected,
                "username": username if is_selected else None,
                "password_configured": bool(password) if is_selected else None,
            },
        )
    return {
        "clients": clients,
        "security_warning_acknowledged": bool(
            warning is not None and warning.xpath(".//input[@checked]"),
        ),
    }


def _parse_sip_incoming_calls(commands: list[Any]) -> list[dict[str, object]]:
    """Parse grouped incoming-call assignments from the selected SIP tab."""
    groups: list[dict[str, object]] = []
    by_selector: dict[str, dict[str, object]] = {}
    for command in commands:
        if (
            not isinstance(command, list)
            or len(command) < HTML_BODY_COMMAND_MIN_PARTS
            or command[:2] != [0, 21]
            or not isinstance(command[2], str)
        ):
            continue
        target = command[2]
        for fragment in _html_fragments(command):
            tree = lxml_html.fromstring(f"<div>{fragment}</div>")
            group_rows = cast(
                "list[HtmlElement]",
                tree.xpath('//tr[@id][descendant::*[@class="groupTitleInternal"]]'),
            )
            for row in group_rows:
                title = " ".join(
                    row.xpath('.//*[@class="groupTitleInternal"]')[0].text_content().split(),
                )
                selector = f"#{row.get('id')}"
                group: dict[str, object] = {"name": title, "calls": []}
                groups.append(group)
                by_selector[selector] = group

            group_selector = target.split(" ", 1)[0]
            current_group = by_selector.get(group_selector)
            if current_group is None:
                continue
            call_rows = cast(
                "list[HtmlElement]",
                tree.xpath(
                    '//tr[descendant::*[contains('
                    'concat(" ", normalize-space(@class), " "), " ssipPICTableEntry ")]]',
                ),
            )
            calls = cast("list[dict[str, object]]", current_group["calls"])
            for row in call_rows:
                name = " ".join(row.text_content().split())
                checkbox = cast("list[HtmlElement]", row.xpath(".//input[@type='checkbox']"))
                calls.append(
                    {
                        "name": name,
                        "assigned": bool(
                            checkbox and checkbox[0].get("checked") is not None,
                        ),
                    },
                )
    return groups


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
        self._navigation_html: str | None = None
        self._current_page_html: str | None = None

    def _connect(self) -> None:
        # The browser always establishes bootstrap state before opening the
        # root page. In particular, the SID cookie returned by /state is not
        # itself a command-loop session id.
        state = self._client.get("/state", params={"callback": "setState"})
        state.raise_for_status()
        resp = self._client.get("/")
        resp.raise_for_status()
        match = _SID_RE.search(resp.content.decode(errors="replace"))
        if not match:
            msg = "could not find session id in TKS-IP root page"
            raise ProtocolError(_PROTOCOL, "connect", "missing-session", msg)
        self._sid = match.group(1)
        self._navigation_html = None
        self._current_page_html = None

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

    def _wait_for_page(
        self,
        commands: list[Any],
        predicate: Callable[[str], bool],
        *,
        timeout: float,
        operation: str,
    ) -> _PageSnapshot:
        """Accumulate HTML from a send and later polls until a page is complete."""
        html_blob = _collect_html_fragments(commands)
        collected_commands = list(commands[1:])
        deadline = time.monotonic() + timeout
        while not predicate(html_blob):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                msg = f"timed out waiting for TKS-IP {operation}"
                raise ProtocolError(_PROTOCOL, operation, "timeout", msg)
            time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))
            polled = self.poll()
            html_blob += _collect_html_fragments(polled)
            collected_commands.extend(polled[1:])
        return _PageSnapshot(html=html_blob, commands=collected_commands)

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
        commands = self._send(["value", pass_id, password, True, True, False])

        try:
            self._navigation_html = self._wait_for_page(
                commands,
                lambda page: _contains_link(page, "Geräteinfos"),
                timeout=timeout,
                operation="authenticated menu",
            ).html
        except ProtocolError as exc:
            msg = "TKS-IP login failed or timed out — check tks_ip credentials"
            command = "TKS-IP login"
            response = {"id": "timeout", "error": msg}
            raise AuthenticationError(command, response) from exc

    def _navigate_page(
        self,
        label: str,
        predicate: Callable[[str], bool],
        *,
        timeout: float,
    ) -> _PageSnapshot:
        if self._current_page_html is not None:
            overview_id = _find_button_id(self._current_page_html, "Übersicht")
            overview = self._wait_for_page(
                self._send(["click", overview_id]),
                lambda html: _contains_menu_action(html, label),
                timeout=timeout,
                operation="Übersicht",
            )
            self._navigation_html = overview.html
            self._current_page_html = None
        menu_html = self._navigation_html or self.reload()
        self._navigation_html = menu_html
        page = self._wait_for_page(
            self._send(_menu_action(menu_html, label)),
            predicate,
            timeout=timeout,
            operation=label,
        )
        self._current_page_html = page.html
        return page

    def _navigate(
        self,
        label: str,
        predicate: Callable[[str], bool],
        *,
        timeout: float,
    ) -> str:
        return self._navigate_page(label, predicate, timeout=timeout).html

    def device_info(self, *, timeout: float = 10.0) -> dict[str, str]:
        """Read the read-only device-info panel from the Administration page.

        Navigates via the "Geräteinfos" menu link rather than assuming the
        panel lives on the post-login landing page — reaching it requires
        this explicit navigation (see research/tks-ip-v1/api-surface.md).
        """
        html = self._navigate(
            "Geräteinfos",
            lambda page: bool(_parse_device_info(page)),
            timeout=timeout,
        )
        return _parse_device_info(html)

    def date_time_info(self, *, timeout: float = 10.0) -> dict[str, object]:
        """Read the current date/time configuration without changing it."""
        page = self._navigate_page(
            "Datum und Uhrzeit",
            lambda html: _contains_class(html, "aDateTime"),
            timeout=timeout,
        )
        return _parse_date_time_info(page.html, page.commands)

    def network_info(self, *, timeout: float = 10.0) -> dict[str, object]:
        """Read the current network and video configuration without changing it."""
        page = self._navigate_page(
            "Netzwerkzugang einrichten",
            lambda html: _contains_class(html, "a2Network"),
            timeout=timeout,
        )
        return _parse_network_info(page.html, page.commands)

    def sip_clients(self, *, timeout: float = 10.0) -> dict[str, object]:
        """List SIP client names and selected-client details without exposing passwords."""
        page = self._navigate_page(
            "IP-Telefone konfigurieren",
            lambda html: _contains_class(html, "ssipPAssistant"),
            timeout=timeout,
        )
        result = _parse_sip_clients(page.html, page.commands)
        tabbar_id, tab_id = _find_tab_selection(
            page.html,
            "ssipPTabBar",
            "Rufe (eingehend)",
        )
        incoming = self._wait_for_page(
            self._send(["value", tabbar_id, tab_id]),
            lambda html: _contains_class(html, "ssipPICTable"),
            timeout=timeout,
            operation="Rufe (eingehend)",
        )
        result["incoming_calls"] = _parse_sip_incoming_calls(incoming.commands)
        return result

    def backup_save(self, *, timeout: float = 30.0) -> bytes:
        """Trigger a configuration backup and download the resulting file."""
        html = self._navigate(
            "Sicherung / Wiederherstellung",
            lambda page: _contains_widget(page, "aBSaveButton"),
            timeout=timeout,
        )
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
        html = self._navigate(
            "Sicherung / Wiederherstellung",
            lambda page: _contains_widget(page, "aBRestoreButton"),
            timeout=30.0,
        )
        self.upload("/upload?id=backup", filename, data)
        self.click(_find_widget_id(html, "aBRestoreButton"))

    def firmware_update(self, data: bytes, filename: str = "firmware.bin") -> None:
        """Upload a firmware image and trigger applying it."""
        html = self._navigate(
            "Update",
            lambda page: _contains_widget(page, "aUSUpdateButton"),
            timeout=30.0,
        )
        self.upload("/update", filename, data)
        self.click(_find_widget_id(html, "aUSUpdateButton"))
