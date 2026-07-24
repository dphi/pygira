"""Tests for the device-neutral logging commands."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from pygira.cli import main


def test_get_logging_uses_device_facade_and_prints_normalized_mode() -> None:
    device = MagicMock()
    device.get_logging_severity.return_value = 0

    with patch("pygira.commands.maintenance._device_client", return_value=device):
        result = CliRunner().invoke(main, ["logging", "get"])

    assert result.exit_code == 0, result.output
    assert result.output == "extended\n"
    device.get_logging_severity.assert_called_once_with()


def test_set_logging_uses_device_facade() -> None:
    device = MagicMock()

    with patch("pygira.commands.maintenance._device_client", return_value=device):
        result = CliRunner().invoke(main, ["logging", "set", "--mode", "normal"])

    assert result.exit_code == 0, result.output
    assert result.output == "Logging mode set to normal.\n"
    device.set_logging_severity.assert_called_once_with(4)
