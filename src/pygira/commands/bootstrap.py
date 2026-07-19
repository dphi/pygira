"""Bootstrap command."""

import json
import uuid
from dataclasses import dataclass
from typing import cast

import click

from pygira import api as api_mod
from pygira import weather as weather_mod
from pygira.context import console, err, resolve_login
from pygira.devices.base import DeviceProfile
from pygira.gds import GdsClient, run_gds
from pygira.models import NetworkConfig, WeatherStation
from pygira.options import common_options, network_options

_STEP_HINTS = (
    "  Step 1 (network): --dhcp/--no-dhcp, --static-ip, --subnet, --gateway, --dns1, --dns2",
    "  Step 2 (TKS-IP):  --tks-ip, --tks-user, --tks-pass",
    "  Step 3 (weather): --weather-zip [--weather-country]",
)


@dataclass(frozen=True)
class BootstrapOptions:
    """Parsed bootstrap command options."""

    ip: str | None
    password: str | None
    username: str | None
    timeout: float
    dhcp: bool | None
    static_ip: str | None
    subnet: str | None
    gateway: str | None
    dns1: str | None
    dns2: str | None
    tks_ip: str | None
    tks_user: str | None
    tks_pass: str | None
    weather_zip: str | None
    weather_country: str

    @classmethod
    def from_kwargs(cls, kwargs: dict[str, object]) -> "BootstrapOptions":
        """Build typed options from Click keyword arguments."""
        return cls(
            ip=cast("str | None", kwargs["ip"]),
            password=cast("str | None", kwargs["password"]),
            username=cast("str | None", kwargs["username"]),
            timeout=cast("float", kwargs["timeout"]),
            dhcp=cast("bool | None", kwargs["dhcp"]),
            static_ip=cast("str | None", kwargs["static_ip"]),
            subnet=cast("str | None", kwargs["subnet"]),
            gateway=cast("str | None", kwargs["gateway"]),
            dns1=cast("str | None", kwargs["dns1"]),
            dns2=cast("str | None", kwargs["dns2"]),
            tks_ip=cast("str | None", kwargs["tks_ip"]),
            tks_user=cast("str | None", kwargs["tks_user"]),
            tks_pass=cast("str | None", kwargs["tks_pass"]),
            weather_zip=cast("str | None", kwargs["weather_zip"]),
            weather_country=cast("str", kwargs["weather_country"]),
        )

    @property
    def has_network_flags(self) -> bool:
        """Return whether any network option was provided."""
        return any(
            x is not None
            for x in [self.dhcp, self.static_ip, self.subnet, self.gateway, self.dns1, self.dns2]
        )


def _network_config(opts: BootstrapOptions, current: dict) -> NetworkConfig:
    use_dhcp = opts.dhcp if opts.dhcp is not None else current.get("Dhcp", False)
    return NetworkConfig(
        dhcp=use_dhcp,
        ip_address=opts.static_ip or current.get("IpAddress", ""),
        subnet_mask=opts.subnet or current.get("SubnetMask", ""),
        default_gateway=opts.gateway or current.get("DefaultGateway", ""),
        primary_dns=opts.dns1 or current.get("NameServer", ""),
        secondary_dns=opts.dns2 or current.get("SecondaryDns", ""),
    )


def _configure_network(
    profile: DeviceProfile,
    opts: BootstrapOptions,
    ip: str,
    username: str,
    password: str,
) -> bool:
    if not opts.has_network_flags:
        console.print("[dim]Step 1: Skipped — pass network flags to enable:[/dim]")
        console.print(f"[dim]{_STEP_HINTS[0]}[/dim]")
        return False

    console.print("[bold]Step 1:[/bold] Configuring network…")
    try:
        api_prefix = profile.api_prefix
        client = api_mod.ApiClient(
            ip,
            username,
            password,
            api_prefix=api_prefix,
            timeout=opts.timeout,
        )
        current = client.get_device_info(force_long=True).get("data", {})
        client.set_ip_config(_network_config(opts, current))
    except Exception as e:
        err.print(f"  [red]✗ IP config failed:[/red] {e}")
        return False
    else:
        console.print("  [green]✓[/green] IP config set")
        return True


def _configure_tks(
    profile: DeviceProfile,
    opts: BootstrapOptions,
    ip: str,
    username: str,
    password: str,
) -> bool:
    if not (opts.tks_ip and opts.tks_user and opts.tks_pass):
        console.print("[dim]Step 2: Skipped — pass gateway flags to enable:[/dim]")
        console.print(f"[dim]{_STEP_HINTS[1]}[/dim]")
        return False

    console.print("[bold]Step 2:[/bold] Configuring TKS-IP gateway…")
    capabilities = profile.capabilities
    display_name = profile.display_name
    if not capabilities.tks:
        err.print(f"  [red]✗ TKS-IP unsupported on {display_name}[/red]")
        return False

    async def _tks(client: GdsClient) -> None:
        await client.configure_tks(opts.tks_ip or "", opts.tks_user or "", opts.tks_pass or "")

    try:
        run_gds(ip, username, password, _tks, timeout=opts.timeout)
    except Exception as e:
        err.print(f"  [red]✗ TKS-IP failed:[/red] {e}")
        return False
    else:
        console.print("  [green]✓[/green] TKS-IP configured")
        return True


def _require_station(station: WeatherStation | None, zip_code: str) -> WeatherStation:
    if station is None:
        msg = f"No station found for {zip_code!r}"
        raise ValueError(msg)
    return station


def _weather_settings_json(station: WeatherStation) -> str:
    station.guid = str(uuid.uuid4())
    return json.dumps(
        {
            "acceptedLicense": True,
            "weatherStations": [
                {
                    "weatherStationId": station.station_id,
                    "label": station.label,
                    "guid": station.guid,
                },
            ],
        },
    )


def _configure_weather(
    profile: DeviceProfile,
    opts: BootstrapOptions,
    ip: str,
    username: str,
    password: str,
) -> bool:
    if not opts.weather_zip:
        console.print("[dim]Step 3: Skipped — pass weather flags to enable:[/dim]")
        console.print(f"[dim]{_STEP_HINTS[2]}[/dim]")
        return False

    console.print("[bold]Step 3:[/bold] Configuring weather…")
    capabilities = profile.capabilities
    display_name = profile.display_name
    if not capabilities.weather:
        err.print(f"  [red]✗ Weather unsupported on {display_name}[/red]")
        return False

    try:
        station = _require_station(
            weather_mod.find_station(opts.weather_zip, opts.weather_country),
            opts.weather_zip,
        )
        settings_json = _weather_settings_json(station)

        async def _weather(client: GdsClient) -> None:
            await client.set_app_value("Gira.G1", "weather.settings", settings_json)

        run_gds(ip, username, password, _weather, timeout=opts.timeout)
    except Exception as e:
        err.print(f"  [red]✗ Weather failed:[/red] {e}")
        return False
    else:
        console.print(f"  [green]✓[/green] Weather set to {station.label} ({station.station_id})")
        return True


@click.command()
@common_options
@network_options
@click.option("--tks-ip", default=None, help="TKS-IP gateway IP address")
@click.option("--tks-user", default=None, help="TKS-IP gateway username")
@click.option("--tks-pass", default=None, help="TKS-IP gateway password")
@click.option("--weather-zip", default=None, help="Postal code for weather station")
@click.option(
    "--weather-country",
    default="DE",
    show_default=True,
    help="Country code for weather lookup",
)
def bootstrap(**kwargs: object) -> None:
    """Full bootstrap: set IP config, TKS-IP, and weather in one step."""
    opts = BootstrapOptions.from_kwargs(kwargs)
    profile, ip, username, password = resolve_login(opts.ip, opts.username, opts.password)
    steps_done = sum(
        [
            _configure_network(profile, opts, ip, username, password),
            _configure_tks(profile, opts, ip, username, password),
            _configure_weather(profile, opts, ip, username, password),
        ],
    )

    console.print(f"\nDone — [bold]{steps_done}[/bold] step(s) completed.")
    if steps_done == 0:
        console.print(
            "[dim]Run [bold]pygira bootstrap --help[/bold] to see all available flags.[/dim]",
        )


def register(main: click.Group) -> None:
    """Register bootstrap command."""
    main.add_command(bootstrap)
