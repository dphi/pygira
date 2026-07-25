"""Tests for command-to-device applicability help."""

from click.testing import CliRunner

from pygira.cli import main
from pygira.command_support import missing_support_paths


def test_every_root_command_declares_device_support() -> None:
    assert missing_support_paths(main) == []


def test_root_help_prefixes_commands_with_supported_devices() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0, result.output
    assert "[G1/X1] Check and install device firmware" in result.output
    assert "[G1] Configure the G1 weather display" in result.output
    assert "[G1/X1] Read or change device logging verbosity" in result.output
    assert "[G1/TKS-IP] Manage G1 door communication" in result.output
    assert "get-ntp" not in result.output


def test_command_help_states_supported_devices() -> None:
    result = CliRunner().invoke(main, ["weather", "set", "--help"])

    assert result.exit_code == 0, result.output
    assert "Supported devices: G1." in result.output
    assert "--ip TEXT" in result.output
    assert "--name TEXT" in result.output
    assert "--location TEXT" in result.output
    assert "--config FILE" in result.output


def test_tks_command_help_uses_common_ip_spelling() -> None:
    result = CliRunner().invoke(main, ["tks", "status", "--help"])

    assert result.exit_code == 0, result.output
    assert "--ip, --tks-ip TEXT" in result.output
    assert "--name TEXT" in result.output


def test_command_support_lists_nested_commands() -> None:
    result = CliRunner().invoke(main, ["command-support"])

    assert result.exit_code == 0, result.output
    assert "gds listen" in result.output
    assert "TKS-IP" in result.output
    assert "program export" in result.output
    assert "network get" in result.output
    assert "tks info" in result.output


def test_flat_command_aliases_are_hidden_and_deprecated() -> None:
    command = main.commands["get-ntp"]

    assert command.hidden is True
    assert command.deprecated
