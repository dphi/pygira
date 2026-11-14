"""First-use TKS log timezone selection."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from pygira.cli import main
from pygira.commands.tks_timezone import _local_timezone
from pygira.config_service import TksDeviceStatus
from pygira.models import load_config


def test_first_use_saves_default_and_reuses_it(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    path.write_text('[devices.front]\ntype = "tks-ip"\nip = "192.0.2.1"\npassword = "secret"\n')
    device = MagicMock()
    device.status.return_value = TksDeviceStatus(True, True, 200, None, None, True, True)
    with (
        patch("pygira.commands.tks_timezone._interactive", return_value=True),
        patch("pygira.commands.tks_timezone._local_timezone", return_value="Europe/Berlin"),
        patch("pygira.commands.maintenance.find_tks_aes_key", return_value="key"),
        patch("pygira.commands.maintenance.TksIp", return_value=device) as factory,
    ):
        runner = CliRunner()
        args = ["--config", str(path), "tks", "status", "--ip", "192.0.2.1"]
        result = runner.invoke(main, args, input="\n")
        assert result.exit_code == 0, result.output
        assert load_config(path).devices["front"].log_timezone == "Europe/Berlin"
        result = runner.invoke(main, args)
        assert result.exit_code == 0, result.output
        assert "TKS log timezone [" not in result.output
        assert factory.call_args.kwargs["log_timezone"] == "Europe/Berlin"


def test_gateway_label_requires_confirmation(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    path.write_text('[devices.front]\ntype = "tks-ip"\nip = "192.0.2.1"\npassword = "secret"\n')
    device = MagicMock()
    device.status.return_value = TksDeviceStatus(True, True, 200, None, None, True, True)
    gateway = MagicMock()
    gateway.ntp_info.return_value = {"timezone": "UTC+01:00 Amsterdam, Berlin"}
    with (
        patch("pygira.commands.tks_timezone._interactive", return_value=True),
        patch("pygira.commands.tks_timezone._local_timezone", return_value="Europe/Berlin"),
        patch("pygira.commands.tks_timezone.TksIp", return_value=gateway),
        patch("pygira.commands.maintenance.find_tks_aes_key", return_value="key"),
        patch("pygira.commands.maintenance.TksIp", return_value=device),
    ):
        result = CliRunner().invoke(
            main, ["--config", str(path), "tks", "status", "--ip", "192.0.2.1"],
            input="gateway\nEurope/Amsterdam\n",
        )
    assert result.exit_code == 0, result.output
    assert "UTC+01:00 Amsterdam, Berlin" in result.output
    assert load_config(path).devices["front"].log_timezone == "Europe/Amsterdam"


def test_noninteractive_assumes_local_without_saving(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    content = '[devices.front]\ntype = "tks-ip"\nip = "192.0.2.1"\npassword = "secret"\n'
    path.write_text(content)
    device = MagicMock()
    device.status.return_value = TksDeviceStatus(True, True, 200, None, None, True, True)
    with (
        patch("pygira.commands.tks_timezone._local_timezone", return_value="Europe/Berlin"),
        patch("pygira.commands.maintenance.find_tks_aes_key", return_value="key"),
        patch("pygira.commands.maintenance.TksIp", return_value=device) as factory,
    ):
        result = CliRunner().invoke(
            main, ["--config", str(path), "tks", "status", "--ip", "192.0.2.1"]
        )
    assert result.exit_code == 0, result.output
    assert "assuming Europe/Berlin (not saved)" in result.output
    assert path.read_text() == content
    assert factory.call_args.kwargs["log_timezone"] == "Europe/Berlin"


def test_local_timezone_prefers_valid_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Europe/Amsterdam")
    assert _local_timezone() == "Europe/Amsterdam"


def test_commented_configuration_is_not_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    content = ('# Keep this comment\n[devices.front]\ntype = "tks-ip"\n'
               'ip = "192.0.2.1"\npassword = "secret"\n')
    path.write_text(content)
    device = MagicMock()
    device.status.return_value = TksDeviceStatus(True, True, 200, None, None, True, True)
    with (
        patch("pygira.commands.tks_timezone._interactive", return_value=True),
        patch("pygira.commands.tks_timezone._local_timezone", return_value="Europe/Berlin"),
        patch("pygira.commands.maintenance.find_tks_aes_key", return_value="key"),
        patch("pygira.commands.maintenance.TksIp", return_value=device),
    ):
        result = CliRunner().invoke(
            main, ["--config", str(path), "tks", "status", "--ip", "192.0.2.1"], input="\n"
        )
    assert result.exit_code == 0, result.output
    assert "manually to preserve them" in result.output
    assert path.read_text() == content
