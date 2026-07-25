"""Firmware and SSH commands."""

import json
from pathlib import Path
from typing import cast

import click

from pygira.commands._target import resolve_device as _device
from pygira.context import console
from pygira.options import common_options


@click.command("check-update")
@common_options
def check_update(
    ip: str | None,
    password: str | None,
    username: str | None,
    timeout: float,
) -> None:
    """Check if an online firmware update is available."""
    result = _device(ip, password, username, timeout).check_update()
    console.print_json(json.dumps(result))


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
    username = cast("str | None", kwargs["username"])
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

    device = _device(ip, password, username, timeout)
    if firmware_file:
        console.print(f"Uploading firmware from {firmware_file!r}…")
        result = device.install_firmware(Path(firmware_file))
        console.print_json(json.dumps(result))
    else:
        console.print("Starting online firmware update…")
        result = device.trigger_online_update()
        console.print_json(json.dumps(result))

    if no_wait:
        return
    if not device.can_wait_for_upgrade:
        console.print(
            "[yellow]Firmware update triggered; this device does not expose progress.[/yellow]",
        )
        return
    console.print("Waiting for update to complete (up to 5 min)…")
    done = device.wait_for_completion()
    if done:
        console.print("[green]Firmware update completed.[/green]")
    else:
        console.print(
            "[yellow]Timed out waiting for completion — device may still be updating.[/yellow]",
        )


@click.command("commissioning-test")
@common_options
def commissioning_test(
    ip: str | None,
    password: str | None,
    username: str | None,
    timeout: float,
) -> None:
    """Run the built-in commissioning test."""
    result = _device(ip, password, username, timeout).commissioning_test()
    console.print_json(json.dumps(result))


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
    username: str | None,
    timeout: float,
    persistent: bool,
) -> None:
    """Enable SSH access on the device."""
    _device(ip, password, username, timeout).enable_ssh(persistent=persistent)
    mode = "persistent" if persistent else "one-time"
    console.print(f"[green]SSH enabled ({mode}).[/green]")


@click.command("disable-ssh")
@common_options
def disable_ssh(
    ip: str | None,
    password: str | None,
    username: str | None,
    timeout: float,
) -> None:
    """Stop sshd and remove the persistent SSH-enable marker."""
    _device(ip, password, username, timeout).disable_ssh()
    console.print("[green]SSH disabled.[/green]")


def register(main: click.Group) -> None:
    """Register firmware and SSH commands."""
    main.add_command(check_update)
    main.add_command(upgrade)
    main.add_command(commissioning_test)
    main.add_command(enable_ssh)
    main.add_command(disable_ssh)
