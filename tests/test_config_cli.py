import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from pygira.cli import main
from pygira.context import resolve_login
from pygira.core.detect import DetectionResult
from pygira.core.types import DeviceType
from pygira.models import DeviceConfig, load_config

PRIVATE_FILE_MODE = 0o600


def test_config_add_direct_device_and_list(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "--config",
            str(path),
            "config",
            "add-device",
            "wall_g1",
            "--type",
            "g1",
            "--host",
            "192.168.1.240",
            "--password",
            "secret",
        ],
    )

    assert result.exit_code == 0, result.output
    cfg = load_config(path)
    assert cfg.devices["wall_g1"].type == "g1"
    assert cfg.devices["wall_g1"].host == "192.168.1.240"
    assert cfg.devices["wall_g1"].password == "secret"
    assert stat.S_IMODE(path.stat().st_mode) == PRIVATE_FILE_MODE

    list_result = runner.invoke(main, ["--config", str(path), "config", "list"])
    assert list_result.exit_code == 0, list_result.output
    assert "wall_g1" in list_result.output
    assert "192.168.1.240" in list_result.output


def test_config_add_device_inside_location(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "--config",
            str(path),
            "config",
            "add-device",
            "controller",
            "--type",
            "x1",
            "--host",
            "x1.local",
            "--password",
            "admin-secret",
            "--location",
            "home",
            "--location-name",
            "Home",
            "--app-username",
            "Wohnung1",
            "--app-password",
            "app-secret",
        ],
    )

    assert result.exit_code == 0, result.output
    cfg = load_config(path)
    device = cfg.locations["home"].devices["controller"]
    assert cfg.locations["home"].name == "Home"
    assert device.type == "x1"
    assert device.host == "x1.local"
    assert device.admin_password == "admin-secret"
    assert device.app_username == "Wohnung1"
    assert device.app_password == "app-secret"

    validate_result = runner.invoke(main, ["--config", str(path), "config", "validate"])
    assert validate_result.exit_code == 0, validate_result.output
    assert "1 located" in validate_result.output
    assert "device(s)" in validate_result.output


def test_config_round_trips_quoted_device_and_location_names(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(path),
            "config",
            "add-device",
            "front.door",
            "--type",
            "g1",
            "--host",
            "g1.local",
            "--password",
            "secret",
            "--location",
            "my home",
        ],
    )

    assert result.exit_code == 0, result.output
    assert load_config(path).locations["my home"].devices["front.door"].host == "g1.local"


def test_config_validate_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    path.write_text(
        "\n".join(
            [
                "[devices.wall]",
                'type = "g1"',
                'host = "g1.local"',
                'password = "secret"',
                'pasword = "typo"',
            ],
        ),
    )

    result = CliRunner().invoke(main, ["--config", str(path), "config", "validate"])

    assert result.exit_code == 1
    assert "pasword" in result.output


@pytest.mark.parametrize(
    ("device", "message"),
    [
        (
            {"type": DeviceType.UNKNOWN, "host": "g1.local", "password": "secret"},
            "cannot be configured",
        ),
        (
            {
                "type": DeviceType.G1,
                "host": "g1.local",
                "ip": "192.168.1.2",
                "password": "secret",
            },
            "Exactly one",
        ),
        (
            {"type": DeviceType.G1, "host": "http://g1.local", "password": "secret"},
            "without a URL",
        ),
        ({"type": DeviceType.G1, "host": "g1.local"}, "missing a password"),
    ],
)
def test_device_config_rejects_unsafe_or_incomplete_entries(
    device: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        DeviceConfig.model_validate(device)


def test_diagnostics_preserves_missing_username_for_resolution() -> None:
    resolved = (SimpleNamespace(api_prefix="/api"), "g1.local", "device", "secret")
    client = MagicMock()
    client.get_diagnostic_page.return_value = {}
    with (
        patch("pygira.commands.device.resolve_login", return_value=resolved) as resolve,
        patch("pygira.commands.device.api_mod.ApiClient", return_value=client),
    ):
        result = CliRunner().invoke(
            main,
            ["diagnostics", "--ip", "g1.local", "--password", "secret"],
        )

    assert result.exit_code == 0, result.output
    resolve.assert_called_once_with("g1.local", None, "secret")


def test_resolve_login_uses_direct_named_device(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    path.write_text(
        """
[devices.wall_g1]
type = "g1"
host = "192.168.1.240"
password = "secret"
""".strip(),
    )

    ctx = click.Context(
        click.Command("test"),
        obj={
            "config_path": str(path),
            "device_name": "wall_g1",
            "location": None,
            "requested_device": None,
        },
    )
    with (
        ctx,
        patch(
            "pygira.context.detect_device_type",
            return_value=DetectionResult(DeviceType.G1, "test"),
        ),
    ):
        profile, host, username, password = resolve_login(None, None, None)

    assert profile.device_type == DeviceType.G1
    assert host == "192.168.1.240"
    assert username == "device"
    assert password == "secret"


def test_resolve_login_uses_named_device_inside_location(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    path.write_text(
        """
[locations.home]
name = "Home"

[locations.home.devices.controller]
type = "x1"
host = "x1.local"
admin_password = "secret"
""".strip(),
    )

    ctx = click.Context(
        click.Command("test"),
        obj={
            "config_path": str(path),
            "device_name": "controller",
            "location": "home",
            "requested_device": None,
        },
    )
    with (
        ctx,
        patch(
            "pygira.context.detect_device_type",
            return_value=DetectionResult(DeviceType.X1, "test"),
        ),
    ):
        profile, host, username, password = resolve_login(None, None, None)

    assert profile.device_type == DeviceType.X1
    assert host == "x1.local"
    assert username == "device"
    assert password == "secret"
