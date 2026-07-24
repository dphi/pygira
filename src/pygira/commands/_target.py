"""Shared device-target resolution for CLI commands."""

from pygira.context import resolve_login
from pygira.devices.base import ResolvedTarget
from pygira.devices.registry import Device, create_device


def resolve_device(
    ip: str | None,
    password: str | None,
    username: str | None,
    timeout: float,
) -> Device:
    """Resolve command options and construct the matching public device facade."""
    profile, host, resolved_username, resolved_password = resolve_login(ip, username, password)
    return create_device(
        ResolvedTarget(
            profile=profile,
            host=host,
            username=resolved_username,
            password=resolved_password,
            timeout=timeout,
        ),
    )
