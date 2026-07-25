"""Behavior tests for the noun-first command interface."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from pygira.cli import main
from pygira.commands.maintenance import _LogTarget
from pygira.context import TKS_AES_KEY_ENV
from pygira.core.detect import DetectionResult
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
        patch(
            "pygira.commands.maintenance._log_target",
            return_value=_LogTarget(DeviceType.G1, None),
        ),
        patch("pygira.commands.maintenance._device_client", return_value=device),
    ):
        result = CliRunner().invoke(main, ["logs", "pull", "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert output.read_bytes() == b"logs"


def test_logs_pull_uses_same_command_for_tks_ip(tmp_path: Path) -> None:
    output = tmp_path / "tks-logs.dat"

    with (
        patch(
            "pygira.commands.maintenance._log_target",
            return_value=_LogTarget(DeviceType.TKS_IP, None),
        ),
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


def test_logs_tail_prompts_for_tks_aes_key_without_device_password(tmp_path: Path) -> None:
    config_path = tmp_path / "devices.toml"
    config_path.write_text(
        """
[devices.front]
type = "tks-ip"
host = "192.0.2.20"
password = "web-secret"

[devices.rear]
type = "tks-ip"
host = "192.0.2.21"
password = "web-secret"
""".strip(),
    )
    download = MagicMock(side_effect=[b"first line\n", KeyboardInterrupt])

    with (
        patch.dict(os.environ, {TKS_AES_KEY_ENV: ""}),
        patch("pygira.context.dotenv_values", return_value={}),
        patch(
            "pygira.commands.maintenance.detect_device_type",
            return_value=DetectionResult(
                DeviceType.TKS_IP,
                "/ asset-marker=com.gira.tkipgw.web.sites",
            ),
        ),
        patch("pygira.commands.maintenance.resolve_login") as resolve_login,
        patch("pygira.commands.maintenance.cs.download_tks_logfile", download),
        patch("pygira.commands.maintenance.time.sleep"),
    ):
        result = CliRunner().invoke(
            main,
            ["--config", str(config_path), "logs", "tail"],
            input="192.0.2.20\n0123456789abcdefghijklmn\n",
        )

    assert result.exit_code == 0, result.output
    assert "Device IP address" in result.output
    assert "Log source device type" not in result.output
    assert "TKS-IP logfile AES key" in result.output
    assert "password" not in result.output.casefold()
    resolve_login.assert_not_called()
    download.assert_called_with("192.0.2.20", aes_key="0123456789abcdefghijklmn")


def test_logs_tail_uses_aes_key_configured_for_prompted_tks_host(tmp_path: Path) -> None:
    config_path = tmp_path / "devices.toml"
    config_path.write_text(
        """
[devices.front]
type = "tks-ip"
host = "192.0.2.20"
password = "web-secret"
aes_key = "configured-key"

[devices.rear]
type = "tks-ip"
host = "192.0.2.21"
password = "web-secret"
aes_key = "other-key"
""".strip(),
    )
    download = MagicMock(side_effect=[b"first line\n", KeyboardInterrupt])

    with (
        patch.dict(os.environ, {TKS_AES_KEY_ENV: ""}),
        patch("pygira.context.dotenv_values", return_value={}),
        patch(
            "pygira.commands.maintenance.detect_device_type",
            return_value=DetectionResult(
                DeviceType.TKS_IP,
                "/ asset-marker=com.gira.tkipgw.web.sites",
            ),
        ),
        patch("pygira.commands.maintenance.resolve_login") as resolve_login,
        patch("pygira.commands.maintenance.cs.download_tks_logfile", download),
        patch("pygira.commands.maintenance.time.sleep"),
    ):
        result = CliRunner().invoke(
            main,
            ["--config", str(config_path), "logs", "tail"],
            input="192.0.2.20\n",
        )

    assert result.exit_code == 0, result.output
    assert "TKS-IP logfile AES key" not in result.output
    resolve_login.assert_not_called()
    download.assert_called_with("192.0.2.20", aes_key="configured-key")
