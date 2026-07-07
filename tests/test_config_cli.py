from pathlib import Path
from unittest.mock import patch

import click
from click.testing import CliRunner

from pygira.cli import main
from pygira.context import resolve_login
from pygira.core.detect import DetectionResult
from pygira.core.types import DeviceType
from pygira.models import load_config


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
