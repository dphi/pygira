"""Firmware and SSH commands."""

import json
from pathlib import Path
from typing import cast

import click

from pygira import api as api_mod
from pygira.context import console, die, resolve_login
from pygira.core.types import DeviceType
from pygira.options import common_options


def _client(
    ip: str | None,
    password: str | None,
    username: str,
    timeout: float,
) -> tuple[DeviceType, api_mod.ApiClient]:
    profile, ip, username, password = resolve_login(ip, username, password)
    client = api_mod.ApiClient(
        ip,
        username,
        password,
        api_prefix=profile.api_prefix,
        timeout=timeout,
    )
    return profile.device_type, client


@click.command("check-update")
@common_options
def check_update(ip: str | None, password: str | None, username: str, timeout: float) -> None:
    """Check if an online firmware update is available."""
    try:
        device_type, client = _client(ip, password, username, timeout)
        result = (
            client.get_firmware_status()
            if device_type == DeviceType.X1
            else client.check_online_update()
        )
        console.print_json(json.dumps(result))
    except Exception as e:
        die(e)


@click.command()
@common_options
@click.option(
    "--file",
    "firmware_file",
    default=None,
    type=click.Path(exists=True),
    help="Local firmware ZIP file",
)
@click.option("--online", is_flag=True, help="Trigger online update from Gira download server")
@click.option("--no-wait", is_flag=True, help="Return immediately without polling progress")
def upgrade(**kwargs: object) -> None:
    """Upgrade device firmware (local file or online)."""
    ip = cast("str | None", kwargs["ip"])
    password = cast("str | None", kwargs["password"])
    username = cast("str", kwargs["username"])
    timeout = cast("float", kwargs["timeout"])
    firmware_file = cast("str | None", kwargs["firmware_file"])
    online = cast("bool", kwargs["online"])
    no_wait = cast("bool", kwargs["no_wait"])

    if not firmware_file and not online:
        source = click.prompt("Update source", type=click.Choice(["file", "online"]))
        if source == "file":
            firmware_file = click.prompt("Firmware file path", type=click.Path(exists=True))
        else:
            online = True

    try:
        _, client = _client(ip, password, username, timeout)
        if firmware_file:
            console.print(f"Uploading firmware from {firmware_file!r}…")
            client.upload_firmware(Path(firmware_file))
            console.print("Upload complete. Triggering install…")
            result = client.initiate_local_install()
            console.print_json(json.dumps(result))
        else:
            console.print("Starting online firmware update…")
            result = client.trigger_online_update()
            console.print_json(json.dumps(result))

        if no_wait:
            return
        console.print("Waiting for update to complete (up to 5 min)…")
        done = client.wait_for_completion()
        if done:
            console.print("[green]Firmware update completed.[/green]")
        else:
            console.print(
                "[yellow]Timed out waiting for completion — device may still be updating.[/yellow]",
            )
    except Exception as e:
        die(e)


@click.command("commissioning-test")
@common_options
def commissioning_test(
    ip: str | None,
    password: str | None,
    username: str,
    timeout: float,
) -> None:
    """Run the built-in commissioning test."""
    try:
        _, client = _client(ip, password, username, timeout)
        result = client.commissioning_test()
        console.print_json(json.dumps(result))
    except Exception as e:
        die(e)


@click.command("enable-ssh")
@common_options
@click.option(
    "--persistent/--no-persistent",
    default=True,
    show_default=True,
    help="Persist across reboots (touches .ssh-enabled marker)",
)
def enable_ssh(
    ip: str | None,
    password: str | None,
    username: str,
    timeout: float,
    persistent: bool,
) -> None:
    """Enable SSH access on the device."""
    try:
        _, client = _client(ip, password, username, timeout)
        client.enable_ssh(persistent=persistent)
        mode = "persistent" if persistent else "one-time"
        console.print(f"[green]SSH enabled ({mode}).[/green]")
    except Exception as e:
        die(e)


@click.command("disable-ssh")
@common_options
def disable_ssh(ip: str | None, password: str | None, username: str, timeout: float) -> None:
    """Stop sshd and remove the persistent SSH-enable marker."""
    try:
        _, client = _client(ip, password, username, timeout)
        client.disable_ssh()
        console.print("[green]SSH disabled.[/green]")
    except Exception as e:
        die(e)


def register(main: click.Group) -> None:
    """Register firmware and SSH commands."""
    main.add_command(check_update)
    main.add_command(upgrade)
    main.add_command(commissioning_test)
    main.add_command(enable_ssh)
    main.add_command(disable_ssh)
