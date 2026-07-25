"""CLI coverage for maintenance and TKS integration commands."""

import asyncio
import io
import zipfile
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from pygira.cli import main
from pygira.config_service import (
    TksDeviceStatus,
    TksRuntimeDiagnostics,
    TksWebInterfaceActivation,
)
from pygira.devices.g1 import PROFILE as G1_PROFILE
from pygira.exceptions import OperationTimeoutError
from pygira.gds import GdsClient
from pygira.models import WeatherStation

HOST = "192.0.2.10"
CREDS = ["--ip", HOST, "--username", "device", "--password", "secret"]


def _login(profile: object = G1_PROFILE) -> tuple[object, str, str, str]:
    return profile, HOST, "device", "secret"


def _run_gds(
    host: str,
    username: str,
    password: str,
    operation: Callable[[GdsClient], Awaitable[object]],
    timeout: float,
) -> object:
    client = MagicMock()
    client.factory_reset = AsyncMock()
    client.set_app_value = AsyncMock()
    return asyncio.run(operation(client))


def _log_archive() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("system.log", "first\nsecond\n")
    return output.getvalue()


def test_activate_tks_web_command() -> None:
    activation = TksWebInterfaceActivation("0", f"http://{HOST}:8080/", 1.25)
    with (
        patch("pygira.commands.maintenance.resolve_tks_ip", return_value=HOST),
        patch("pygira.commands.maintenance.cs.activate_tks_webinterface", return_value=activation),
    ):
        result = CliRunner().invoke(main, ["activate-tks-web", "--tks-ip", HOST])

    assert result.exit_code == 0, result.output
    assert "active" in result.output


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            TksDeviceStatus(False, False, None, None, None, False, False),
            "unavailable",
        ),
        (
            TksDeviceStatus(True, True, 200, None, None, True, True),
            "reachable",
        ),
        (
            TksDeviceStatus(
                True,
                True,
                200,
                datetime(2026, 7, 25, 10, 20, tzinfo=timezone.utc),
                0.5,
                True,
                True,
                diagnostics=TksRuntimeDiagnostics(
                    observed_at=datetime(2026, 7, 25, 10, 19, 59, tzinfo=timezone.utc),
                    free_memory_kib=24048,
                    load_averages=(0.02, 0.04, 0.0),
                    runnable_tasks=5,
                    total_tasks=72,
                    sip_pid=1132,
                    sip_memory_kib=2396,
                    sip_memory_limit_kib=6000,
                    sip_responsive=True,
                    sip_observed_at=datetime(
                        2026,
                        7,
                        25,
                        10,
                        19,
                        58,
                        tzinfo=timezone.utc,
                    ),
                    tks_bus_state="5",
                    tks_bus_observed_at=datetime(
                        2026,
                        7,
                        25,
                        10,
                        19,
                        58,
                        tzinfo=timezone.utc,
                    ),
                    recent_failures=(),
                ),
            ),
            "operational",
        ),
    ],
)
def test_tks_status_command_branches(status: TksDeviceStatus, expected: str) -> None:
    with (
        patch("pygira.commands.maintenance.resolve_tks_ip", return_value=HOST),
        patch("pygira.commands.maintenance.find_tks_aes_key", return_value=None),
        patch(
            "pygira.commands.maintenance.cs.get_tks_device_status",
            return_value=status,
        ) as inspect_status,
    ):
        result = CliRunner().invoke(main, ["tks-status", "--tks-ip", HOST])

    assert result.exit_code == 0, result.output
    assert expected in result.output
    assert "Port 8080 was not contacted" in result.output
    inspect_status.assert_called_once_with(HOST, timeout=30.0, aes_key=None)


def test_tks_backup_and_firmware_commands(tmp_path: Path) -> None:
    client = MagicMock()
    client.backup_save.return_value = b"backup"
    login = (HOST, "admin", "secret")
    backup_output = tmp_path / "saved.img"
    backup_input = tmp_path / "input.img"
    firmware = tmp_path / "firmware.bin"
    backup_input.write_bytes(b"restore")
    firmware.write_bytes(b"firmware")

    with (
        patch("pygira.commands.maintenance.resolve_tks_login", return_value=login),
        patch("pygira.commands.maintenance.cs.activate_tks_webinterface"),
        patch("pygira.commands.maintenance.TksWebClient", return_value=client),
    ):
        runner = CliRunner()
        results = [
            runner.invoke(
                main,
                ["tks-backup-save", "--output", str(backup_output)],
            ),
            runner.invoke(
                main,
                ["tks-backup-restore", "--confirm", str(backup_input)],
            ),
            runner.invoke(
                main,
                ["tks-firmware-update", "--confirm", str(firmware)],
            ),
        ]

    assert all(result.exit_code == 0 for result in results)
    assert backup_output.read_bytes() == b"backup"
    client.backup_restore.assert_called_once_with(b"restore", "input.img")
    client.firmware_update.assert_called_once_with(b"firmware", "firmware.bin")


def test_tks_info_command() -> None:
    client = MagicMock()
    client.device_info.return_value = {"Software-Version": "05.04.00.08"}
    login = (HOST, "admin", "secret")

    with (
        patch("pygira.commands.maintenance.resolve_tks_login", return_value=login),
        patch(
            "pygira.commands.maintenance.cs.activate_tks_webinterface",
        ) as activate_web,
        patch("pygira.commands.maintenance.TksWebClient", return_value=client),
    ):
        result = CliRunner().invoke(main, ["tks-info"])

    assert result.exit_code == 0, result.output
    assert "Software-Version" in result.output
    assert "05.04.00.08" in result.output
    activate_web.assert_called_once_with(HOST)
    client.login.assert_called_once_with("admin", "secret")


def test_tks_info_reports_web_interface_activation_failure() -> None:
    login = (HOST, "admin", "secret")
    client_type = MagicMock()
    failure = OperationTimeoutError(
        "TKS-IP web interface did not start: <urlopen error [Errno 61] Connection refused>",
    )

    with (
        patch("pygira.commands.maintenance.resolve_tks_login", return_value=login),
        patch(
            "pygira.commands.maintenance.cs.activate_tks_webinterface",
            side_effect=failure,
        ),
        patch("pygira.commands.maintenance.TksWebClient", client_type),
    ):
        result = CliRunner().invoke(main, ["tks", "info"])

    assert result.exit_code == 1
    assert "Error: TKS-IP web interface did not start" in result.output
    assert "Connection refused" in result.output
    client_type.assert_not_called()


def test_tks_sip_info_command_never_prints_password_values() -> None:
    client = MagicMock()
    client.sip_clients.return_value = {
        "clients": [
            {
                "name": "Front desk",
                "selected": True,
                "username": "sip-user",
                "password_configured": True,
            },
        ],
        "incoming_calls": [
            {
                "name": "Door station",
                "calls": [{"name": "Main entrance", "assigned": True}],
            },
        ],
        "security_warning_acknowledged": True,
    }
    login = (HOST, "admin", "secret")

    with (
        patch("pygira.commands.maintenance.resolve_tks_login", return_value=login),
        patch("pygira.commands.maintenance.cs.activate_tks_webinterface"),
        patch("pygira.commands.maintenance.TksWebClient", return_value=client),
    ):
        result = CliRunner().invoke(main, ["tks", "sip", "info"])

    assert result.exit_code == 0, result.output
    assert "Front desk" in result.output
    assert "sip-user" in result.output
    assert "Main entrance" in result.output
    assert "unencrypted" in result.output
    assert "secret" not in result.output


def test_tks_backup_accepts_direct_ip_and_command_local_config(tmp_path: Path) -> None:
    config_path = tmp_path / "devices.toml"
    config_path.write_text(
        f"""
[devices.front]
type = "tks-ip"
host = "{HOST}"
username = "configured-admin"
password = "configured-secret"

[devices.rear]
type = "tks-ip"
host = "192.0.2.11"
username = "other-admin"
password = "other-secret"
""".strip(),
    )
    client = MagicMock()
    client.backup_save.return_value = b"backup"
    output = tmp_path / "backup.img"

    with (
        patch("pygira.commands.maintenance.cs.activate_tks_webinterface"),
        patch("pygira.commands.maintenance.TksWebClient", return_value=client),
    ):
        result = CliRunner().invoke(
            main,
            [
                "tks",
                "backup",
                "save",
                "--config",
                str(config_path),
                "--ip",
                HOST,
                "--output",
                str(output),
            ],
        )

    assert result.exit_code == 0, result.output
    client.login.assert_called_once_with("configured-admin", "configured-secret")
    assert output.read_bytes() == b"backup"


def test_tks_pull_logs_command(tmp_path: Path) -> None:
    output = tmp_path / "logs.dat"

    with (
        patch("pygira.commands.maintenance.resolve_tks_ip", return_value=HOST),
        patch(
            "pygira.commands.maintenance.resolve_tks_aes_key",
            return_value="0123456789abcdefghijklmn",
        ),
        patch(
            "pygira.commands.maintenance.cs.download_tks_logfile",
            return_value=b"log data",
        ) as download,
    ):
        result = CliRunner().invoke(
            main,
            ["tks-pull-logs", "--aes-key", "cli-key", "--output", str(output)],
        )

    assert result.exit_code == 0, result.output
    assert output.read_bytes() == b"log data"
    download.assert_called_once_with(HOST, aes_key="0123456789abcdefghijklmn")


def test_tks_tail_logs_command_prints_new_lines_only() -> None:
    first = b"line one\nline two\n"
    second = b"line one\nline two\nline three\n"

    with (
        patch("pygira.commands.maintenance.resolve_tks_ip", return_value=HOST),
        patch(
            "pygira.commands.maintenance.resolve_tks_aes_key",
            return_value="0123456789abcdefghijklmn",
        ),
        patch(
            "pygira.commands.maintenance.cs.download_tks_logfile",
            side_effect=[first, second, KeyboardInterrupt],
        ),
        patch("pygira.commands.maintenance.time.sleep"),
    ):
        result = CliRunner().invoke(main, ["tks-tail-logs"])

    assert result.exit_code == 0, result.output
    assert "line one" not in result.output
    assert "line three" in result.output


def test_set_weather_and_g1_factory_reset() -> None:
    station = WeatherStation(station_id="station", label="Test")
    with (
        patch("pygira.commands.maintenance.resolve_login", return_value=_login()),
        patch("pygira.commands.maintenance.weather_mod.find_station", return_value=station),
        patch("pygira.commands.maintenance.run_gds", side_effect=_run_gds),
    ):
        runner = CliRunner()
        weather = runner.invoke(main, ["set-weather", *CREDS, "--zip", "10115"])
        reset = runner.invoke(main, ["factory-reset", *CREDS, "--confirm"])

    assert weather.exit_code == 0, weather.output
    assert reset.exit_code == 0, reset.output


def test_pull_logs_uses_device_facade(tmp_path: Path) -> None:
    output = tmp_path / "logs.zip"
    device = MagicMock()
    device.logfile.return_value = b"logs"
    with patch("pygira.commands.maintenance._device_client", return_value=device):
        result = CliRunner().invoke(
            main,
            ["--device", "x1", "logs", "pull", *CREDS, "--output", str(output)],
        )

    assert result.exit_code == 0, result.output
    assert output.read_bytes() == b"logs"
    device.logfile.assert_called_once_with()


def test_tail_logs_stops_cleanly_on_keyboard_interrupt() -> None:
    with (
        patch("pygira.commands.maintenance.resolve_login", return_value=_login()),
        patch(
            "pygira.commands.maintenance._fetch_tail_logs",
            side_effect=[_log_archive(), KeyboardInterrupt()],
        ),
        patch("pygira.commands.maintenance.time.sleep"),
    ):
        result = CliRunner().invoke(main, ["--device", "g1", "logs", "tail", *CREDS, "-n", "1"])

    assert result.exit_code == 0, result.output
    assert "second" in result.output
