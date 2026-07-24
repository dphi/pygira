"""CLI coverage for maintenance and TKS integration commands."""

import asyncio
import io
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from pygira.cli import main
from pygira.config_service import TksStatus, TksWebInterfaceActivation
from pygira.devices.g1 import PROFILE as G1_PROFILE
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
        (TksStatus(False, False, None, None), "unreachable"),
        (TksStatus(True, True, "0", "ready"), "running"),
        (TksStatus(True, False, None, None), "not running"),
    ],
)
def test_tks_status_command_branches(status: TksStatus, expected: str) -> None:
    with (
        patch("pygira.commands.maintenance.resolve_tks_ip", return_value=HOST),
        patch("pygira.commands.maintenance.cs.get_tks_status", return_value=status),
    ):
        result = CliRunner().invoke(main, ["tks-status", "--tks-ip", HOST])

    assert result.exit_code == 0, result.output
    assert expected in result.output


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
