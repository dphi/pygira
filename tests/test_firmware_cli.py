"""CLI coverage for firmware and SSH commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from pygira.cli import main
from pygira.core.types import DeviceType


def test_check_update_uses_device_specific_status_command() -> None:
    runner = CliRunner()
    g1 = MagicMock()
    x1 = MagicMock()
    g1.check_online_update.return_value = {"available": False}
    x1.get_firmware_status.return_value = {"state": "idle"}

    with patch("pygira.commands.firmware._client", return_value=(DeviceType.G1, g1)):
        g1_result = runner.invoke(main, ["check-update"])
    with patch("pygira.commands.firmware._client", return_value=(DeviceType.X1, x1)):
        x1_result = runner.invoke(main, ["check-update"])

    assert g1_result.exit_code == 0, g1_result.output
    assert x1_result.exit_code == 0, x1_result.output
    g1.check_online_update.assert_called_once()
    x1.get_firmware_status.assert_called_once()


def test_online_upgrade_can_wait_for_completion() -> None:
    client = MagicMock()
    client.trigger_online_update.return_value = {"started": True}
    client.wait_for_completion.return_value = True
    with patch("pygira.commands.firmware._client", return_value=(DeviceType.G1, client)):
        result = CliRunner().invoke(main, ["upgrade", "--online"])

    assert result.exit_code == 0, result.output
    assert "completed" in result.output


def test_local_upgrade_uploads_and_can_return_without_waiting(tmp_path: Path) -> None:
    firmware = tmp_path / "firmware.zip"
    firmware.write_bytes(b"PK\x03\x04")
    client = MagicMock()
    client.initiate_local_install.return_value = {"started": True}
    with patch("pygira.commands.firmware._client", return_value=(DeviceType.G1, client)):
        result = CliRunner().invoke(
            main,
            ["upgrade", "--file", str(firmware), "--no-wait"],
        )

    assert result.exit_code == 0, result.output
    client.upload_firmware.assert_called_once_with(firmware)
    client.wait_for_completion.assert_not_called()


def test_commissioning_and_ssh_commands_delegate_to_client() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.commissioning_test.return_value = {"ok": True}

    with patch("pygira.commands.firmware._client", return_value=(DeviceType.G1, client)):
        results = [
            runner.invoke(main, ["commissioning-test"]),
            runner.invoke(main, ["enable-ssh", "--no-persistent"]),
            runner.invoke(main, ["disable-ssh"]),
        ]

    assert all(result.exit_code == 0 for result in results)
    client.enable_ssh.assert_called_once_with(persistent=False)
    client.disable_ssh.assert_called_once()
