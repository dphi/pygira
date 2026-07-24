"""CLI coverage for device inspection and configuration commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from pygira.cli import main
from pygira.core.detect import DetectionResult
from pygira.core.types import DeviceType

USAGE_ERROR_EXIT_CODE = 2


def _device_client() -> MagicMock:
    device = MagicMock()
    device.device_info.return_value = {
        "data": {
            "CurrentFirmwareVersion": "3.5.63",
            "MacAddress": "00:11:22:33:44:55",
            "Dhcp": True,
            "IpAddress": "192.0.2.10",
            "Ntp": True,
            "NtpServerAddress": "pool.ntp.org",
            "NtpInterval": "10",
        },
    }
    device.diagnostic_page.return_value = {
        "data": {
            "diagnosticpage": [
                {"title": "diagnostic.titles.system", "blob": "Linux test"},
            ],
        },
    }
    return device


def test_detect_renders_identified_device() -> None:
    detected = DetectionResult(DeviceType.G1, "/api AppName=Gira G1", "Gira G1", "3.5.63")
    with patch("pygira.commands.device.detect_device_type", return_value=detected):
        result = CliRunner().invoke(main, ["detect", "--ip", "192.0.2.10"])

    assert result.exit_code == 0, result.output
    assert "3.5.63" in result.output


def test_detect_reports_unknown_device_as_cli_error() -> None:
    detected = DetectionResult(DeviceType.UNKNOWN, "no probe succeeded")
    with patch("pygira.commands.device.detect_device_type", return_value=detected):
        result = CliRunner().invoke(main, ["detect", "--ip", "192.0.2.10"])

    assert result.exit_code == 1
    assert "Could not detect" in result.output


def test_detect_can_select_a_configured_location_and_device(tmp_path: Path) -> None:
    config_path = tmp_path / "devices.toml"
    config_path.write_text(
        """
[locations.home]
name = "Home"

[locations.home.devices.panel]
type = "g1"
host = "g1.home"
username = "configured-user"
password = "configured-secret"
""".strip(),
    )
    detected = DetectionResult(DeviceType.G1, "/api AppName=Gira G1")

    with patch(
        "pygira.commands.device.detect_device_type",
        return_value=detected,
    ) as detect_device:
        result = CliRunner().invoke(
            main,
            ["--config", str(config_path), "device", "detect"],
            input="y\nHome (home)\npanel (g1, g1.home)\n",
        )

    assert result.exit_code == 0, result.output
    assert "Home (home)" in result.output
    assert "panel (g1, g1.home)" in result.output
    detect_device.assert_called_once_with("g1.home", "configured-user", "configured-secret")


def test_info_diagnostics_and_ntp_commands() -> None:
    client = _device_client()
    runner = CliRunner()
    with patch("pygira.commands.device._device_client", return_value=client):
        results = [
            runner.invoke(main, ["info"]),
            runner.invoke(main, ["info", "--long"]),
            runner.invoke(main, ["diagnostics", "--json"]),
            runner.invoke(main, ["get-ntp"]),
            runner.invoke(main, ["set-ntp", "--server", "pool.ntp.org", "--interval", "15"]),
        ]

    assert all(result.exit_code == 0 for result in results)
    client.set_ntp.assert_called_once_with(
        enabled=True,
        server="pool.ntp.org",
        interval_minutes=15,
    )


def test_set_ip_merges_requested_network_values() -> None:
    client = _device_client()
    with patch("pygira.commands.device._device_client", return_value=client):
        result = CliRunner().invoke(
            main,
            ["set-ip", "--no-dhcp", "--static-ip", "192.0.2.20", "--dns1", "192.0.2.53"],
        )

    assert result.exit_code == 0, result.output
    config = client.set_ip.call_args.args[0]
    assert config.dhcp is False
    assert config.ip_address == "192.0.2.20"
    assert config.primary_dns == "192.0.2.53"


def test_set_ip_requires_at_least_one_change() -> None:
    result = CliRunner().invoke(main, ["set-ip"])

    assert result.exit_code == USAGE_ERROR_EXIT_CODE
    assert "No network flags" in result.output
