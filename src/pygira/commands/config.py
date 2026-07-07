"""Configuration file management commands."""

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import click
from pydantic import ValidationError
from rich.table import Table

from pygira.context import console, die
from pygira.core.types import DeviceType
from pygira.models import DeviceConfig, LocationConfig, PygiraConfig, load_config


@dataclass(frozen=True)
class DeviceInput:
    """Parsed config add-device command options."""

    device_type: str
    host: str
    username: str
    password: str
    app_username: str
    app_password: str

    @classmethod
    def from_kwargs(cls, kwargs: dict[str, object]) -> "DeviceInput":
        """Build typed device input from Click keyword arguments."""
        return cls(
            device_type=cast("str", kwargs["device_type"]),
            host=cast("str", kwargs["host"]),
            username=cast("str", kwargs["username"]),
            password=cast("str", kwargs["password"]),
            app_username=cast("str", kwargs["app_username"]),
            app_password=cast("str", kwargs["app_password"]),
        )


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _device_lines(device: DeviceConfig) -> list[str]:
    fields = {
        "type": device.type,
        "host": device.host,
        "ip": device.ip,
        "username": device.username,
        "password": device.password,
        "admin_password": device.admin_password,
        "app_username": device.app_username,
        "app_password": device.app_password,
    }
    return [f"{key} = {_quote(value)}" for key, value in fields.items() if value]


def _serialize(config: PygiraConfig) -> str:
    lines: list[str] = []
    for name, device in sorted(config.devices.items()):
        lines.append(f"[devices.{name}]")
        lines.extend(_device_lines(device))
        lines.append("")

    for location_key, location in sorted(config.locations.items()):
        lines.append(f"[locations.{location_key}]")
        if location.name:
            lines.append(f"name = {_quote(location.name)}")
        lines.append("")
        for name, device in sorted(location.devices.items()):
            lines.append(f"[locations.{location_key}.devices.{name}]")
            lines.extend(_device_lines(device))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _config_path(ctx: click.Context) -> Path:
    return Path(ctx.obj.get("config_path", "devices.toml"))


def _load_or_empty(path: Path) -> PygiraConfig:
    if not path.exists():
        return PygiraConfig()
    return load_config(path)


def _write_config(path: Path, config: PygiraConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize(config))


def _build_device(fields: DeviceInput) -> DeviceConfig:
    if fields.device_type == DeviceType.X1.value:
        return DeviceConfig(
            type=fields.device_type,
            host=fields.host,
            username=fields.username or "device",
            admin_password=fields.password,
            app_username=fields.app_username,
            app_password=fields.app_password,
        )
    if fields.device_type == DeviceType.TKS_IP.value:
        return DeviceConfig(
            type=fields.device_type,
            host=fields.host,
            username=fields.username or "admin",
            password=fields.password,
        )
    return DeviceConfig(
        type=fields.device_type,
        host=fields.host,
        username=fields.username or "device",
        password=fields.password,
    )


@click.group("config")
def config_group() -> None:
    """Manage devices.toml."""


@config_group.command("init")
@click.option("--force", is_flag=True, help="Overwrite an existing config file")
@click.pass_context
def init_config(ctx: click.Context, force: bool) -> None:
    """Create an empty devices.toml file."""
    path = _config_path(ctx)
    if path.exists() and not force:
        msg = f"{path} already exists; pass --force to overwrite it"
        raise click.UsageError(msg)
    _write_config(path, PygiraConfig())
    console.print(f"[green]Created {path}[/green]")


@config_group.command("validate")
@click.pass_context
def validate_config(ctx: click.Context) -> None:
    """Validate devices.toml."""
    path = _config_path(ctx)
    try:
        cfg = load_config(path)
    except (OSError, ValidationError, ValueError) as e:
        die(e)
    direct = len(cfg.devices)
    located = sum(len(location.devices) for location in cfg.locations.values())
    console.print(
        f"[green]Valid {path}[/green]: {direct} direct device(s), "
        f"{located} located device(s), {len(cfg.locations)} location(s)",
    )


@config_group.command("list")
@click.pass_context
def list_devices(ctx: click.Context) -> None:
    """List configured devices."""
    path = _config_path(ctx)
    try:
        cfg = load_config(path)
    except (OSError, ValidationError, ValueError) as e:
        die(e)

    table = Table(title=str(path))
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Host")
    table.add_column("Location")

    for name, device in sorted(cfg.devices.items()):
        table.add_row(name, device.type, device.address, "")
    for location_name, location in sorted(cfg.locations.items()):
        label = location.name or location_name
        for name, device in sorted(location.devices.items()):
            table.add_row(name, device.type, device.address, label)
    console.print(table)


@config_group.command("add-device")
@click.argument("name")
@click.option(
    "--type",
    "device_type",
    required=True,
    type=click.Choice([DeviceType.G1.value, DeviceType.X1.value, DeviceType.TKS_IP.value]),
    help="Device family",
)
@click.option("--host", required=True, help="Device hostname or IP address")
@click.option("--location", default=None, help="Optional location to add the device to")
@click.option("--location-name", default="", help="Display name for a new location")
@click.option("--username", default="", help="Device username override")
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=False,
    help="Device password; for X1 this is the admin password",
)
@click.option("--app-username", default="", help="X1 app username")
@click.option("--app-password", default="", help="X1 app password")
@click.pass_context
def add_device(ctx: click.Context, **kwargs: object) -> None:
    """Add or replace a named device."""
    path = _config_path(ctx)
    try:
        cfg = _load_or_empty(path)
    except (OSError, ValidationError, ValueError) as e:
        die(e)

    name = cast("str", kwargs["name"])
    location = cast("str | None", kwargs["location"])
    location_name = cast("str", kwargs["location_name"])
    fields = DeviceInput.from_kwargs(kwargs)
    device = _build_device(fields)
    if location:
        location_cfg = cfg.locations.setdefault(
            location,
            LocationConfig(name=location_name, devices={}),
        )
        if location_name:
            location_cfg.name = location_name
        location_cfg.devices[name] = device
    else:
        cfg.devices[name] = device

    _write_config(path, cfg)
    where = f" in location {location!r}" if location else ""
    console.print(f"[green]Saved {fields.device_type} device {name!r}{where} to {path}[/green]")


def register(main: click.Group) -> None:
    """Register config-related commands."""
    main.add_command(config_group)
