"""Tests for the multi-step bootstrap workflow."""

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from pygira.cli import main
from pygira.devices.g1 import PROFILE
from pygira.exceptions import TransportError
from pygira.gds import GdsClient
from pygira.models import WeatherStation


def _run_gds(
    host: str,
    username: str,
    password: str,
    operation: Callable[[GdsClient], Awaitable[object]],
    timeout: float,
) -> object:
    client = MagicMock()
    client.configure_tks = AsyncMock()
    client.set_app_value = AsyncMock()
    return asyncio.run(operation(client))


def test_bootstrap_runs_all_requested_steps() -> None:
    device = MagicMock()
    device.device_info.return_value = {"data": {"Dhcp": True}}
    station = WeatherStation(station_id="test-station", label="Test Station")
    login = (PROFILE, "192.0.2.10", "device", "secret")

    with (
        patch("pygira.commands.bootstrap.resolve_login", return_value=login),
        patch("pygira.commands.bootstrap.create_device", return_value=device),
        patch("pygira.commands.bootstrap.weather_mod.find_station", return_value=station),
        patch("pygira.commands.bootstrap.run_gds", side_effect=_run_gds),
    ):
        result = CliRunner().invoke(
            main,
            [
                "bootstrap",
                "--dhcp",
                "--tks-ip",
                "192.0.2.20",
                "--tks-user",
                "tks",
                "--tks-pass",
                "secret",
                "--weather-zip",
                "10115",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "3" in result.output
    device.set_ip.assert_called_once()


def test_bootstrap_without_step_options_reports_all_skipped() -> None:
    login = (PROFILE, "192.0.2.10", "device", "secret")
    with patch("pygira.commands.bootstrap.resolve_login", return_value=login):
        result = CliRunner().invoke(main, ["bootstrap"])

    assert result.exit_code == 0, result.output
    assert "0" in result.output
    assert "Skipped" in result.output


def test_bootstrap_continues_after_step_failures() -> None:
    device = MagicMock()
    device.device_info.side_effect = TransportError("network failed")
    login = (PROFILE, "192.0.2.10", "device", "secret")

    with (
        patch("pygira.commands.bootstrap.resolve_login", return_value=login),
        patch("pygira.commands.bootstrap.create_device", return_value=device),
        patch("pygira.commands.bootstrap.weather_mod.find_station", return_value=None),
        patch("pygira.commands.bootstrap.run_gds", side_effect=TransportError("GDS failed")),
    ):
        result = CliRunner().invoke(
            main,
            [
                "bootstrap",
                "--dhcp",
                "--tks-ip",
                "192.0.2.20",
                "--tks-user",
                "tks",
                "--tks-pass",
                "secret",
                "--weather-zip",
                "10115",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "0" in result.output
    assert "network, tks, weather" in result.output


def test_bootstrap_does_not_hide_programming_errors() -> None:
    device = MagicMock()
    device.device_info.side_effect = RuntimeError("programming bug")
    login = (PROFILE, "192.0.2.10", "device", "secret")

    with (
        patch("pygira.commands.bootstrap.resolve_login", return_value=login),
        patch("pygira.commands.bootstrap.create_device", return_value=device),
    ):
        result = CliRunner().invoke(main, ["bootstrap", "--dhcp"])

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "programming bug"
