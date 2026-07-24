"""CLI command-to-device applicability metadata."""

from collections.abc import Iterator
from copy import copy
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
G1_TKS_IP = DeviceSupport(("G1", "TKS-IP"))
ALL_DEVICES = DeviceSupport(("G1", "X1", "TKS-IP"))
LOCAL = DeviceSupport(("Local",))

COMMAND_MOVES: dict[str, tuple[str, ...]] = {
    "detect": ("device", "detect"),
    "info": ("device", "info"),
    "diagnostics": ("device", "diagnostics"),
    "commissioning-test": ("device", "commissioning-test"),
    "restart": ("device", "restart"),
    "factory-reset": ("device", "factory-reset"),
    "get-ip": ("network", "get"),
    "set-ip": ("network", "set"),
    "get-ntp": ("ntp", "get"),
    "set-ntp": ("ntp", "set"),
    "check-update": ("firmware", "check"),
    "upgrade": ("firmware", "upgrade"),
    "enable-ssh": ("ssh", "enable"),
    "disable-ssh": ("ssh", "disable"),
    "get-logging": ("logging", "get"),
    "set-logging": ("logging", "set"),
    "pull-logs": ("logs", "pull"),
    "tail-logs": ("logs", "tail"),
    "set-weather": ("weather", "set"),
    "set-tks": ("tks", "configure"),
    "activate-tks-web": ("tks", "activate"),
    "tks-status": ("tks", "status"),
    "tks-info": ("tks", "info"),
    "tks-backup-save": ("tks", "backup", "save"),
    "tks-backup-restore": ("tks", "backup", "restore"),
    "tks-firmware-update": ("tks", "firmware", "update"),
    "x1-export-program": ("program", "export"),
    "x1-import-program": ("program", "import"),
}

LEGACY_ONLY: dict[str, str] = {
    "tks-pull-logs": "logs pull",
    "tks-tail-logs": "logs tail",
}

GROUP_HELP = {
    "device": "Inspect and maintain a device.",
    "network": "Read or change network settings.",
    "ntp": "Read or change time synchronization.",
    "firmware": "Check and install device firmware.",
    "ssh": "Control device SSH access.",
    "logging": "Read or change device logging verbosity.",
    "logs": "Download or follow diagnostic logs.",
    "weather": "Configure the G1 weather display.",
    "tks": "Manage G1 door communication and TKS-IP gateways.",
    "backup": "Save or restore a TKS-IP configuration backup.",
    "program": "Export or import an X1 program.",
}

PATH_SUPPORT: dict[tuple[str, ...], DeviceSupport] = {
    ("bootstrap",): G1_X1,
    ("command-support",): LOCAL,
    ("config",): ALL_DEVICES,
    ("device",): ALL_DEVICES,
    ("device", "commissioning-test"): G1_X1,
    ("device", "diagnostics"): G1_X1,
    ("device", "factory-reset"): G1_X1,
    ("device", "info"): G1_X1,
    ("device", "restart"): G1_X1,
    ("firmware",): G1_X1,
    ("gds",): G1,
    ("logging",): G1_X1,
    ("logs",): ALL_DEVICES,
    ("network",): G1_X1,
    ("ntp",): G1_X1,
    ("program",): X1,
    ("ssh",): G1_X1,
    ("tks",): G1_TKS_IP,
    ("tks", "configure"): G1,
    ("tks", "activate"): TKS_IP,
    ("tks", "status"): TKS_IP,
    ("tks", "info"): TKS_IP,
    ("tks", "backup"): TKS_IP,
    ("tks", "firmware"): TKS_IP,
    ("weather",): G1,
}


def _ensure_group(parent: click.Group, name: str) -> click.Group:
    existing = parent.commands.get(name)
    if isinstance(existing, click.Group):
        return existing
    group = click.Group(name=name, help=GROUP_HELP[name])
    parent.add_command(group)
    return group


def _hide_legacy_alias(
    main: click.Group,
    command: click.Command,
    old: str,
    replacement: str,
) -> None:
    alias = copy(command)
    alias.hidden = True
    alias.deprecated = f"Use 'pygira {replacement}' instead."
    main.add_command(alias, old)


def organize_commands(main: click.Group) -> None:
    """Expose noun-first groups while keeping hidden deprecated flat aliases."""
    for old, path in COMMAND_MOVES.items():
        command = main.commands.pop(old, None)
        if command is None:
            continue
        _hide_legacy_alias(main, command, old, " ".join(path))
        parent = main
        for group_name in path[:-1]:
            parent = _ensure_group(parent, group_name)
        parent.add_command(command, path[-1])

    for old, replacement in LEGACY_ONLY.items():
        command = main.commands.get(old)
        if command is None:
            continue
        command.hidden = True
        command.deprecated = f"Use 'pygira {replacement}' instead."


def _commands(
    group: click.Group,
    *,
    prefix: tuple[str, ...] = (),
    inherited: DeviceSupport | None = None,
) -> Iterator[tuple[tuple[str, ...], click.Command, DeviceSupport | None]]:
    for name, command in sorted(group.commands.items()):
        if command.hidden:
            continue
        path = (*prefix, name)
        support = PATH_SUPPORT.get(path, inherited)
        yield path, command, support
        if isinstance(command, click.Group):
            yield from _commands(command, prefix=path, inherited=support)


def missing_support_paths(group: click.Group) -> list[str]:
    """Return visible command paths that do not declare device applicability."""
    return [" ".join(path) for path, _command, support in _commands(group) if support is None]


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
