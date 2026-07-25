"""Compatibility tests for the apartment-oriented devices.toml schema."""

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from pygira.cli import main
from pygira.context import _selected_tks_device, resolve_tks_login
from pygira.core.types import DeviceType
from pygira.models import load_config

LEGACY_CONFIG = """
[[apartments]]
id = 17
name = "North"

[apartments.g1]
ip = "192.0.2.10"
password = "g1-secret"

[apartments.x1]
ip = "192.0.2.11"
admin_password = "x1-secret"
app_username = "app"
app_password = "app-secret"

[apartments.tks_ip]
ip = "192.0.2.12"
username = "admin"
password = "tks-secret"
"""
def test_load_config_normalizes_legacy_apartments(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    path.write_text(LEGACY_CONFIG)

    config = load_config(path)

    location = config.locations["17"]
    assert location.name == "North"
    assert location.devices["g1"].type == DeviceType.G1
    assert location.devices["x1"].admin_password == "x1-secret"
    assert location.devices["tks_ip"].type == DeviceType.TKS_IP


def test_location_can_be_selected_by_apartment_name(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    path.write_text(LEGACY_CONFIG)

    with click.Context(
        main,
        obj={"config_path": str(path), "location": "North", "device_name": None},
    ):
        device = _selected_tks_device()

    assert device is not None
    assert device.address == "192.0.2.12"


def test_multiple_tks_gateways_can_be_selected_by_location(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    path.write_text(
        LEGACY_CONFIG
        + LEGACY_CONFIG.replace("id = 17", "id = 18").replace(
            'ip = "192.0.2.12"',
            'ip = "192.0.2.22"',
        ),
    )

    @click.command()
    def resolve() -> None:
        host, username, password = resolve_tks_login(None, None, None)
        click.echo(f"{host}|{username}|{password}")

    result = CliRunner().invoke(
        resolve,
        input="North (18)\n",
        obj={
            "config_path": str(path),
            "location": None,
            "device_name": None,
            "requested_device": None,
        },
    )

    assert result.exit_code == 0, result.output
    assert "North (18)" in result.output
    assert "Device:" not in result.output
    assert "192.0.2.22|admin|tks-secret" in result.output


def test_legacy_apartment_ids_must_be_unique(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    path.write_text(LEGACY_CONFIG + LEGACY_CONFIG)

    with pytest.raises(ValueError, match="Apartment id '17' is duplicated"):
        load_config(path)
