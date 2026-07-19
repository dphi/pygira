"""CLI runtime context and helpers."""

from pathlib import Path
from typing import NoReturn

import click
from rich.console import Console

from pygira.core.detect import detect_device_type
from pygira.core.resolve import resolve_device_type
from pygira.core.types import DeviceType
from pygira.devices.base import DeviceProfile
from pygira.devices.registry import get_profile
from pygira.exceptions import UnsupportedCapabilityError
from pygira.models import DeviceConfig, load_config

console = Console()
err = Console(stderr=True)


def resolve_profile(ip: str, username: str, password: str) -> DeviceProfile:
    """Resolve active device profile for a command execution."""
    ctx = click.get_current_context()
    requested = ctx.obj.get("requested_device")
    detected = detect_device_type(ip, username, password)
    resolved = resolve_device_type(requested, detected)
    return get_profile(resolved)


def _device_type(value: str | DeviceType) -> DeviceType:
    try:
        return DeviceType(value)
    except ValueError as exc:
        msg = f"Unsupported device type {value!r} in devices.toml"
        raise click.UsageError(msg) from exc


def _selected_device() -> tuple[str, DeviceConfig] | None:
    """Return the selected named device from devices.toml, if --name was used."""
    ctx = click.get_current_context()
    obj = ctx.obj or {}
    device_name = obj.get("device_name")
    if not device_name:
        return None

    config_path = obj.get("config_path", "devices.toml")
    cfg = load_config(Path(config_path))
    location_name = obj.get("location")

    if location_name:
        location = cfg.locations.get(location_name)
        if location is None:
            msg = f"Location {location_name!r} not found in {config_path}"
            raise click.UsageError(msg)
        device = location.devices.get(device_name)
        if device is None:
            msg = f"Device {device_name!r} not found in location {location_name!r}"
            raise click.UsageError(
                msg,
            )
        return device_name, device

    if device_name in cfg.devices:
        return device_name, cfg.devices[device_name]

    matches = [
        (location_key, location.devices[device_name])
        for location_key, location in cfg.locations.items()
        if device_name in location.devices
    ]
    if len(matches) == 1:
        return device_name, matches[0][1]
    if matches:
        locations = ", ".join(name for name, _ in matches)
        msg = f"Device {device_name!r} exists in multiple locations ({locations}); pass --location"
        raise click.UsageError(
            msg,
        )
    msg = f"Device {device_name!r} not found in {config_path}"
    raise click.UsageError(msg)


def _device_login(device: DeviceConfig) -> tuple[str, str, str, DeviceType]:
    """Convert a config device into host, username, password, and type."""
    device_type = _device_type(device.type)
    host = device.address
    if not host:
        msg = "Configured device is missing host"
        raise click.UsageError(msg)

    if device_type == DeviceType.X1:
        password = device.admin_password or device.password
        username = device.username or "device"
    elif device_type == DeviceType.TKS_IP:
        password = device.password
        username = device.username or "admin"
    else:
        password = device.password
        username = device.username or "device"

    if not password:
        msg = f"Configured {device_type.value} device {host!r} is missing password"
        raise click.UsageError(
            msg,
        )
    return host, username, password, device_type


def _selected_tks_device() -> DeviceConfig | None:
    """Return a configured TKS-IP device related to the selected location/device."""
    ctx = click.get_current_context()
    obj = ctx.obj or {}
    config_path = obj.get("config_path", "devices.toml")
    try:
        cfg = load_config(Path(config_path))
    except FileNotFoundError:
        return None

    location_name = obj.get("location")
    if location_name:
        location = cfg.locations.get(location_name)
        if location is None:
            msg = f"Location {location_name!r} not found in {config_path}"
            raise click.UsageError(msg)
        devices = location.devices.values()
    else:
        devices = cfg.devices.values()

    tks_devices = [device for device in devices if _device_type(device.type) == DeviceType.TKS_IP]
    if len(tks_devices) == 1:
        return tks_devices[0]
    return None


def resolve_tks_ip(tks_ip: str | None) -> str:
    """Resolve a TKS-IP host from --tks-ip, config, or prompt."""
    if tks_ip:
        return tks_ip
    device = _selected_tks_device()
    if device is not None:
        return device.address
    return click.prompt("TKS-IP gateway IP address")


def resolve_tks_login(
    tks_ip: str | None,
    tks_user: str | None,
    tks_pass: str | None,
) -> tuple[str, str, str]:
    """Resolve TKS-IP gateway host + web-login credentials."""
    device = _selected_tks_device() if not (tks_ip and tks_user and tks_pass) else None
    if device is not None:
        host, username, password, _ = _device_login(device)
        tks_ip = tks_ip or host
        tks_user = tks_user or username
        tks_pass = tks_pass or password

    resolved_ip = tks_ip or click.prompt("TKS-IP gateway IP address")
    resolved_user = tks_user or click.prompt("TKS-IP gateway username")
    resolved_pass = tks_pass or click.prompt("TKS-IP gateway password", hide_input=True)
    return resolved_ip, resolved_user, resolved_pass


def resolve_login(
    ip: str | None,
    username: str | None,
    password: str | None,
) -> tuple[DeviceProfile, str, str, str]:
    """Resolve device profile and effective username from config, options, or prompts."""
    ctx = click.get_current_context()
    obj = ctx.obj or {}
    selected = _selected_device()
    if selected is not None:
        _, device = selected
        cfg_ip, cfg_username, cfg_password, cfg_type = _device_login(device)
        ip = ip or cfg_ip
        username = username or cfg_username
        password = password or cfg_password
        obj["requested_device"] = obj.get("requested_device") or cfg_type
    else:
        ip = ip or click.prompt("Device IP address")
        password = password or click.prompt("Device password", hide_input=True)

    resolved_ip = ip or click.prompt("Device IP address")
    resolved_password = password or click.prompt("Device password", hide_input=True)
    profile = resolve_profile(resolved_ip, username or "", resolved_password)
    return profile, resolved_ip, (username or profile.default_username), resolved_password


def die(e: Exception | str) -> NoReturn:
    """Translate an application failure into Click's standard CLI error handling."""
    click_error = click.ClickException(str(e))
    if isinstance(e, Exception):
        raise click_error from e
    raise click_error


def require_capability(profile: DeviceProfile, *, weather: bool = False, tks: bool = False) -> None:
    """Ensure profile supports requested capability."""
    if weather and not profile.capabilities.weather:
        msg = f"Weather configuration is not supported on {profile.display_name}."
        raise UnsupportedCapabilityError(msg)
    if tks and not profile.capabilities.tks:
        msg = f"TKS configuration is not supported on {profile.display_name}."
        raise UnsupportedCapabilityError(msg)
