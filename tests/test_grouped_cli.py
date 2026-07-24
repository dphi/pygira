"""Behavior tests for the noun-first command interface."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from pygira.cli import main
from pygira.core.types import DeviceType


def test_network_get_prints_normalized_keys() -> None:
    device = MagicMock()
    device.device_info.return_value = {
        "data": {
            "Dhcp": True,
            "IpAddress": "192.0.2.10",
            "SubnetMask": "255.255.255.0",
            "DefaultGateway": "192.0.2.1",
            "NameServer": "192.0.2.53",
        },
    }

    with patch("pygira.commands.device._device_client", return_value=device):
        result = CliRunner().invoke(main, ["network", "get"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ip_address"] == "192.0.2.10"


def test_logs_pull_uses_same_command_for_g1_and_x1(tmp_path: Path) -> None:
    output = tmp_path / "device-logs.zip"
    device = MagicMock()
    device.logfile.return_value = b"logs"

    with (
        patch("pygira.commands.maintenance._log_target_type", return_value=DeviceType.G1),
        patch("pygira.commands.maintenance._device_client", return_value=device),
    ):
        result = CliRunner().invoke(main, ["logs", "pull", "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert output.read_bytes() == b"logs"


def test_logs_pull_uses_same_command_for_tks_ip(tmp_path: Path) -> None:
    output = tmp_path / "tks-logs.dat"

    with (
        patch("pygira.commands.maintenance._log_target_type", return_value=DeviceType.TKS_IP),
        patch("pygira.commands.maintenance.resolve_tks_ip", return_value="192.0.2.20"),
        patch("pygira.commands.maintenance.resolve_tks_aes_key", return_value="key"),
        patch(
            "pygira.commands.maintenance.cs.download_tks_logfile",
            return_value=b"tks logs",
        ),
    ):
        result = CliRunner().invoke(main, ["logs", "pull", "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert output.read_bytes() == b"tks logs"
