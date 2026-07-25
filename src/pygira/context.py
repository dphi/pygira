"""CLI runtime context and helpers."""

import os
from pathlib import Path
from typing import NoReturn

import click
from dotenv import dotenv_values
from rich.console import Console

from pygira.core.detect import detect_device_type
from pygira.core.resolve import resolve_device_type
from pygira.core.types import DeviceType
from pygira.devices.base import DeviceProfile
from pygira.devices.registry import get_profile
from pygira.exceptions import UnsupportedCapabilityError
from pygira.models import DeviceConfig, LocationConfig, PygiraConfig, load_config
from pygira.prompting import TypedAddress, search_select, search_select_or_ip

console = Console()
err = Console(stderr=True)
TKS_AES_KEY_ENV = "PYGIRA_TKS_AES_KEY"


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


def _find_location(cfg: PygiraConfig, name: str) -> LocationConfig | None:
    """Find a location by stable key or unique display name."""
    locations = cfg.locations
    if name in locations:
        return locations[name]
    matches = [location for location in locations.values() if location.name == name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        msg = f"Location name {name!r} is ambiguous; use its apartment id"
        raise click.UsageError(msg)
    return None


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
        location = _find_location(cfg, location_name)
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


def _prompt_configured_device(
    device_type: DeviceType | None = None,
) -> tuple[str, DeviceConfig] | TypedAddress | None:
    """Offer a keyboard-driven location/device selection before manual IP entry."""
    ctx = click.get_current_context()
    ctx.ensure_object(dict)
    obj = ctx.obj
    config_path = obj.get("config_path", "devices.toml")
    try:
        cfg = load_config(Path(config_path))
    except FileNotFoundError:
        return None

    requested = device_type or obj.get("requested_device")
    locations = [
        (key, location)
        for key, location in sorted(cfg.locations.items())
        if any(
            requested is None or _device_type(device.type) == requested
            for device in location.devices.values()
        )
    ]
    configured_location = obj.get("location")
    if configured_location:
        location = _find_location(cfg, configured_location)
        if location is None:
            msg = f"Location {configured_location!r} not found in {config_path}"
            raise click.UsageError(msg)
        locations = [(key, candidate) for key, candidate in locations if candidate is location]
        if not locations:
            expected = requested.value if isinstance(requested, DeviceType) else "supported"
            msg = f"Location {configured_location!r} has no configured {expected} device"
            raise click.UsageError(msg)
    elif not locations:
        return None

    if configured_location:
        location_key, location = locations[0]
    else:
        location_selection = search_select_or_ip(
            "Location or IP address",
            [
                (
                    f"{candidate.name} ({key})" if candidate.name else key,
                    (key, candidate),
                )
                for key, candidate in locations
            ],
        )
        if isinstance(location_selection, TypedAddress):
            return location_selection
        location_key, location = location_selection

    devices = [
        (name, device)
        for name, device in sorted(location.devices.items())
        if requested is None or _device_type(device.type) == requested
    ]
    device_name, device = search_select(
        "Device",
        [
            (
                f"{name} ({device.type.value}, {device.address})",
                (name, device),
            )
            for name, device in devices
        ],
    )
    obj["location"] = location_key
    obj["device_name"] = device_name
    return device_name, device


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
    if obj.get("device_name"):
        selected = _selected_device()
        if selected is None:
            return None
        _, device = selected
        if _device_type(device.type) != DeviceType.TKS_IP:
            msg = "The selected device is not a TKS-IP gateway"
            raise click.UsageError(msg)
        return device

    config_path = obj.get("config_path", "devices.toml")
    try:
        cfg = load_config(Path(config_path))
    except FileNotFoundError:
        return None

    location_name = obj.get("location")
    if location_name:
        location = _find_location(cfg, location_name)
        if location is None:
            msg = f"Location {location_name!r} not found in {config_path}"
            raise click.UsageError(msg)
        devices = list(location.devices.values())
    else:
        devices = [
            *cfg.devices.values(),
            *(
                device
                for location in cfg.locations.values()
                for device in location.devices.values()
            ),
        ]

    tks_devices = [device for device in devices if _device_type(device.type) == DeviceType.TKS_IP]
    if len(tks_devices) == 1:
        return tks_devices[0]
    if len(tks_devices) > 1:
        msg = (
            "Multiple TKS-IP gateways are configured; pass --location "
            "<apartment-id-or-name> before the command"
        )
        raise click.UsageError(msg)
    return None


def _selected_or_prompted_tks_device() -> DeviceConfig | TypedAddress | None:
    """Resolve one TKS-IP config, prompting when location/device choice is ambiguous."""
    try:
        return _selected_tks_device()
    except click.UsageError:
        ctx = click.get_current_context()
        if (ctx.obj or {}).get("device_name"):
            raise
        selected = _prompt_configured_device(DeviceType.TKS_IP)
        if isinstance(selected, tuple):
            return selected[1]
        return selected


def resolve_tks_ip(tks_ip: str | None) -> str:
    """Resolve a TKS-IP host from --tks-ip, config, or prompt."""
    if tks_ip:
        return tks_ip
    selected = _selected_or_prompted_tks_device()
    if isinstance(selected, TypedAddress):
        return selected.value
    if selected is not None:
        return selected.address
    return click.prompt("TKS-IP gateway IP address")


def _configured_tks_device(host: str) -> DeviceConfig | None:
    """Return one unambiguously configured TKS-IP device matching a host."""
    ctx = click.get_current_context()
    config_path = (ctx.obj or {}).get("config_path", "devices.toml")
    try:
        cfg = load_config(Path(config_path))
    except FileNotFoundError:
        return None
    devices = [
        *cfg.devices.values(),
        *(device for location in cfg.locations.values() for device in location.devices.values()),
    ]
    matches = [
        device
        for device in devices
        if _device_type(device.type) == DeviceType.TKS_IP and device.address == host
    ]
    return matches[0] if len(matches) == 1 else None


def _configured_tks_aes_key(host: str) -> str | None:
    """Return the configured AES key for one unambiguously matching host."""
    device = _configured_tks_device(host)
    return device.aes_key if device is not None else None


def find_tks_aes_key(aes_key: str | None, *, host: str | None = None) -> str | None:
    """Find a TKS logfile AES key without prompting."""
    if aes_key:
        return aes_key

    environment_key = os.environ.get(TKS_AES_KEY_ENV)
    if environment_key:
        return environment_key

    dotenv_key = dotenv_values(".env").get(TKS_AES_KEY_ENV)
    if dotenv_key:
        return dotenv_key

    if host:
        configured_key = _configured_tks_aes_key(host)
        if configured_key:
            return configured_key
    else:
        device = _selected_tks_device()
        if device is not None and device.aes_key:
            return device.aes_key

    return None


def resolve_tks_aes_key(aes_key: str | None, *, host: str | None = None) -> str:
    """Resolve the TKS logfile AES key from options, configuration, or a prompt."""
    resolved = find_tks_aes_key(aes_key, host=host)
    if resolved:
        return resolved
    return click.prompt(
        "TKS-IP logfile AES key",
        hide_input=True,
    )


def resolve_tks_login(
    tks_ip: str | None,
    tks_user: str | None,
    tks_pass: str | None,
) -> tuple[str, str, str]:
    """Resolve TKS-IP gateway host + web-login credentials."""
    selected: DeviceConfig | TypedAddress | None
    if tks_ip and tks_user and tks_pass:
        selected = None
    elif tks_ip:
        obj = click.get_current_context().find_root().obj or {}
        if obj.get("device_name") or obj.get("location"):
            selected = _selected_or_prompted_tks_device()
        else:
            selected = _configured_tks_device(tks_ip)
    else:
        selected = _selected_or_prompted_tks_device()
    if isinstance(selected, TypedAddress):
        tks_ip = tks_ip or selected.value
    elif selected is not None:
        host, username, password, _ = _device_login(selected)
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
    ctx.ensure_object(dict)
    obj = ctx.obj
    selected = _selected_device()
    if selected is None and not ip:
        prompted = _prompt_configured_device()
        if isinstance(prompted, TypedAddress):
            ip = prompted.value
        else:
            selected = prompted
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
