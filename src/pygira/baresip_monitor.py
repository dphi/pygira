"""Passive SIP event monitoring through the external Baresip user agent."""

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from pygira.exceptions import DependencyUnavailableError, InvalidInputError, ProtocolError

_CONTROL_HOST = "127.0.0.1"
_MAX_CONTROL_MESSAGE_BYTES = 1024 * 1024
_MAX_NETSTRING_LENGTH_DIGITS = 9
_POLL_INTERVAL_SECONDS = 0.05
_SAFE_SIP_USERNAME = re.compile(r"^[A-Za-z0-9_.+-]+$")
_UNSAFE_ACCOUNT_VALUE = re.compile(r'[\s;<>"\\]')
_PROTOCOL = "Baresip"
_CONFIG_MODE = 0o600
_CONFIG_DIR_MODE = 0o700


@dataclass(frozen=True)
class SipMonitorEvent:
    """One structured event emitted by Baresip's control interface."""

    type: str
    event_class: str
    peer_uri: str | None = None
    peer_display_name: str | None = None
    call_id: str | None = None
    detail: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SipMonitorEvent":
        """Create an event from a decoded Baresip control message."""
        return cls(
            type=str(payload.get("type", "")),
            event_class=str(payload.get("class", "")),
            peer_uri=_optional_string(payload.get("peeruri")),
            peer_display_name=_optional_string(payload.get("peerdisplayname")),
            call_id=_optional_string(payload.get("id")),
            detail=_optional_string(payload.get("param")),
        )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _validate_account_values(host: str, username: str, password: str) -> None:
    if not host or _UNSAFE_ACCOUNT_VALUE.search(host):
        msg = "SIP gateway host contains characters that cannot be represented safely"
        raise InvalidInputError(msg)
    if not _SAFE_SIP_USERNAME.fullmatch(username):
        msg = "SIP username may contain letters, digits, dot, underscore, plus, and hyphen"
        raise InvalidInputError(msg)
    if not password or _UNSAFE_ACCOUNT_VALUE.search(password):
        msg = (
            "SIP password must be non-empty and contain no whitespace, semicolon, "
            "angle quote, quote, or backslash"
        )
        raise InvalidInputError(msg)


def _account_parameter(host: str, username: str, password: str) -> str:
    _validate_account_values(host, username, password)
    return (
        f"<sip:{username}@{host};transport=udp>"
        f";auth_user={username};auth_pass={password}"
        ";answermode=manual;inreq_allowed=yes;regint=300"
        ";audio_codecs=pcma,pcmu"
    )


def _reserve_control_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((_CONTROL_HOST, 0))
        return int(listener.getsockname()[1])


def _module_path(executable: str) -> Path:
    prefix = Path(executable).resolve().parent.parent
    candidates = (
        prefix / "lib" / "baresip" / "modules",
        prefix / "lib64" / "baresip" / "modules",
    )
    required = ("account.so", "ctrl_tcp.so", "g711.so", "menu.so")
    for candidate in candidates:
        if all((candidate / module).is_file() for module in required):
            return candidate
    locations = ", ".join(str(path) for path in candidates)
    msg = f"could not locate required Baresip modules below: {locations}"
    raise DependencyUnavailableError(msg)


def _netstring(payload: bytes) -> bytes:
    return str(len(payload)).encode("ascii") + b":" + payload + b","


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            msg = "Baresip closed its control connection"
            raise ProtocolError(_PROTOCOL, "read event", "closed", msg)
        chunks.extend(chunk)
    return bytes(chunks)


def _read_netstring(sock: socket.socket) -> bytes:
    length_bytes = bytearray()
    while True:
        char = _read_exact(sock, 1)
        if char == b":":
            break
        if not char.isdigit() or len(length_bytes) >= _MAX_NETSTRING_LENGTH_DIGITS:
            msg = "invalid netstring length"
            raise ProtocolError(_PROTOCOL, "read event", "invalid-frame", msg)
        length_bytes.extend(char)
    if not length_bytes:
        msg = "empty netstring length"
        raise ProtocolError(_PROTOCOL, "read event", "invalid-frame", msg)
    length = int(length_bytes)
    if length > _MAX_CONTROL_MESSAGE_BYTES:
        msg = f"control message exceeds {_MAX_CONTROL_MESSAGE_BYTES} bytes"
        raise ProtocolError(_PROTOCOL, "read event", "oversized-frame", msg)
    payload = _read_exact(sock, length)
    if _read_exact(sock, 1) != b",":
        msg = "netstring is missing its trailing comma"
        raise ProtocolError(_PROTOCOL, "read event", "invalid-frame", msg)
    return payload


class BaresipMonitor:
    """Run Baresip in an isolated directory and expose its structured events."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        executable: str = "baresip",
        startup_timeout: float = 10.0,
    ) -> None:
        """Configure a passive, manual-answer SIP registration."""
        _validate_account_values(host, username, password)
        if startup_timeout <= 0:
            msg = "Baresip startup timeout must be greater than zero"
            raise InvalidInputError(msg)
        self.host = host
        self.username = username
        self._password = password
        self.executable = executable
        self.startup_timeout = startup_timeout
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._control: socket.socket | None = None
        self._pending_events: deque[SipMonitorEvent] = deque()

    def __enter__(self) -> "BaresipMonitor":
        """Start Baresip and establish the monitoring registration."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop Baresip and erase its temporary configuration."""
        self.close()

    def _write_config(
        self,
        directory: Path,
        control_port: int,
        module_path: Path,
    ) -> Path:
        config = (
            "sip_listen 0.0.0.0:0\n"
            f"module_path {module_path}\n"
            "module g711.so\n"
            "module_app account.so\n"
            "module_app menu.so\n"
            "module_app ctrl_tcp.so\n"
            f"ctrl_tcp_listen {_CONTROL_HOST}:{control_port}\n"
        )
        config_path = directory / "config"
        config_path.write_text(config, encoding="utf-8")
        config_path.chmod(_CONFIG_MODE)
        accounts_path = directory / "accounts"
        # Baresip's account module only loads registration credentials from this file.
        # It is mode 0600 inside a mode-0700 temporary directory, is never logged or
        # passed as a process argument, and is unlinked immediately after startup.
        accounts_path.write_text(
            # codeql[py/clear-text-storage-sensitive-data]
            _account_parameter(self.host, self.username, self._password) + "\n",
            encoding="utf-8",
        )
        accounts_path.chmod(_CONFIG_MODE)
        return accounts_path

    def _startup_log(self) -> str:
        if self._temporary_directory is None:
            return ""
        log_path = Path(self._temporary_directory.name) / "baresip.log"
        try:
            return log_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""

    def _connect(self, control_port: int) -> socket.socket:
        assert self._process is not None
        deadline = time.monotonic() + self.startup_timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            return_code = self._process.poll()
            if return_code is not None:
                detail = self._startup_log() or f"process exited with status {return_code}"
                raise ProtocolError(_PROTOCOL, "start", return_code, detail)
            try:
                control = socket.create_connection(
                    (_CONTROL_HOST, control_port),
                    timeout=_POLL_INTERVAL_SECONDS,
                )
            except OSError as exc:
                last_error = exc
                time.sleep(_POLL_INTERVAL_SECONDS)
            else:
                control.settimeout(1.0)
                return control
        detail = f"control socket did not start within {self.startup_timeout:g}s"
        if last_error is not None:
            detail += f": {last_error}"
        startup_log = self._startup_log()
        if startup_log:
            detail += f"\n{startup_log}"
        raise ProtocolError(_PROTOCOL, "start", "timeout", detail)

    def _decode_message(self) -> dict[str, Any]:
        if self._control is None:
            msg = "monitor is not running"
            raise ProtocolError(_PROTOCOL, "read event", "not-started", msg)
        payload = _read_netstring(self._control)
        try:
            decoded = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            msg = "control socket returned invalid JSON"
            raise ProtocolError(_PROTOCOL, "read event", "invalid-json", msg) from exc
        if not isinstance(decoded, dict):
            msg = "control socket returned a non-object JSON message"
            raise ProtocolError(_PROTOCOL, "read event", "invalid-json", msg)
        return decoded

    def _send_command(self, command: str, params: str | None = None) -> str:
        if self._control is None:
            msg = "monitor is not running"
            raise ProtocolError(_PROTOCOL, command, "not-started", msg)
        token = uuid.uuid4().hex
        request: dict[str, object] = {"command": command, "token": token}
        if params is not None:
            request["params"] = params
        self._control.sendall(_netstring(json.dumps(request).encode()))
        while True:
            message = self._decode_message()
            if message.get("event") is True:
                self._pending_events.append(SipMonitorEvent.from_payload(message))
                continue
            if message.get("response") is not True or message.get("token") != token:
                continue
            if message.get("ok") is not True:
                detail = str(message.get("data") or "command failed")
                raise ProtocolError(_PROTOCOL, command, "rejected", detail)
            return str(message.get("data") or "")

    def start(self) -> None:
        """Start the external user agent and create an in-memory account."""
        if self._process is not None:
            return
        executable = shutil.which(self.executable)
        if executable is None:
            msg = (
                "baresip executable not found; install Baresip 4.x "
                "(macOS/Linuxbrew: brew install baresip)"
            )
            raise DependencyUnavailableError(msg)

        temporary_directory = tempfile.TemporaryDirectory(prefix="pygira-baresip-")
        self._temporary_directory = temporary_directory
        directory = Path(temporary_directory.name)
        directory.chmod(_CONFIG_DIR_MODE)
        control_port = _reserve_control_port()
        accounts_path = self._write_config(
            directory,
            control_port,
            _module_path(executable),
        )
        log_path = directory / "baresip.log"
        log_fd = os.open(log_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, _CONFIG_MODE)
        try:
            with os.fdopen(log_fd, "wb") as log:
                self._process = subprocess.Popen(  # noqa: S603 - resolved executable, fixed args
                    [executable, "-4", "-c", "-f", str(directory)],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            self._control = self._connect(control_port)
            accounts_path.unlink(missing_ok=True)
            self._password = ""
            self._send_command("uareg", "300")
        except BaseException:
            self.close()
            raise

    def events(self) -> Iterator[SipMonitorEvent]:
        """Yield Baresip events until the process exits or the caller stops."""
        if self._process is None or self._control is None:
            msg = "monitor is not running"
            raise ProtocolError(_PROTOCOL, "read event", "not-started", msg)
        while self._pending_events:
            yield self._pending_events.popleft()
        while True:
            try:
                message = self._decode_message()
            except TimeoutError:
                return_code = self._process.poll()
                if return_code is not None:
                    detail = self._startup_log() or f"process exited with status {return_code}"
                    raise ProtocolError(_PROTOCOL, "monitor", return_code, detail) from None
                continue
            if message.get("event") is True:
                yield SipMonitorEvent.from_payload(message)

    def close(self) -> None:
        """Stop the child process and erase all temporary state."""
        if self._control is not None:
            try:
                self._control.close()
            finally:
                self._control = None
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=3)
            self._process = None
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None
        self._password = ""
