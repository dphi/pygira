"""First-use timezone selection for TKS diagnostic logs."""

import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import click

from pygira.commands.config import _write_config
from pygira.context import _device_type, resolve_tks_login
from pygira.core.types import DeviceType
from pygira.devices.tks_ip import TksIp
from pygira.models import load_config


def _valid_timezone(value: str) -> bool:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _local_timezone() -> str | None:
    candidates = [os.environ.get("TZ", "").removeprefix(":")]
    try:
        target = str(Path("/etc/localtime").resolve())
        if "/zoneinfo/" in target:
            candidates.append(target.split("/zoneinfo/", 1)[1])
        candidates.append(Path("/etc/timezone").read_text().strip())
    except OSError:
        pass
    return next((value for value in candidates if value and _valid_timezone(value)), None)


def _interactive() -> bool:
    return sys.stdin.isatty()


def resolve_log_timezone(host: str, configured: str | None, timeout: float) -> str | None:
    """Choose the timezone once, saving it for an unambiguous configured gateway."""
    if configured:
        return configured
    local = _local_timezone()
    if not _interactive():
        click.echo(
            f"TKS log timezone is not configured; assuming {local or 'UTC'} (not saved).",
            err=True,
        )
        return local

    click.echo("TKS logs have no timezone. The local system timezone may differ from the gateway.")
    click.echo("Enter an IANA timezone, or 'gateway' to read the gateway's setting "
               "(starts its web interface).")
    default = local
    while True:
        selected = click.prompt("TKS log timezone", default=default, type=str).strip()
        if selected.lower() == "gateway":
            try:
                _, username, password = resolve_tks_login(host, None, None)
                info = TksIp(host, username, password, timeout=timeout).ntp_info()
                label = str(info.get("timezone", ""))
                click.echo(f"Gateway timezone: {label or 'unavailable'}")
                default = label if _valid_timezone(label) else local
                click.echo("Confirm the corresponding IANA timezone name.")
            except Exception as exc:  # Gateway lookup is optional; allow manual entry.
                click.echo(f"Could not read gateway timezone: {exc}", err=True)
            continue
        if _valid_timezone(selected):
            break
        click.echo("Please enter a valid IANA timezone, such as Europe/Berlin.", err=True)

    return _save_timezone(host, selected)


def _save_timezone(host: str, selected: str) -> str:
    ctx = click.get_current_context().find_root()
    path = Path((ctx.obj or {}).get("config_path", "devices.toml"))
    try:
        original = path.read_bytes()
        config = load_config(path)
    except FileNotFoundError:
        click.echo("No configuration file found; timezone is used for this request only.", err=True)
        return selected
    devices = [
        *config.devices.values(),
        *(d for loc in config.locations.values() for d in loc.devices.values()),
    ]
    matches = [
        d for d in devices if _device_type(d.type) == DeviceType.TKS_IP and d.address == host
    ]
    if len(matches) != 1:
        click.echo("No unique configured gateway; timezone is used for this request only.",
                   err=True)
    elif matches[0].log_timezone:
        return matches[0].log_timezone
    elif b"#" in original or config.apartments:
        click.echo(
            "Configuration contains comments or legacy apartments; add "
            f'log_timezone = "{selected}" manually to preserve them.',
            err=True,
        )
    elif path.read_bytes() != original:
        click.echo("Configuration changed; timezone is used for this request only.", err=True)
    else:
        matches[0].log_timezone = selected
        _write_config(path, config)
        click.echo(f"Saved TKS log timezone {selected} in {path}.")
    return selected
