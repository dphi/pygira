"""Tests for the external Baresip monitor adapter."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pygira.baresip_monitor import (
    BaresipMonitor,
    _account_parameter,
    _module_path,
    _netstring,
    _read_netstring,
)
from pygira.exceptions import DependencyUnavailableError, InvalidInputError, ProtocolError

HOST = "192.0.2.10"


class _ControlSocket:
    def __init__(self) -> None:
        self.received = bytearray()
        self.sent: list[bytes] = []
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)
        colon = data.index(b":")
        request = json.loads(data[colon + 1 : -1])
        event = {
            "event": True,
            "class": "ua",
            "type": "REGISTER_OK",
            "accountaor": f"sip:monitor@{HOST}",
        }
        response = {
            "response": True,
            "ok": True,
            "data": "",
            "token": request["token"],
        }
        self.received.extend(_netstring(json.dumps(event).encode()))
        self.received.extend(_netstring(json.dumps(response).encode()))

    def recv(self, size: int) -> bytes:
        chunk = bytes(self.received[:size])
        del self.received[:size]
        return chunk

    def settimeout(self, timeout: float) -> None:
        assert timeout > 0

    def close(self) -> None:
        self.closed = True


def test_baresip_monitor_keeps_credentials_out_of_files_and_process_arguments() -> None:
    control = _ControlSocket()
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 0
    password = "monitor-secret"

    with (
        patch("pygira.baresip_monitor.shutil.which", return_value="/usr/bin/baresip"),
        patch(
            "pygira.baresip_monitor._module_path",
            return_value=Path("/usr/lib/baresip/modules"),
        ),
        patch("pygira.baresip_monitor._reserve_control_port", return_value=4567),
        patch("pygira.baresip_monitor.socket.create_connection", return_value=control),
        patch("pygira.baresip_monitor.subprocess.Popen", return_value=process) as popen,
        BaresipMonitor(HOST, "monitor", password) as monitor,
    ):
        directory = Path(monitor._temporary_directory.name)  # noqa: SLF001
        config = (directory / "config").read_text()
        events = monitor.events()
        event = next(events)

        assert "ctrl_tcp_listen 127.0.0.1:4567" in config
        assert "module_path /usr/lib/baresip/modules" in config
        assert password not in config
        assert not (directory / "accounts").exists()
        assert password not in (directory / "baresip.log").read_text()
        assert event.type == "REGISTER_OK"

    command = popen.call_args.args[0]
    assert command == ["/usr/bin/baresip", "-4", "-c", "-f", str(directory)]
    assert password not in repr(command)
    assert control.closed is True
    process.terminate.assert_called_once()


def test_baresip_monitor_reports_missing_executable() -> None:
    with (
        patch("pygira.baresip_monitor.shutil.which", return_value=None),
        pytest.raises(DependencyUnavailableError, match="brew install baresip"),
    ):
        BaresipMonitor(HOST, "monitor", "monitor-secret").start()


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("monitor@example", "secret"),
        ("monitor", "bad password"),
        ("monitor", "bad;password"),
        ("monitor", 'bad"password'),
    ],
)
def test_account_parameter_rejects_configuration_injection(
    username: str,
    password: str,
) -> None:
    with pytest.raises(InvalidInputError):
        _account_parameter(HOST, username, password)


def test_monitor_validates_host_and_startup_timeout() -> None:
    with pytest.raises(InvalidInputError, match="gateway host"):
        BaresipMonitor("unsafe host", "monitor", "monitor-secret")
    with pytest.raises(InvalidInputError, match="startup timeout"):
        BaresipMonitor(HOST, "monitor", "monitor-secret", startup_timeout=0)


def test_module_path_requires_the_baresip_modules(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "baresip"
    executable.parent.mkdir()
    executable.touch()
    modules = tmp_path / "lib" / "baresip" / "modules"
    modules.mkdir(parents=True)
    for module in ("account.so", "ctrl_tcp.so", "g711.so", "menu.so"):
        (modules / module).touch()

    assert _module_path(str(executable)) == modules

    (modules / "menu.so").unlink()
    with pytest.raises(DependencyUnavailableError, match="could not locate"):
        _module_path(str(executable))


def test_monitor_reports_invalid_control_messages() -> None:
    monitor = BaresipMonitor(HOST, "monitor", "monitor-secret")
    with pytest.raises(ProtocolError, match="not running"):
        monitor._decode_message()  # noqa: SLF001

    control = _ControlSocket()
    monitor._control = control  # noqa: SLF001
    control.received.extend(_netstring(b"{"))
    with pytest.raises(ProtocolError, match="invalid JSON"):
        monitor._decode_message()  # noqa: SLF001

    control.received.extend(_netstring(b"[]"))
    with pytest.raises(ProtocolError, match="non-object"):
        monitor._decode_message()  # noqa: SLF001


def test_netstring_reader_rejects_missing_trailer() -> None:
    control = _ControlSocket()
    control.received.extend(b"2:{}!")

    with pytest.raises(ProtocolError, match="trailing comma"):
        _read_netstring(control)  # type: ignore[arg-type]
