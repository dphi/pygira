"""Tests for command-to-device applicability help."""

from click.testing import CliRunner

from pygira.cli import main
from pygira.command_support import missing_support_paths


def test_every_root_command_declares_device_support() -> None:
    assert missing_support_paths(main) == []


def test_root_help_prefixes_commands_with_supported_devices() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0, result.output
    assert "[G1/X1] Check if an online firmware update" in result.output
    assert "[G1] Configure weather display" in result.output
    assert "[X1] Show X1 logging verbosity" in result.output
    assert "[TKS-IP] Check TKS-IP gateway status" in result.output


def test_command_help_states_supported_devices() -> None:
    result = CliRunner().invoke(main, ["set-weather", "--help"])

    assert result.exit_code == 0, result.output
    assert "Supported devices: G1." in result.output


def test_command_support_lists_nested_commands() -> None:
    result = CliRunner().invoke(main, ["command-support"])

    assert result.exit_code == 0, result.output
    assert "gds listen" in result.output
    assert "TKS-IP" in result.output
    assert "x1-export-program" in result.output
