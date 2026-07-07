"""configurationservice client — HTTPS port 4433, used for IP config and device info.

Auth: Authorization: basic <base64(user:password)>  (lowercase "basic")
"""

import base64
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any, cast

from lxml import etree

from pygira import _http as httpx
from pygira.models import DeviceInfo, NetworkConfig

NS = "http://service.schema.gira.de/configuration"
NSMAP = {"conf": NS}


def _auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"basic {token}"


def _make_client(host: str, username: str, password: str, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        base_url=f"https://{host}:4433",
        headers={"Authorization": _auth_header(username, password)},
        verify=False,
        timeout=timeout,
    )


@dataclass(frozen=True)
class TksWebInterfaceActivation:
    """Result of starting the TKS-IP web interface on port 8080."""

    state: str
    url: str
    elapsed_seconds: float


HTTP_ERROR_STATUS = 400

_TKS_STARTED_STATE = "0"
_TKS_ERROR_STATE = "2"

_APP_STATE_DESCRIPTIONS = {
    "0": "ready",
    "1": "starting",
    "2": "error",
    "3": "starting (reboot)",
    "4": "suspended (active video call)",
}


def _parse_tks_state(content: bytes) -> str | None:
    text = content.decode("utf-8", "replace")
    match = re.search(r'"system\.state"\s*:\s*"([^"]+)"', text)
    return match.group(1) if match else None


@dataclass(frozen=True)
class TksStatus:
    """Read-only TKS-IP gateway health snapshot."""

    bootstrap_reachable: bool
    app_running: bool
    state_code: str | None
    state_description: str | None


def get_tks_status(host: str, *, timeout: float = 10.0) -> TksStatus:
    """Check TKS-IP gateway status without starting the on-demand web app.

    Unlike activate_tks_webinterface, this never sends the documentReady hook —
    port 8080 is only probed passively, so an app killed for inactivity is
    correctly reported as not running rather than being restarted.
    """
    bootstrap_reachable = False
    try:
        with httpx.Client(base_url=f"http://{host}", timeout=timeout) as client:
            client.get("/")
        bootstrap_reachable = True
    except httpx.HTTPError:
        pass

    state_code = None
    try:
        with httpx.Client(base_url=f"http://{host}:8080", timeout=timeout) as client:
            resp = client.get("/state", params={"callback": "setState"})
            state_code = _parse_tks_state(resp.content)
    except httpx.HTTPError:
        pass

    return TksStatus(
        bootstrap_reachable=bootstrap_reachable,
        app_running=state_code is not None,
        state_code=state_code,
        state_description=_APP_STATE_DESCRIPTIONS.get(state_code, state_code)
        if state_code
        else None,
    )


def _start_tks_webinterface(host: str, timeout: float) -> float:
    started = time.monotonic()
    hook_timeout = min(timeout, 15.0)
    with httpx.Client(base_url=f"http://{host}", timeout=hook_timeout) as client:
        resp = client.get(
            "/json",
            params={"sid": "undefined", "rid": "undefined", "data": '["documentReady"]'},
        )
        resp.raise_for_status()
    return started


def _poll_tks_webinterface(
    host: str,
    started: float,
    timeout: float,
    poll_interval: float,
) -> TksWebInterfaceActivation:
    deadline = started + timeout
    last_state: str | None = None
    last_error: Exception | None = None
    with httpx.Client(base_url=f"http://{host}:8080", timeout=min(poll_interval, 5.0)) as client:
        while True:
            last_state, last_error = _poll_tks_state(client, last_state, last_error)
            if last_state == _TKS_STARTED_STATE:
                return TksWebInterfaceActivation(
                    state=last_state,
                    url=f"http://{host}:8080/",
                    elapsed_seconds=time.monotonic() - started,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _raise_tks_activation_error(last_state, last_error)
            time.sleep(min(poll_interval, remaining))


def _poll_tks_state(
    client: httpx.Client,
    last_state: str | None,
    last_error: Exception | None,
) -> tuple[str | None, Exception | None]:
    try:
        resp = client.get("/state", params={"callback": "setState"})
        if resp.status_code >= HTTP_ERROR_STATUS:
            return last_state, httpx.HTTPError(f"HTTP {resp.status_code}")
        state = _parse_tks_state(resp.content)
        if state == _TKS_ERROR_STATE:
            msg = "TKS-IP web interface reported error state 2"
            raise RuntimeError(msg)
    except httpx.HTTPError as exc:
        return last_state, exc
    else:
        return state, last_error


def _raise_tks_activation_error(last_state: str | None, last_error: Exception | None) -> None:
    if last_state is not None:
        msg = f"TKS-IP web interface did not start; last state was {last_state!r}"
        raise RuntimeError(msg)
    if last_error is not None:
        msg = f"TKS-IP web interface did not start: {last_error}"
        raise RuntimeError(msg) from last_error
    msg = "TKS-IP web interface did not start"
    raise RuntimeError(msg)


def activate_tks_webinterface(
    host: str,
    *,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
) -> TksWebInterfaceActivation:
    """Start the TKS-IP web interface on port 8080 via the port-80 bootstrap hook."""
    if timeout <= 0:
        msg = "timeout must be positive"
        raise ValueError(msg)
    if poll_interval <= 0:
        msg = "poll_interval must be positive"
        raise ValueError(msg)
    started = _start_tks_webinterface(host, timeout)
    return _poll_tks_webinterface(host, started, timeout, poll_interval)


def get_device_xml(
    host: str,
    username: str,
    password: str,
    timeout: float = 30.0,
) -> etree._Element:
    """Fetch and parse the device configuration XML from the configurationservice."""
    with _make_client(host, username, password, timeout=timeout) as client:
        resp = client.get("/service")
        resp.raise_for_status()
    return etree.fromstring(resp.content)


def parse_device_info(root: etree._Element) -> DeviceInfo:
    """Extract device identity and network settings from the configuration XML root."""

    def get(tag: str) -> str:
        el = root.find(f"conf:{tag}", NSMAP)
        return (el.text or "").strip() if el is not None else ""

    return DeviceInfo(
        firmware_version=get("FirmwareVersion"),
        mac_address=get("MacAddress"),
        ip_address=get("IpAddress"),
        subnet_mask=get("SubnetMask"),
        default_gateway=get("DefaultGateway"),
        primary_dns=get("PrimaryDNS"),
        secondary_dns=get("SecondaryDNS"),
        dhcp=(get("DHCP").lower() == "true"),
        device_name=get("LogicalName"),
        entity_id=get("EntityId"),
    )


def set_ip_config(root: etree._Element, cfg: NetworkConfig) -> None:
    """Mutate the configuration XML root in-place with the given network settings."""

    def set_text(tag: str, value: str) -> None:
        el = root.find(f"conf:{tag}", NSMAP)
        if el is not None:
            el.text = value

    set_text("DHCP", "true" if cfg.dhcp else "false")
    if not cfg.dhcp:
        set_text("IpAddress", cfg.ip_address)
        set_text("SubnetMask", cfg.subnet_mask)
        set_text("DefaultGateway", cfg.default_gateway)
        set_text("PrimaryDNS", cfg.primary_dns)
        set_text("SecondaryDNS", cfg.secondary_dns)


def push_device_xml(
    host: str,
    username: str,
    password: str,
    root: etree._Element,
    timeout: float = 30.0,
) -> None:
    """Upload the modified configuration XML back to the device via PUT."""
    body = etree.tostring(root, xml_declaration=True, encoding="utf-8")
    with _make_client(host, username, password, timeout=timeout) as client:
        resp = client.put(
            "/service",
            content=body,
            headers={"Content-Type": "text/xml; charset=utf-8"},
        )
        resp.raise_for_status()


def download_logs(host: str, username: str, password: str, timeout: float = 30.0) -> bytes:
    """Download the diagnostic log bundle from the device."""
    with _make_client(host, username, password, timeout=timeout) as client:
        resp = client.get("/discovery/download/logfiles")
        resp.raise_for_status()
    return resp.content


def _ws_payload(command: str, data: dict | None = None) -> dict:
    payload = {"command": command, "keepAlive": True}
    if data is not None:
        payload["data"] = data
    return payload


def _sha256_hex_upper(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest().upper()


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _gds1_password_hash(password: str, salt: str) -> str:
    # Web UI implementation for version "GDS_1":
    # base64(sha256(utf8(password)+salt)).substring(0, 43)
    digest = hashlib.sha256((password + salt).encode("utf-8")).digest()
    return base64.b64encode(digest).decode()[:43]


def _legacy_password_hash(password: str, salt: str) -> str:
    # version "1": sha256(sha256(password) + "+" + salt)
    first = _sha256_hex(password)
    return _sha256_hex(f"{first}+{salt}")


def _compute_auth_token(password: str, salt: str, session_salt: str, version: str) -> str:
    if version == "GDS_1":
        password_hash = _gds1_password_hash(password, salt)
    else:
        password_hash = _legacy_password_hash(password, salt)
    return _sha256_hex_upper(f"{password_hash}+{session_salt}")


def _x1_session_request(
    client: httpx.Client,
    username: str,
    password: str,
    command: str,
    data: dict | None = None,
) -> dict:
    salt_resp = client.post(
        "/webservice",
        json=_ws_payload("getPasswordSalt", {"username": username}),
    )
    salt_resp.raise_for_status()
    salt_data = cast("dict[str, Any]", salt_resp.json())
    session_data = salt_data.get("data") or {}
    salt = session_data.get("salt")
    session_salt = session_data.get("sessionSalt")
    version = session_data.get("version", "1")
    if not salt or not session_salt:
        error = salt_data.get("error", "unknown")
        error_id = salt_data.get("id", "n/a")
        msg = f"X1 session init failed ({error}/{error_id})"
        raise RuntimeError(msg)

    token = _compute_auth_token(password, salt, session_salt, version)
    auth_resp = client.post(
        "/webservice",
        json=_ws_payload("doAuthenticateSession", {"username": username, "token": token}),
    )
    auth_resp.raise_for_status()
    auth_data = cast("dict[str, Any]", auth_resp.json())
    if isinstance(auth_data, dict) and auth_data.get("error"):
        error = auth_data.get("error", "unknown")
        error_id = auth_data.get("id", "n/a")
        msg = f"X1 authentication failed ({error}/{error_id})"
        raise RuntimeError(msg)

    resp = client.post("/webservice", json=_ws_payload(command, data))
    resp.raise_for_status()
    return cast("dict[str, Any]", resp.json())


def download_logs_x1(host: str, username: str, password: str, timeout: float = 30.0) -> bytes:
    """Download log bundle from X1 via /webservice session auth."""
    with httpx.Client(base_url=f"https://{host}", verify=False, timeout=timeout) as client:
        logs_data = _x1_session_request(client, username, password, "getLogfile")
        content_b64 = (logs_data.get("data") or {}).get("content")
        if not content_b64:
            error = logs_data.get("error", "unknown")
            error_id = logs_data.get("id", "n/a")
            msg = f"X1 logfile fetch failed ({error}/{error_id})"
            raise RuntimeError(msg)
        return base64.b64decode(content_b64)


def set_syslog_severity_x1(
    host: str,
    username: str,
    password: str,
    severity: int,
    timeout: float = 30.0,
) -> None:
    """Set X1 syslog severity (0..4), where 4 disables extended logging."""
    with httpx.Client(base_url=f"https://{host}", verify=False, timeout=timeout) as client:
        resp_data = _x1_session_request(
            client,
            username,
            password,
            "setSyslogSeverity",
            {"syslogSeverity": severity},
        )
        if isinstance(resp_data, dict) and resp_data.get("error"):
            error = resp_data.get("error", "unknown")
            error_id = resp_data.get("id", "n/a")
            msg = f"X1 setSyslogSeverity failed ({error}/{error_id})"
            raise RuntimeError(msg)


def get_syslog_severity_x1(host: str, username: str, password: str, timeout: float = 30.0) -> int:
    """Read current X1 syslog severity from device info."""
    with httpx.Client(base_url=f"https://{host}", verify=False, timeout=timeout) as client:
        resp_data = _x1_session_request(client, username, password, "getDeviceInfo")
        data = (resp_data or {}).get("data") or {}
        value = data.get("SyslogSeverity")
        if value is None:
            msg = "X1 getDeviceInfo did not include SyslogSeverity"
            raise RuntimeError(msg)
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            msg = f"Invalid SyslogSeverity value: {value!r}"
            raise RuntimeError(msg) from exc
