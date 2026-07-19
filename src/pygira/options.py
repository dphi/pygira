"""Reusable click option decorators."""

from collections.abc import Callable
from typing import ParamSpec, TypeVar

import click

P = ParamSpec("P")
R = TypeVar("R")
ClickCommand = Callable[P, R]


def common_options(f: ClickCommand[P, R]) -> ClickCommand[P, R]:
    """Attach --ip, --password, --username, and --timeout options to a command."""
    return click.option(
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
                help="Device IP address",
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
