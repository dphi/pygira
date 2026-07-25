"""Reusable click option decorators."""

from collections.abc import Callable
from typing import ParamSpec, TypeVar

import click

from pygira.core.types import DeviceType

P = ParamSpec("P")
R = TypeVar("R")
ClickCommand = Callable[P, R]


def _remember_target_option(
    key: str,
    transform: Callable[[str], object] | None = None,
) -> Callable[[click.Context, click.Parameter, str | None], str | None]:
    """Store a command-local target option in the shared root context."""

    def callback(
        ctx: click.Context,
        _param: click.Parameter,
        value: str | None,
    ) -> str | None:
        if value is not None:
            root = ctx.find_root()
            root.ensure_object(dict)
            root.obj[key] = transform(value) if transform else value
        return value

    return callback


def selection_options(f: ClickCommand[P, R]) -> ClickCommand[P, R]:
    """Allow configured-device selection directly on an operational command."""
    options = [
        click.option(
            "--name",
            "selected_device_name",
            default=None,
            expose_value=False,
            callback=_remember_target_option("device_name"),
            help="Configured device name",
        ),
        click.option(
            "--location",
            "selected_location",
            default=None,
            expose_value=False,
            callback=_remember_target_option("location"),
            help="Configured location key or name",
        ),
        click.option(
            "--config",
            "selected_config_path",
            default=None,
            expose_value=False,
            callback=_remember_target_option("config_path"),
            type=click.Path(dir_okay=False),
            metavar="FILE",
            help="Device configuration file (default: devices.toml)",
        ),
        click.option(
            "--device",
            "selected_device_type",
            default=None,
            expose_value=False,
            callback=_remember_target_option("requested_device", DeviceType),
            type=click.Choice(["g1", "x1", "tks-ip"]),
            help="Expected device type (otherwise auto-detect)",
        ),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def common_options(f: ClickCommand[P, R]) -> ClickCommand[P, R]:
    """Attach target selection, login, and timeout options to a command."""
    decorated = click.option(
        "--username",
        default=None,
        help="Device username (default: device for G1 and X1)",
    )(
        click.option(
            "--password",
            default=None,
            hide_input=True,
            help="Device admin password",
        )(
            click.option(
                "--ip",
                default=None,
                help="Direct device IP address (skips configuration selection)",
            )(
                click.option(
                    "--timeout",
                    default=60.0,
                    show_default=True,
                    type=float,
                    help="Request timeout in seconds",
                )(f),
            ),
        ),
    )
    return selection_options(decorated)


def network_options(f: ClickCommand[P, R]) -> ClickCommand[P, R]:
    """Attach DHCP/static network configuration options to a command."""
    return click.option(
        "--dns2",
        default=None,
        metavar="DNS",
        help="Secondary DNS",
    )(
        click.option(
            "--dns1",
            default=None,
            metavar="DNS",
            help="Primary DNS",
        )(
            click.option(
                "--gateway",
                default=None,
                metavar="GW",
                help="Default gateway",
            )(
                click.option(
                    "--subnet",
                    default=None,
                    metavar="MASK",
                    help="Subnet mask",
                )(
                    click.option(
                        "--static-ip",
                        default=None,
                        metavar="IP",
                        help="Static IP address",
                    )(
                        click.option(
                            "--dhcp/--no-dhcp",
                            default=None,
                            help="Use DHCP (default: keep current)",
                        )(f),
                    ),
                ),
            ),
        ),
    )
