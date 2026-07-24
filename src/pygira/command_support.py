"""CLI command-to-device applicability metadata."""

from collections.abc import Iterator
from dataclasses import dataclass

import click
from rich.table import Table

from pygira.context import console


@dataclass(frozen=True)
class DeviceSupport:
    """Device families on which a command can be invoked."""

    devices: tuple[str, ...]

    @property
    def display(self) -> str:
        """Human-readable device list."""
        return ", ".join(self.devices)

    @property
    def tag(self) -> str:
        """Compact help-list tag."""
        return "/".join(self.devices)


G1 = DeviceSupport(("G1",))
X1 = DeviceSupport(("X1",))
TKS_IP = DeviceSupport(("TKS-IP",))
G1_X1 = DeviceSupport(("G1", "X1"))
ALL_DEVICES = DeviceSupport(("G1", "X1", "TKS-IP"))
LOCAL = DeviceSupport(("Local",))

ROOT_SUPPORT: dict[str, DeviceSupport] = {
    "activate-tks-web": TKS_IP,
    "bootstrap": G1_X1,
    "check-update": G1_X1,
    "commissioning-test": G1_X1,
    "config": ALL_DEVICES,
    "detect": G1_X1,
    "diagnostics": G1_X1,
    "disable-ssh": G1_X1,
    "enable-ssh": G1_X1,
    "factory-reset": G1_X1,
    "gds": G1,
    "get-logging": X1,
    "get-ntp": G1_X1,
    "info": G1_X1,
    "pull-logs": G1_X1,
    "restart": G1_X1,
    "set-ip": G1_X1,
    "set-logging": X1,
    "set-ntp": G1_X1,
    "set-tks": G1,
    "set-weather": G1,
    "tail-logs": G1_X1,
    "tks-backup-restore": TKS_IP,
    "tks-backup-save": TKS_IP,
    "tks-firmware-update": TKS_IP,
    "tks-info": TKS_IP,
    "tks-pull-logs": TKS_IP,
    "tks-status": TKS_IP,
    "tks-tail-logs": TKS_IP,
    "upgrade": G1_X1,
    "x1-export-program": X1,
    "x1-import-program": X1,
    "command-support": LOCAL,
}


def _commands(
    group: click.Group,
    *,
    prefix: tuple[str, ...] = (),
    inherited: DeviceSupport | None = None,
) -> Iterator[tuple[tuple[str, ...], click.Command, DeviceSupport | None]]:
    for name, command in sorted(group.commands.items()):
        path = (*prefix, name)
        support = ROOT_SUPPORT.get(name) if not prefix else inherited
        yield path, command, support
        if isinstance(command, click.Group):
            yield from _commands(command, prefix=path, inherited=support)


def missing_support_paths(group: click.Group) -> list[str]:
    """Return root commands that do not declare device applicability."""
    return sorted(set(group.commands) - set(ROOT_SUPPORT))


def annotate_help(group: click.Group) -> None:
    """Add device tags to command listings and detailed command help."""
    missing = missing_support_paths(group)
    if missing:
        names = ", ".join(missing)
        msg = f"Commands missing device applicability metadata: {names}"
        raise RuntimeError(msg)

    for _path, command, support in _commands(group):
        if support is None:
            continue
        description = command.help or ""
        supported_line = f"Supported devices: {support.display}."
        if supported_line not in description:
            command.help = f"{description}\n\n{supported_line}".strip()
        summary = command.short_help or description.split("\n", 1)[0]
        command.short_help = f"[{support.tag}] {summary}"


def register(main: click.Group) -> None:
    """Register the consolidated command support table."""

    @main.command("command-support")
    def command_support() -> None:
        """List every command and the devices it supports."""
        table = Table(title="Command support")
        table.add_column("Command", style="bold")
        table.add_column("Supported devices")
        for path, command, support in _commands(main):
            if isinstance(command, click.Group) or support is None:
                continue
            table.add_row(" ".join(path), support.display)
        console.print(table)
