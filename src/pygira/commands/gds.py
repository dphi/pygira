"""G1 GDS command group."""

import asyncio
import json
from contextlib import suppress

import click

from pygira.context import console, require_capability, resolve_login
from pygira.core.types import DeviceType
from pygira.devices.g1 import G1
from pygira.exceptions import UnsupportedCapabilityError
from pygira.gds import GdsClient
from pygira.options import common_options


def _print_json(value: object) -> None:
    console.print_json(json.dumps(value))


def _parse_key_value(pair: str) -> tuple[str, str]:
    if "=" not in pair:
        msg = "expected KEY=VALUE"
        raise click.BadParameter(msg)
    key, value = pair.split("=", 1)
    if not key:
        msg = "key must not be empty"
        raise click.BadParameter(msg)
    return key, value


def _require_g1(device_type: DeviceType) -> None:
    if device_type != DeviceType.G1:
        msg = "gds commands are supported for G1 only"
        raise UnsupportedCapabilityError(msg)


def _ctx_g1(ctx: click.Context) -> G1:
    return ctx.obj["g1"]


@click.group("gds")
@common_options
@click.pass_context
def gds(
    ctx: click.Context,
    ip: str | None,
    password: str | None,
    username: str | None,
    timeout: float,
) -> None:
    """Inspect and control G1 GDS WebSocket functions."""
    profile, ip, username, password = resolve_login(ip, username, password)
    _require_g1(profile.device_type)
    ctx.ensure_object(dict)
    ctx.obj.update({"profile": profile, "g1": G1(ip, username, password, timeout=timeout)})


@gds.command("process-view")
@click.pass_context
def process_view(ctx: click.Context) -> None:
    """Print the raw GDS process view."""
    _print_json(_ctx_g1(ctx).process_view())


@gds.command("device-config")
@click.pass_context
def device_config(ctx: click.Context) -> None:
    """Print the flat ipc device-config dictionary."""
    _print_json(_ctx_g1(ctx).device_config())


@gds.command("set-device-config")
@click.option("--set", "pairs", multiple=True, required=True, metavar="KEY=VALUE")
@click.pass_context
def set_device_config(ctx: click.Context, pairs: tuple[str, ...]) -> None:
    """Write one or more ipc device-config keys."""
    values = dict(_parse_key_value(pair) for pair in pairs)
    _ctx_g1(ctx).set_device_config(values)
    console.print("[green]Device config updated.[/green]")


@gds.group("app-value")
@click.pass_context
def app_value(ctx: click.Context) -> None:
    """Read or write persistent GDS app values."""


@app_value.command("get")
@click.option("--app-name", required=True, help="GDS app name")
@click.option("--key", required=True, help="App value key")
@click.pass_context
def app_value_get(ctx: click.Context, app_name: str, key: str) -> None:
    """Print a persistent GDS app value."""
    _print_json(_ctx_g1(ctx).app_value(app_name, key))


@app_value.command("set")
@click.option("--app-name", required=True, help="GDS app name")
@click.option("--key", required=True, help="App value key")
@click.option("--value", required=True, help="Value to store")
@click.pass_context
def app_value_set(ctx: click.Context, app_name: str, key: str, value: str) -> None:
    """Write a persistent GDS app value."""
    _ctx_g1(ctx).set_app_value(app_name, key, value)
    console.print("[green]App value updated.[/green]")


@gds.command("set-location")
@click.option("--lat", type=float, required=True, help="Latitude")
@click.option("--lon", type=float, required=True, help="Longitude")
@click.pass_context
def set_location(ctx: click.Context, lat: float, lon: float) -> None:
    """Write G1 latitude and longitude device-config keys."""
    _ctx_g1(ctx).set_location(lat, lon)
    console.print(f"[green]Location updated.[/green] lat={lat:.6f} lon={lon:.6f}")


@gds.command("tks-status")
@click.pass_context
def tks_status(ctx: click.Context) -> None:
    """Print the live TKS-IP connection status datapoint."""
    _print_json(_ctx_g1(ctx).tks_status())


@gds.command("configure-tks")
@click.option("--tks-ip", prompt="TKS-IP gateway IP address", help="TKS-IP gateway IP address")
@click.option("--tks-user", prompt="TKS-IP gateway username", help="TKS-IP gateway username")
@click.option(
    "--tks-pass",
    prompt="TKS-IP gateway password",
    hide_input=True,
    help="TKS-IP gateway password",
)
@click.pass_context
def configure_tks(ctx: click.Context, tks_ip: str, tks_user: str, tks_pass: str) -> None:
    """Configure TKS-IP credentials and trigger reconnect."""
    require_capability(ctx.obj["profile"], tks=True)
    _ctx_g1(ctx).configure_tks(tks_ip, tks_user, tks_pass)
    console.print("[green]TKS-IP gateway configured.[/green]")


@gds.command("listen")
@click.pass_context
def listen(ctx: click.Context) -> None:
    """Stream live GDS push events to stdout (Ctrl+C to stop)."""

    async def _stream() -> None:
        g1 = _ctx_g1(ctx)
        client = GdsClient(g1._host, g1._username, g1._password, timeout=g1._timeout)
        await client.connect()
        try:
            async for msg in client.listen():
                console.print_json(json.dumps(msg))
        finally:
            await client.close()

    with suppress(KeyboardInterrupt, click.exceptions.Abort):
        asyncio.run(_stream())


@gds.command("restart")
@click.pass_context
def restart(ctx: click.Context) -> None:
    """Restart the G1 via GDS."""
    _ctx_g1(ctx).restart()
    console.print("[green]Restart command sent.[/green]")


@gds.command("factory-reset")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt (for scripting)")
@click.pass_context
def factory_reset(ctx: click.Context, confirm: bool) -> None:
    """Reset the G1 to factory settings via GDS."""
    if not confirm:
        click.confirm("This will erase all configuration. Continue?", abort=True)

    _ctx_g1(ctx).factory_reset()
    console.print("[green]Factory reset command sent.[/green]")


def register(main: click.Group) -> None:
    """Register the G1 GDS command group."""
    main.add_command(gds)
