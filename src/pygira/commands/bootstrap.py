"""Bootstrap command."""

from dataclasses import dataclass
from enum import Enum
from typing import cast

import click

from pygira import weather as weather_mod
from pygira.context import console, err, resolve_login
from pygira.devices.base import DeviceProfile, ResolvedTarget
from pygira.devices.registry import create_device
from pygira.exceptions import InvalidInputError, PygiraError
from pygira.gds import GdsClient, run_gds
from pygira.models import NetworkConfig, WeatherStation
from pygira.operations import NetworkPatch, build_weather_settings, merge_network_config
from pygira.options import common_options, network_options

_STEP_HINTS = (
    "  Step 1 (network): --dhcp/--no-dhcp, --static-ip, --subnet, --gateway, --dns1, --dns2",
    "  Step 2 (TKS-IP):  --tks-ip, --tks-user, --tks-pass",
    "  Step 3 (weather): --weather-zip [--weather-country]",
)


class StepStatus(Enum):
    """Outcome of one bootstrap step."""

    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class BootstrapStep:
    """Structured bootstrap step outcome."""

    name: str
    status: StepStatus
    detail: str = ""

    @property
    def succeeded(self) -> bool:
        """Return whether the step completed successfully."""
        return self.status == StepStatus.SUCCEEDED


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
    return merge_network_config(
        current,
        NetworkPatch(
            dhcp=opts.dhcp,
            ip_address=opts.static_ip,
            subnet_mask=opts.subnet,
            default_gateway=opts.gateway,
            primary_dns=opts.dns1,
            secondary_dns=opts.dns2,
        ),
    )


def _configure_network(
    profile: DeviceProfile,
    opts: BootstrapOptions,
    ip: str,
    username: str,
    password: str,
) -> BootstrapStep:
    if not opts.has_network_flags:
        console.print("[dim]Step 1: Skipped — pass network flags to enable:[/dim]")
        console.print(f"[dim]{_STEP_HINTS[0]}[/dim]")
        return BootstrapStep("network", StepStatus.SKIPPED)

    console.print("[bold]Step 1:[/bold] Configuring network…")
    try:
        device = create_device(
            ResolvedTarget(
                profile=profile,
                host=ip,
                username=username,
                password=password,
                timeout=opts.timeout,
            ),
        )
        current = device.device_info(long=True).get("data", {})
        device.set_ip(_network_config(opts, current))
    except PygiraError as e:
        err.print(f"  [red]✗ IP config failed:[/red] {e}")
        return BootstrapStep("network", StepStatus.FAILED, str(e))
    else:
        console.print("  [green]✓[/green] IP config set")
        return BootstrapStep("network", StepStatus.SUCCEEDED)


def _configure_tks(
    profile: DeviceProfile,
    opts: BootstrapOptions,
    ip: str,
    username: str,
    password: str,
) -> BootstrapStep:
    if not (opts.tks_ip and opts.tks_user and opts.tks_pass):
        console.print("[dim]Step 2: Skipped — pass gateway flags to enable:[/dim]")
        console.print(f"[dim]{_STEP_HINTS[1]}[/dim]")
        return BootstrapStep("tks", StepStatus.SKIPPED)

    console.print("[bold]Step 2:[/bold] Configuring TKS-IP gateway…")
    capabilities = profile.capabilities
    display_name = profile.display_name
    if not capabilities.tks:
        err.print(f"  [red]✗ TKS-IP unsupported on {display_name}[/red]")
        return BootstrapStep("tks", StepStatus.FAILED, f"unsupported on {display_name}")

    async def _tks(client: GdsClient) -> None:
        await client.configure_tks(opts.tks_ip or "", opts.tks_user or "", opts.tks_pass or "")

    try:
        run_gds(ip, username, password, _tks, timeout=opts.timeout)
    except PygiraError as e:
        err.print(f"  [red]✗ TKS-IP failed:[/red] {e}")
        return BootstrapStep("tks", StepStatus.FAILED, str(e))
    else:
        console.print("  [green]✓[/green] TKS-IP configured")
        return BootstrapStep("tks", StepStatus.SUCCEEDED)


def _require_station(station: WeatherStation | None, zip_code: str) -> WeatherStation:
    if station is None:
        msg = f"No station found for {zip_code!r}"
        raise InvalidInputError(msg)
    return station


def _configure_weather(
    profile: DeviceProfile,
    opts: BootstrapOptions,
    ip: str,
    username: str,
    password: str,
) -> BootstrapStep:
    if not opts.weather_zip:
        console.print("[dim]Step 3: Skipped — pass weather flags to enable:[/dim]")
        console.print(f"[dim]{_STEP_HINTS[2]}[/dim]")
        return BootstrapStep("weather", StepStatus.SKIPPED)

    console.print("[bold]Step 3:[/bold] Configuring weather…")
    capabilities = profile.capabilities
    display_name = profile.display_name
    if not capabilities.weather:
        err.print(f"  [red]✗ Weather unsupported on {display_name}[/red]")
        return BootstrapStep("weather", StepStatus.FAILED, f"unsupported on {display_name}")

    try:
        station = _require_station(
            weather_mod.find_station(opts.weather_zip, opts.weather_country),
            opts.weather_zip,
        )
        settings_json = build_weather_settings(station)

        async def _weather(client: GdsClient) -> None:
            await client.set_app_value("Gira.G1", "weather.settings", settings_json)

        run_gds(ip, username, password, _weather, timeout=opts.timeout)
    except PygiraError as e:
        err.print(f"  [red]✗ Weather failed:[/red] {e}")
        return BootstrapStep("weather", StepStatus.FAILED, str(e))
    else:
        console.print(f"  [green]✓[/green] Weather set to {station.label} ({station.station_id})")
        return BootstrapStep("weather", StepStatus.SUCCEEDED)


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
    steps = [
        _configure_network(profile, opts, ip, username, password),
        _configure_tks(profile, opts, ip, username, password),
        _configure_weather(profile, opts, ip, username, password),
    ]
    steps_done = sum(step.succeeded for step in steps)
    failures = [step.name for step in steps if step.status == StepStatus.FAILED]

    console.print(f"\nDone — [bold]{steps_done}[/bold] step(s) completed.")
    if failures:
        console.print(f"[yellow]Failed steps:[/yellow] {', '.join(failures)}")
    if steps_done == 0:
        console.print(
            "[dim]Run [bold]pygira bootstrap --help[/bold] to see all available flags.[/dim]",
        )


def register(main: click.Group) -> None:
    """Register bootstrap command."""
    main.add_command(bootstrap)
