"""configurationservice client — HTTPS port 4433, used for IP config and device info.

Auth: Authorization: basic <base64(user:password)>  (lowercase "basic")
"""

import base64
import gzip
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from lxml import etree

from pygira import _http as httpx
from pygira.auth import authenticated_request
from pygira.exceptions import (
    DeviceApiError,
    InvalidInputError,
    OperationTimeoutError,
    ProtocolError,
    TransportError,
)
from pygira.models import DeviceInfo, NetworkConfig

NS = "http://service.schema.gira.de/configuration"
NSMAP = {"conf": NS}
_ISCSERVICE_PROTOCOL = "iscwebservice"
_TKS_PROTOCOL = "TKS-IP"


@dataclass(frozen=True)
class TlsConfig:
    """TLS verification and optional SHA-256 certificate pinning."""

    verify: bool = False
    ssl_context: ssl.SSLContext | None = None
    certificate_fingerprint: str | None = None

    @property
    def verify_argument(self) -> bool | ssl.SSLContext:
        """Return the verification value expected by the HTTP transport."""
        return self.ssl_context or self.verify


def _auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"basic {token}"


def _make_client(
    host: str,
    username: str,
    password: str,
    timeout: float = 30.0,
    *,
    tls: TlsConfig | None = None,
) -> httpx.Client:
    tls = tls or TlsConfig()
    return httpx.Client(
        base_url=f"https://{host}:4433",
        headers={"Authorization": _auth_header(username, password)},
        verify=tls.verify_argument,
        certificate_fingerprint=tls.certificate_fingerprint,
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

_TKS_ASSET_MARKER = b"com.gira.tkipgw.web.sites"
_TKS_SSH_PORT = 222
_TKS_SDA_PORT = 50500
_SYSLOG_LINE_RE = re.compile(
    r"^(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+"
    r"(?P<clock>\d{2}:\d{2}:\d{2})\s+[^:]+:\s*(?P<message>.*)$",
)
_MEMORY_RE = re.compile(r"\bMemFree:\s*(\d+)\s+kB\b")
_LOAD_RE = re.compile(
    r"^(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+"
    r"(\d+)/(\d+)\s+\d+$",
)
_SIP_MEMORY_RE = re.compile(r"\bSIPDPID:\s*(\d+)\s+MEMCURR:\s*(\d+)\s*>\s*(\d+)")
_SIP_KEEPALIVE_RE = re.compile(
    r"\bsipdkeepaliveanswer:\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(-?\d+)",
)
_TKS_BUS_STATE_RE = re.compile(r"\bB_GREL_STATE:\s*([0-9A-Fa-f]+)")
_TKS_FAILURE_PATTERNS = {
    "SIP daemon restarted": re.compile(r"\bSIPD RESTART(?:1|2)?\b"),
    "SIP operation failed": re.compile(r"\bError from sipd"),
    "TKS bus acknowledgement failed": re.compile(r"\bGREL .*Failed to receive ACK\b"),
    "TKS bus address unavailable": re.compile(r"\bgetBackupIpgwBusaddr error\b"),
    "web process watchdog stopped": re.compile(r"\btoo many watchdog pings missed\b"),
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


@dataclass(frozen=True)
class TksRuntimeDiagnostics:
    """Resource and daemon signals recovered from the port-80 diagnostic log."""

    observed_at: datetime | None
    free_memory_kib: int | None
    load_averages: tuple[float, float, float] | None
    runnable_tasks: int | None
    total_tasks: int | None
    sip_pid: int | None
    sip_memory_kib: int | None
    sip_memory_limit_kib: int | None
    sip_responsive: bool | None
    sip_observed_at: datetime | None
    tks_bus_state: str | None
    tks_bus_observed_at: datetime | None
    recent_failures: tuple[str, ...]


@dataclass(frozen=True)
class TksDeviceStatus:
    """Passive TKS-IP health snapshot that never starts or logs into the web app."""

    bootstrap_reachable: bool
    identified_as_tks_ip: bool
    http_status: int | None
    device_time: datetime | None
    clock_skew_seconds: float | None
    ssh_reachable: bool
    sda_listener_reachable: bool
    diagnostics: TksRuntimeDiagnostics | None = None
    diagnostics_error: str | None = None


@dataclass
class _RuntimeSignals:
    observed_at: datetime | None = None
    free_memory_kib: int | None = None
    load_averages: tuple[float, float, float] | None = None
    runnable_tasks: int | None = None
    total_tasks: int | None = None
    sip_pid: int | None = None
    sip_memory_kib: int | None = None
    sip_memory_limit_kib: int | None = None
    sip_responsive: bool | None = None
    sip_observed_at: datetime | None = None
    tks_bus_state: str | None = None
    tks_bus_observed_at: datetime | None = None
    failures: list[tuple[datetime, str]] = field(default_factory=list)


def _http_date(headers: dict[str, str]) -> datetime | None:
    value = next(
        (value for name, value in headers.items() if name.casefold() == "date"),
        None,
    )
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc)


def _tcp_reachable(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _syslog_time(match: re.Match[str], reference: datetime) -> datetime | None:
    try:
        parsed = datetime.strptime(
            f"{reference.year} {match['month']} {match['day']} {match['clock']}",
            "%Y %b %d %H:%M:%S",
        ).replace(tzinfo=reference.tzinfo)
    except ValueError:
        return None
    if parsed - reference > timedelta(days=1):
        parsed = parsed.replace(year=parsed.year - 1)
    return parsed


def _read_resource_signals(signals: _RuntimeSignals, message: str, timestamp: datetime) -> None:
    memory_match = _MEMORY_RE.search(message)
    if memory_match:
        signals.free_memory_kib = int(memory_match.group(1))
        signals.observed_at = timestamp

    load_match = _LOAD_RE.match(message)
    if load_match:
        first, second, third = (float(load_match.group(index)) for index in range(1, 4))
        signals.load_averages = (first, second, third)
        signals.runnable_tasks = int(load_match.group(4))
        signals.total_tasks = int(load_match.group(5))
        signals.observed_at = timestamp


def _read_sip_signals(signals: _RuntimeSignals, message: str, timestamp: datetime) -> None:
    memory_match = _SIP_MEMORY_RE.search(message)
    if memory_match:
        signals.sip_pid = int(memory_match.group(1))
        signals.sip_memory_kib = int(memory_match.group(2))
        signals.sip_memory_limit_kib = int(memory_match.group(3))
        signals.sip_observed_at = timestamp

    keepalive_match = _SIP_KEEPALIVE_RE.search(message)
    if keepalive_match:
        reported_pid = int(keepalive_match.group(1))
        expected_pid = int(keepalive_match.group(2))
        result = int(keepalive_match.group(3))
        signals.sip_pid = reported_pid
        signals.sip_responsive = reported_pid == expected_pid and result == 0
        signals.sip_observed_at = timestamp


def _read_bus_and_failure_signals(
    signals: _RuntimeSignals,
    message: str,
    timestamp: datetime,
) -> None:
    bus_match = _TKS_BUS_STATE_RE.search(message)
    if bus_match:
        signals.tks_bus_state = bus_match.group(1).upper()
        signals.tks_bus_observed_at = timestamp
    signals.failures.extend(
        (timestamp, label)
        for label, pattern in _TKS_FAILURE_PATTERNS.items()
        if pattern.search(message)
    )


def _runtime_diagnostics(
    signals: _RuntimeSignals,
    reference: datetime,
    failure_window: timedelta,
) -> TksRuntimeDiagnostics:
    latest_signal = max(
        (
            value
            for value in (
                signals.observed_at,
                signals.sip_observed_at,
                signals.tks_bus_observed_at,
            )
            if value is not None
        ),
        default=None,
    )
    cutoff = (latest_signal or reference) - failure_window
    recent_failures = tuple(
        dict.fromkeys(
            label
            for timestamp, label in signals.failures
            if timestamp >= cutoff
        ),
    )
    return TksRuntimeDiagnostics(
        observed_at=signals.observed_at,
        free_memory_kib=signals.free_memory_kib,
        load_averages=signals.load_averages,
        runnable_tasks=signals.runnable_tasks,
        total_tasks=signals.total_tasks,
        sip_pid=signals.sip_pid,
        sip_memory_kib=signals.sip_memory_kib,
        sip_memory_limit_kib=signals.sip_memory_limit_kib,
        sip_responsive=signals.sip_responsive,
        sip_observed_at=signals.sip_observed_at,
        tks_bus_state=signals.tks_bus_state,
        tks_bus_observed_at=signals.tks_bus_observed_at,
        recent_failures=recent_failures,
    )


def parse_tks_runtime_diagnostics(
    content: bytes,
    *,
    reference_time: datetime | None = None,
    failure_window: timedelta = timedelta(minutes=15),
) -> TksRuntimeDiagnostics:
    """Extract bounded health signals from a decrypted TKS-IP syslog."""
    reference = reference_time or datetime.now(timezone.utc)
    signals = _RuntimeSignals()

    for line in content.decode("utf-8", errors="replace").splitlines():
        line_match = _SYSLOG_LINE_RE.match(line)
        if line_match is None:
            continue
        timestamp = _syslog_time(line_match, reference)
        if timestamp is None:
            continue
        message = line_match["message"]
        _read_resource_signals(signals, message, timestamp)
        _read_sip_signals(signals, message, timestamp)
        _read_bus_and_failure_signals(signals, message, timestamp)

    return _runtime_diagnostics(signals, reference, failure_window)


def get_tks_device_status(
    host: str,
    *,
    timeout: float = 30.0,
    aes_key: str | bytes | None = None,
) -> TksDeviceStatus:
    """Inspect the always-on TKS-IP services without accessing port 8080."""
    bootstrap_reachable = False
    identified_as_tks_ip = False
    http_status: int | None = None
    device_time: datetime | None = None
    clock_skew_seconds: float | None = None
    try:
        with httpx.Client(base_url=f"http://{host}", timeout=min(timeout, 5.0)) as client:
            response = client.get("/")
        bootstrap_reachable = True
        http_status = response.status_code
        identified_as_tks_ip = _TKS_ASSET_MARKER in response.content.lower()
        device_time = _http_date(response.headers)
        if device_time is not None:
            clock_skew_seconds = (device_time - datetime.now(timezone.utc)).total_seconds()
    except httpx.HTTPError:
        pass

    diagnostics = None
    diagnostics_error = None
    if aes_key is not None and bootstrap_reachable:
        try:
            log_content = download_tks_logfile(host, timeout=timeout, aes_key=aes_key)
            diagnostics = parse_tks_runtime_diagnostics(
                log_content,
                reference_time=device_time,
            )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            diagnostics_error = str(exc)

    service_timeout = min(timeout, 2.0)
    return TksDeviceStatus(
        bootstrap_reachable=bootstrap_reachable,
        identified_as_tks_ip=identified_as_tks_ip,
        http_status=http_status,
        device_time=device_time,
        clock_skew_seconds=clock_skew_seconds,
        ssh_reachable=_tcp_reachable(host, _TKS_SSH_PORT, service_timeout),
        sda_listener_reachable=_tcp_reachable(host, _TKS_SDA_PORT, service_timeout),
        diagnostics=diagnostics,
        diagnostics_error=diagnostics_error,
    )


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


_GZIP_MAGIC = b"\x1f\x8b"
_AES_192_KEY_BYTES = 24
_AES_BLOCK_BYTES = 16


def _tks_aes_key_bytes(aes_key: str | bytes) -> bytes:
    """Decode a 24-byte text key or its 48-character hexadecimal form."""
    if isinstance(aes_key, bytes):
        key = aes_key
    elif re.fullmatch(r"[0-9a-fA-F]{48}", aes_key):
        key = bytes.fromhex(aes_key)
    else:
        key = aes_key.encode()
    if len(key) != _AES_192_KEY_BYTES:
        msg = "TKS-IP AES key must be 24 bytes of text or 48 hexadecimal characters"
        raise InvalidInputError(msg)
    return key


def decrypt_tks_logfile(content: bytes, aes_key: str | bytes) -> bytes:
    """Decrypt the firmware's AES-192-ECB logfile format and remove zero padding."""
    if len(content) % _AES_BLOCK_BYTES:
        msg = "TKS-IP encrypted logfile length must be a multiple of 16 bytes"
        raise InvalidInputError(msg)
    decryptor = Cipher(algorithms.AES(_tks_aes_key_bytes(aes_key)), modes.ECB()).decryptor()
    return (decryptor.update(content) + decryptor.finalize()).rstrip(b"\0")


def download_tks_logfile(
    host: str,
    *,
    timeout: float = 30.0,
    aes_key: str | bytes | None = None,
) -> bytes:
    """Download the diagnostic log file from the always-on bootstrap daemon.

    Unauthenticated GET on port 80 (`/getlogfile`). The daemon assembles the
    file on demand and gives up on its own after an internal timeout (firmware
    string: "timeout creatng downloadable logfile" — typo present in the
    firmware), which is what previously made this endpoint unsafe to call
    without a bound; the request timeout here is the only bound needed on the
    client side.

    Confirmed live (2026-07-20, apartment 4 gateway): the response body is
    itself a gzip stream with **no** `Content-Encoding` header announcing it
    — detected here by magic bytes instead. Its contents are AES-192-ECB
    ciphertext with firmware-added zero padding. When ``aes_key`` is provided,
    this function returns decrypted syslog text; otherwise it preserves the
    historical behavior of returning the ciphertext after removing gzip.
    """
    with httpx.Client(base_url=f"http://{host}", timeout=timeout) as client:
        resp = client.get("/getlogfile")
        resp.raise_for_status()
    content = resp.content
    if content[:2] == _GZIP_MAGIC:
        content = gzip.decompress(content)
    if aes_key is not None:
        content = decrypt_tks_logfile(content, aes_key)
    return content


def _start_tks_webinterface(host: str, timeout: float) -> float:
    started = time.monotonic()
    hook_timeout = min(timeout, 15.0)
    try:
        with httpx.Client(base_url=f"http://{host}", timeout=hook_timeout) as client:
            resp = client.get(
                "/json",
                params={"sid": "undefined", "rid": "undefined", "data": '["documentReady"]'},
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        msg = f"Could not activate the TKS-IP web interface at {host}: {exc}"
        raise TransportError(msg) from exc
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
            return last_state, httpx.HTTPError(
                f"HTTP {resp.status_code}",
                status_code=resp.status_code,
            )
        state = _parse_tks_state(resp.content)
        if state == _TKS_ERROR_STATE:
            msg = "TKS-IP web interface reported error state 2"
            raise ProtocolError(_TKS_PROTOCOL, "activate web interface", state, msg)
    except httpx.HTTPError as exc:
        return last_state, exc
    else:
        return state, last_error


def _raise_tks_activation_error(last_state: str | None, last_error: Exception | None) -> None:
    if last_state is not None:
        msg = f"TKS-IP web interface did not start; last state was {last_state!r}"
        raise OperationTimeoutError(msg)
    if last_error is not None:
        msg = f"TKS-IP web interface did not start: {last_error}"
        raise OperationTimeoutError(msg) from last_error
    msg = "TKS-IP web interface did not start"
    raise OperationTimeoutError(msg)


def activate_tks_webinterface(
    host: str,
    *,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
) -> TksWebInterfaceActivation:
    """Start the TKS-IP web interface on port 8080 via the port-80 bootstrap hook."""
    if timeout <= 0:
        msg = "timeout must be positive"
        raise InvalidInputError(msg)
    if poll_interval <= 0:
        msg = "poll_interval must be positive"
        raise InvalidInputError(msg)
    started = _start_tks_webinterface(host, timeout)
    return _poll_tks_webinterface(host, started, timeout, poll_interval)


def get_device_xml(
    host: str,
    username: str,
    password: str,
    timeout: float = 30.0,
    *,
    tls: TlsConfig | None = None,
) -> etree._Element:
    """Fetch and parse the device configuration XML from the configurationservice."""
    with _make_client(host, username, password, timeout=timeout, tls=tls) as client:
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


def push_device_xml(  # noqa: PLR0913 - explicit connection and TLS options
    host: str,
    username: str,
    password: str,
    root: etree._Element,
    timeout: float = 30.0,
    *,
    tls: TlsConfig | None = None,
) -> None:
    """Upload the modified configuration XML back to the device via PUT."""
    body = etree.tostring(root, xml_declaration=True, encoding="utf-8")
    with _make_client(host, username, password, timeout=timeout, tls=tls) as client:
        resp = client.put(
            "/service",
            content=body,
            headers={"Content-Type": "text/xml; charset=utf-8"},
        )
        resp.raise_for_status()


def download_logs(
    host: str,
    username: str,
    password: str,
    timeout: float = 30.0,
    *,
    tls: TlsConfig | None = None,
) -> bytes:
    """Download the diagnostic log bundle from the device."""
    with _make_client(host, username, password, timeout=timeout, tls=tls) as client:
        resp = client.get("/discovery/download/logfiles")
        resp.raise_for_status()
    return resp.content


def _x1_session_request(
    client: httpx.Client,
    username: str,
    password: str,
    command: str,
    data: dict | None = None,
) -> dict:
    return authenticated_request(
        client,
        "/webservice",
        username,
        password,
        command,
        data,
    )


def _make_x1_client(host: str, timeout: float, tls: TlsConfig | None) -> httpx.Client:
    tls = tls or TlsConfig()
    return httpx.Client(
        base_url=f"https://{host}",
        verify=tls.verify_argument,
        certificate_fingerprint=tls.certificate_fingerprint,
        timeout=timeout,
    )


def download_logs_x1(
    host: str,
    username: str,
    password: str,
    timeout: float = 30.0,
    *,
    tls: TlsConfig | None = None,
) -> bytes:
    """Download log bundle from X1 via /webservice session auth."""
    with _make_x1_client(host, timeout, tls) as client:
        logs_data = _x1_session_request(client, username, password, "getLogfile")
        content_b64 = (logs_data.get("data") or {}).get("content")
        if not content_b64:
            raise ProtocolError(
                _ISCSERVICE_PROTOCOL,
                "getLogfile",
                "missing-content",
                logs_data,
            )
        return base64.b64decode(content_b64)


def set_syslog_severity_x1(  # noqa: PLR0913 - explicit connection and TLS options
    host: str,
    username: str,
    password: str,
    severity: int,
    timeout: float = 30.0,
    *,
    tls: TlsConfig | None = None,
) -> None:
    """Set X1 syslog severity (0..4), where 4 disables extended logging."""
    with _make_x1_client(host, timeout, tls) as client:
        resp_data = _x1_session_request(
            client,
            username,
            password,
            "setSyslogSeverity",
            {"syslogSeverity": severity},
        )
        if isinstance(resp_data, dict) and resp_data.get("error"):
            command = "setSyslogSeverity"
            raise DeviceApiError(command, resp_data)


def get_syslog_severity_x1(
    host: str,
    username: str,
    password: str,
    timeout: float = 30.0,
    *,
    tls: TlsConfig | None = None,
) -> int:
    """Read current X1 syslog severity from device info."""
    with _make_x1_client(host, timeout, tls) as client:
        resp_data = _x1_session_request(client, username, password, "getDeviceInfo")
        data = (resp_data or {}).get("data") or {}
        value = data.get("SyslogSeverity")
        if value is None:
            raise ProtocolError(
                _ISCSERVICE_PROTOCOL,
                "getDeviceInfo",
                "missing-field",
                "response did not include SyslogSeverity",
            )
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            msg = f"Invalid SyslogSeverity value: {value!r}"
            raise ProtocolError(
                _ISCSERVICE_PROTOCOL,
                "getDeviceInfo",
                "invalid-field",
                msg,
            ) from exc
