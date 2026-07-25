"""Maintenance and integration commands."""

import io
import json
import time
import uuid
import zipfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import ParamSpec, TypeVar, cast

import click
from rich.table import Table

from pygira import api as api_mod
from pygira import config_service as cs
from pygira import weather as weather_mod
from pygira.commands._target import resolve_device as _device_client
from pygira.context import (
    _device_type,
    _prompt_configured_device,
    _selected_device,
    console,
    find_tks_aes_key,
    require_capability,
    resolve_login,
    resolve_tks_aes_key,
    resolve_tks_ip,
    resolve_tks_login,
)
from pygira.core.detect import detect_device_type
from pygira.core.types import DeviceType
from pygira.devices.base import DeviceProfile
from pygira.exceptions import TransportError, UnsupportedCapabilityError
from pygira.gds import GdsClient, run_gds
from pygira.options import common_options, selection_options
from pygira.prompting import TypedAddress
from pygira.tks_web import TksWebClient

P = ParamSpec("P")
R = TypeVar("R")
ClickCommand = Callable[P, R]
NORMAL_SYSLOG_SEVERITY = 4
SECONDS_PER_MINUTE = 60
MAX_CLOCK_SKEW_SECONDS = 120
MIN_FREE_MEMORY_KIB = 4096
TKS_WEB_LOGIN_ATTEMPTS = 2


def _tks_ip_option(f: ClickCommand[P, R]) -> ClickCommand[P, R]:
    """Expose the common --ip spelling while retaining --tks-ip compatibility."""
    return click.option(
        "--ip",
        "--tks-ip",
        "tks_ip",
        default=None,
        help="Direct TKS-IP gateway address (skips configuration selection)",
    )(f)


@dataclass(frozen=True)
class _LogTarget:
    """Log-source family and any host resolved while detecting it."""

    device_type: DeviceType
    host: str | None


def _tks_login_options(f: ClickCommand[P, R]) -> ClickCommand[P, R]:
    decorated = click.option(
        "--tks-pass",
        default=None,
        hide_input=True,
        help="TKS-IP gateway password",
    )(f)
    decorated = click.option(
        "--tks-user",
        default=None,
        help="TKS-IP gateway username",
    )(decorated)
    decorated = _tks_ip_option(decorated)
    return selection_options(decorated)


def _require_x1(profile: DeviceProfile, command_name: str) -> None:
    if profile.device_type != DeviceType.X1:
        msg = f"{command_name} is supported on X1 only"
        raise UnsupportedCapabilityError(msg)


def _login_tks_web(host: str, username: str, password: str) -> TksWebClient:
    """Start the on-demand web app and return an authenticated session."""
    last_error: TransportError | None = None
    with console.status("[bold]Opening TKS-IP web interface…[/bold]"):
        for _ in range(TKS_WEB_LOGIN_ATTEMPTS):
            cs.activate_tks_webinterface(host)
            client = TksWebClient(host, persist_session=True)
            try:
                client.login(username, password)
            except TransportError as exc:
                last_error = exc
            else:
                return client
    assert last_error is not None
    raise last_error


def _check_status(ok: bool, *, warning: bool = False) -> str:
    if ok:
        return "[green]OK[/green]"
    return "[yellow]Warning[/yellow]" if warning else "[red]Failed[/red]"


def _format_age(timestamp: datetime | None, reference: datetime) -> str:
    if timestamp is None:
        return "unknown age"
    seconds = max(0, int((reference - timestamp).total_seconds()))
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds}s ago"
    return f"{seconds // SECONDS_PER_MINUTE}m ago"


def _add_tks_gateway_rows(table: Table, host: str, status: cs.TksDeviceStatus) -> None:
    identity_ok = status.bootstrap_reachable and status.identified_as_tks_ip
    if not status.bootstrap_reachable:
        bootstrap_detail = f"port 80 is unreachable at {host}"
    elif status.identified_as_tks_ip:
        bootstrap_detail = f"TKS-IP bootstrap API (HTTP {status.http_status})"
    else:
        bootstrap_detail = f"unexpected page (HTTP {status.http_status})"
    table.add_row("Gateway", _check_status(identity_ok), bootstrap_detail)

    if status.device_time is not None and status.clock_skew_seconds is not None:
        skew = abs(status.clock_skew_seconds)
        clock_ok = skew < MAX_CLOCK_SKEW_SECONDS
        table.add_row(
            "Clock",
            _check_status(clock_ok, warning=True),
            f"{status.device_time.isoformat()} ({skew:.1f}s skew)",
        )
    else:
        table.add_row("Clock", "[dim]Unknown[/dim]", "no HTTP Date header")


def _add_tks_listener_rows(table: Table, status: cs.TksDeviceStatus) -> None:
    table.add_row(
        "SDA listener",
        _check_status(status.sda_listener_reachable, warning=True),
        "TCP 50500 accepts connections; cloud connection is not verified"
        if status.sda_listener_reachable
        else "TCP 50500 is not accepting connections",
    )
    table.add_row(
        "SSH listener",
        _check_status(status.ssh_reachable, warning=True),
        "TCP 222 accepts connections"
        if status.ssh_reachable
        else "TCP 222 is not accepting connections",
    )


def _resource_detail(
    diagnostics: cs.TksRuntimeDiagnostics,
    reference: datetime,
) -> str:
    parts = []
    if diagnostics.free_memory_kib is not None:
        parts.append(f"{diagnostics.free_memory_kib / 1024:.1f} MiB free")
    if diagnostics.load_averages is not None:
        parts.append(
            "load " + "/".join(f"{value:.2f}" for value in diagnostics.load_averages),
        )
    if diagnostics.runnable_tasks is not None and diagnostics.total_tasks is not None:
        parts.append(f"tasks {diagnostics.runnable_tasks}/{diagnostics.total_tasks}")
    parts.append(_format_age(diagnostics.observed_at, reference))
    return "; ".join(parts)


def _sip_detail(diagnostics: cs.TksRuntimeDiagnostics, reference: datetime) -> str:
    parts = [
        "tuerko ↔ sipd keepalive succeeded"
        if diagnostics.sip_responsive
        else "tuerko ↔ sipd keepalive failed",
        _format_age(diagnostics.sip_observed_at, reference),
    ]
    if diagnostics.sip_pid is not None:
        parts.append(f"PID {diagnostics.sip_pid}")
    if diagnostics.sip_memory_kib is not None:
        parts.append(f"{diagnostics.sip_memory_kib / 1024:.1f} MiB")
    return "; ".join(parts)


def _add_tks_runtime_rows(
    table: Table,
    diagnostics: cs.TksRuntimeDiagnostics,
    reference: datetime,
) -> None:
    memory_ok = (
        diagnostics.free_memory_kib is None or diagnostics.free_memory_kib >= MIN_FREE_MEMORY_KIB
    )
    table.add_row(
        "Runtime",
        _check_status(memory_ok, warning=True),
        _resource_detail(diagnostics, reference),
    )

    if diagnostics.sip_responsive is None:
        table.add_row("SIP daemon", "[dim]Unknown[/dim]", "no SIP keepalive found")
    else:
        table.add_row(
            "SIP daemon",
            _check_status(diagnostics.sip_responsive, warning=True),
            _sip_detail(diagnostics, reference),
        )

    if diagnostics.tks_bus_state is None:
        table.add_row("TKS bus", "[dim]Unknown[/dim]", "no bus state found")
    else:
        table.add_row(
            "TKS bus",
            "[green]Observed[/green]",
            f"raw state 0x{diagnostics.tks_bus_state}; "
            f"{_format_age(diagnostics.tks_bus_observed_at, reference)}",
        )

    failures_ok = not diagnostics.recent_failures
    table.add_row(
        "Recent failures",
        _check_status(failures_ok, warning=True),
        "none in the latest 15-minute log window"
        if failures_ok
        else ", ".join(diagnostics.recent_failures),
    )


def _tks_status_summary(host: str, status: cs.TksDeviceStatus) -> str:
    diagnostics = status.diagnostics
    if not status.bootstrap_reachable:
        return f"[red]TKS-IP gateway unavailable at {host}[/red]"
    if not status.identified_as_tks_ip:
        return f"[yellow]Unexpected HTTP service at {host}[/yellow]"
    if diagnostics is None:
        return f"[green]TKS-IP gateway reachable at {host}[/green]"
    if diagnostics.recent_failures or diagnostics.sip_responsive is False:
        return f"[yellow]TKS-IP gateway needs attention at {host}[/yellow]"
    return f"[green]TKS-IP gateway operational at {host}[/green]"


def _print_tks_device_status(host: str, status: cs.TksDeviceStatus) -> None:
    reference = status.device_time or datetime.now(timezone.utc)
    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Details")
    _add_tks_gateway_rows(table, host, status)
    _add_tks_listener_rows(table, status)

    if status.diagnostics is None:
        detail = status.diagnostics_error or "configure an AES key for log-backed checks"
        table.add_row("Runtime", "[dim]Not checked[/dim]", detail)
    else:
        _add_tks_runtime_rows(table, status.diagnostics, reference)

    console.print(_tks_status_summary(host, status))
    console.print(table)
    console.print("[dim]Port 8080 was not contacted.[/dim]")


def register(main: click.Group) -> None:
    """Register maintenance and integration commands."""
    _register_set_tks(main)
    _register_tks_web(main)
    _register_tks_backup_save(main)
    _register_tks_backup_restore(main)
    _register_tks_firmware_update(main)
    _register_tks_device_info(main)
    _register_tks_sip_info(main)
    _register_weather(main)
    _register_basic_maintenance(main)
    _register_pull_logs(main)
    _register_tail_logs(main)
    _register_tks_pull_logs(main)
    _register_tks_tail_logs(main)
    _register_logging_commands(main)
    _register_x1_program_commands(main)


def _register_set_tks(main: click.Group) -> None:
    @main.command("set-tks")
    @common_options
    @click.option("--tks-ip", prompt="TKS-IP gateway IP address", help="TKS-IP gateway IP address")
    @click.option("--tks-user", prompt="TKS-IP gateway username", help="TKS-IP gateway username")
    @click.option(
        "--tks-pass",
        prompt="TKS-IP gateway password",
        hide_input=True,
        help="TKS-IP gateway password",
    )
    def set_tks(**kwargs: object) -> None:
        """Configure TKS-IP door communication gateway."""
        tks_ip = str(kwargs["tks_ip"])
        tks_user = str(kwargs["tks_user"])
        tks_pass = str(kwargs["tks_pass"])

        async def _do(client: GdsClient) -> None:
            await client.configure_tks(tks_ip, tks_user, tks_pass)

        profile, ip, username, password = resolve_login(
            cast("str | None", kwargs.get("ip")),
            cast("str | None", kwargs.get("username")),
            cast("str | None", kwargs.get("password")),
        )
        require_capability(profile, tks=True)
        run_gds(ip, username, password, _do, timeout=cast("float", kwargs["timeout"]))
        console.print("[green]TKS-IP gateway configured.[/green]")


def _register_tks_web(main: click.Group) -> None:
    @main.command("activate-tks-web")
    @selection_options
    @_tks_ip_option
    @click.option(
        "--timeout",
        default=60.0,
        show_default=True,
        type=float,
        help="Maximum seconds to wait for port 8080",
    )
    @click.option(
        "--poll-interval",
        default=1.0,
        show_default=True,
        type=float,
        help="Seconds between /state polls",
    )
    def activate_tks_web(tks_ip: str | None, timeout: float, poll_interval: float) -> None:
        """Start the TKS-IP web interface on port 8080."""
        host = resolve_tks_ip(tks_ip)
        result = cs.activate_tks_webinterface(
            host,
            timeout=timeout,
            poll_interval=poll_interval,
        )
        console.print(
            f"[green]TKS-IP web interface active:[/green] {result.url} "
            f"(state={result.state}, {result.elapsed_seconds:.1f}s)",
        )

    @main.command("tks-status")
    @selection_options
    @_tks_ip_option
    @click.option(
        "--aes-key",
        default=None,
        metavar="KEY",
        help="AES-192 log key for runtime, SIP, and TKS bus checks",
    )
    @click.option(
        "--timeout",
        default=30.0,
        show_default=True,
        type=float,
        help="Maximum seconds for the diagnostic log request",
    )
    def tks_status(tks_ip: str | None, aes_key: str | None, timeout: float) -> None:
        """Inspect TKS-IP health without contacting the port-8080 web app."""
        host = resolve_tks_ip(tks_ip)
        with console.status("[bold]Inspecting TKS-IP services…[/bold]"):
            status = cs.get_tks_device_status(
                host,
                timeout=timeout,
                aes_key=find_tks_aes_key(aes_key, host=host),
            )
        _print_tks_device_status(host, status)


def _register_tks_backup_save(main: click.Group) -> None:
    @main.command("tks-backup-save")
    @_tks_login_options
    @click.option(
        "--output",
        default=None,
        help="Output file path (default: tks-backup-<ip>-<timestamp>.img)",
    )
    def tks_backup_save(
        tks_ip: str | None,
        tks_user: str,
        tks_pass: str,
        output: str | None,
    ) -> None:
        """Download a configuration backup from the TKS-IP gateway."""
        host, user, pw = resolve_tks_login(tks_ip, tks_user, tks_pass)
        client = _login_tks_web(host, user, pw)
        data = client.backup_save()
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = output or f"tks-backup-{host.replace('.', '-')}-{ts}.img"
        Path(out).write_bytes(data)
        console.print(f"[green]Backup saved to {out!r} ({len(data):,} bytes)[/green]")


def _register_tks_backup_restore(main: click.Group) -> None:
    @main.command("tks-backup-restore")
    @click.argument("backup_file", type=click.Path(exists=True))
    @_tks_login_options
    @click.option("--confirm", is_flag=True, help="Skip confirmation prompt (for scripting)")
    def tks_backup_restore(
        backup_file: str,
        tks_ip: str | None,
        tks_user: str,
        tks_pass: str,
        confirm: bool,
    ) -> None:
        """Restore TKS-IP gateway configuration from a backup file."""
        if not confirm:
            click.confirm(
                "This will overwrite the TKS-IP gateway's current configuration. Continue?",
                abort=True,
            )
        host, user, pw = resolve_tks_login(tks_ip, tks_user, tks_pass)
        client = _login_tks_web(host, user, pw)
        client.backup_restore(Path(backup_file).read_bytes(), Path(backup_file).name)
        console.print("[green]Restore triggered.[/green]")


def _register_tks_firmware_update(main: click.Group) -> None:
    @main.command("tks-firmware-update")
    @click.argument("firmware_file", type=click.Path(exists=True))
    @_tks_login_options
    @click.option("--confirm", is_flag=True, help="Skip confirmation prompt (for scripting)")
    def tks_firmware_update(
        firmware_file: str,
        tks_ip: str | None,
        tks_user: str,
        tks_pass: str,
        confirm: bool,
    ) -> None:
        """Upload and apply a firmware update on the TKS-IP gateway."""
        if not confirm:
            click.confirm(
                "This will update the TKS-IP gateway's firmware. Continue?",
                abort=True,
            )
        host, user, pw = resolve_tks_login(tks_ip, tks_user, tks_pass)
        client = _login_tks_web(host, user, pw)
        client.firmware_update(Path(firmware_file).read_bytes(), Path(firmware_file).name)
        console.print("[green]Firmware update triggered.[/green]")


def _register_tks_device_info(main: click.Group) -> None:
    @main.command("tks-info")
    @_tks_login_options
    def tks_info(tks_ip: str | None, tks_user: str, tks_pass: str) -> None:
        """Show read-only device info from the TKS-IP gateway's Administration page."""
        host, user, pw = resolve_tks_login(tks_ip, tks_user, tks_pass)
        client = _login_tks_web(host, user, pw)
        for name, value in client.device_info().items():
            console.print(f"[bold]{name}:[/bold] {value}")


def _register_tks_sip_info(main: click.Group) -> None:
    @main.command("tks-sip-info")
    @_tks_login_options
    def tks_sip_info(tks_ip: str | None, tks_user: str, tks_pass: str) -> None:
        """Show configured SIP clients and incoming-call assignments."""
        host, user, pw = resolve_tks_login(tks_ip, tks_user, tks_pass)
        client = _login_tks_web(host, user, pw)
        info = client.sip_clients()

        clients = cast("list[dict[str, object]]", info["clients"])
        client_table = Table(title="SIP clients")
        client_table.add_column("Name")
        client_table.add_column("Selected")
        client_table.add_column("Username")
        client_table.add_column("Password configured")
        for item in clients:
            password_configured = item["password_configured"]
            client_table.add_row(
                str(item["name"]),
                "yes" if item["selected"] else "",
                str(item["username"] or ""),
                ("" if password_configured is None else ("yes" if password_configured else "no")),
            )
        console.print(client_table)

        groups = cast("list[dict[str, object]]", info["incoming_calls"])
        call_table = Table(title="Selected client's incoming-call assignments")
        call_table.add_column("Group")
        call_table.add_column("Call")
        call_table.add_column("Assigned")
        for group in groups:
            calls = cast("list[dict[str, object]]", group["calls"])
            for call in calls:
                call_table.add_row(
                    str(group["name"]),
                    str(call["name"]),
                    "yes" if call["assigned"] else "no",
                )
        console.print(call_table)
        acknowledged = "yes" if info["security_warning_acknowledged"] else "no"
        console.print(
            "[yellow]Device warning: IP-phone door-opener telegrams are unencrypted "
            f"(warning acknowledged: {acknowledged}).[/yellow]",
        )


def _register_weather(main: click.Group) -> None:
    @main.command("set-weather")
    @common_options
    @click.option(
        "--zip",
        "zip_code",
        prompt="Postal code",
        help="Postal code for weather station lookup",
    )
    @click.option(
        "--country",
        default="DE",
        show_default=True,
        help="ISO 3166-1 alpha-2 country code",
    )
    def set_weather(**kwargs: object) -> None:
        """Configure weather display for a postal code."""
        zip_code = str(kwargs["zip_code"])
        country = str(kwargs["country"])
        timeout = cast("float", kwargs["timeout"])
        profile, ip, username, password = resolve_login(
            cast("str | None", kwargs.get("ip")),
            cast("str | None", kwargs.get("username")),
            cast("str | None", kwargs.get("password")),
        )
        require_capability(profile, weather=True)
        station = weather_mod.find_station(zip_code, country)
        if not station:
            msg = f"No weather station found for zip {zip_code!r} in {country!r}"
            raise click.ClickException(msg)
        console.print(f"Found station: [bold]{station.label}[/bold] ({station.station_id})")

        if not station.guid:
            station.guid = str(uuid.uuid4())

        settings_json = json.dumps(
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

        async def _do(client: GdsClient) -> None:
            await client.set_app_value("Gira.G1", "weather.settings", settings_json)

        run_gds(ip, username, password, _do, timeout=timeout)
        console.print("[green]Weather station configured.[/green]")


def _api_client(
    profile: DeviceProfile,
    ip: str,
    username: str,
    password: str,
    timeout: float,
) -> api_mod.ApiClient:
    return api_mod.ApiClient(
        ip,
        username,
        password,
        api_prefix=profile.api_prefix,
        timeout=timeout,
    )


def _register_basic_maintenance(main: click.Group) -> None:
    @main.command()
    @common_options
    def restart(
        ip: str | None,
        password: str | None,
        username: str | None,
        timeout: float,
    ) -> None:
        """Restart the device."""
        profile, ip, username, password = resolve_login(ip, username, password)
        _api_client(profile, ip, username, password, timeout).reboot()
        console.print("[green]Restart command sent.[/green]")

    @main.command("factory-reset")
    @common_options
    @click.option("--confirm", is_flag=True, help="Skip confirmation prompt (for scripting)")
    def factory_reset(
        ip: str | None,
        password: str | None,
        username: str | None,
        timeout: float,
        confirm: bool,
    ) -> None:
        """Reset the device to factory settings (erases all configuration)."""
        if not confirm:
            click.confirm("This will erase all configuration. Continue?", abort=True)

        profile, ip, username, password = resolve_login(ip, username, password)
        if profile.device_type == DeviceType.X1:
            _api_client(profile, ip, username, password, timeout).factory_reset()
        else:

            async def _do(client: GdsClient) -> None:
                await client.factory_reset()

            run_gds(ip, username, password, _do, timeout=timeout)
        console.print("[green]Factory reset command sent.[/green]")


def _register_pull_logs(main: click.Group) -> None:
    @main.command("pull-logs")
    @common_options
    @click.option(
        "--aes-key",
        default=None,
        metavar="KEY",
        help="TKS-IP AES-192 log key",
    )
    @click.option("--output", default=None, help="Output file path")
    def pull_logs(**kwargs: object) -> None:
        """Download diagnostic log bundle from the device."""
        ip = cast("str | None", kwargs.get("ip"))
        password = cast("str | None", kwargs.get("password"))
        username = cast("str | None", kwargs.get("username"))
        timeout = cast("float", kwargs["timeout"])
        aes_key = cast("str | None", kwargs.get("aes_key"))
        output = cast("str | None", kwargs.get("output"))
        target = _log_target(ip, username, password)
        if target.device_type == DeviceType.TKS_IP:
            host = resolve_tks_ip(target.host or ip)
            data = cs.download_tks_logfile(
                host,
                aes_key=resolve_tks_aes_key(aes_key, host=host),
            )
            output = output or "tks-logs.dat"
        else:
            data = _device_client(target.host or ip, password, username, timeout).logfile()
            output = output or "pygira-logs.zip"
        Path(output).write_bytes(data)
        console.print(f"[green]Logs saved to {output!r} ({len(data):,} bytes)[/green]")


def _log_target(
    ip: str | None,
    username: str | None,
    password: str | None,
) -> _LogTarget:
    """Resolve the log-source type before asking for device-specific credentials."""
    selected = _selected_device()
    if selected is None and not ip:
        prompted = _prompt_configured_device()
        if isinstance(prompted, TypedAddress):
            ip = prompted.value
        else:
            selected = prompted
    if selected is not None:
        device = selected[1]
        return _LogTarget(_device_type(device.type), ip or device.address)
    ctx = click.get_current_context()
    requested = (ctx.find_root().obj or {}).get("requested_device")
    if requested is not None:
        return _LogTarget(cast("DeviceType", requested), ip)

    host = ip or click.prompt("Device IP address")
    detected = detect_device_type(host, username or "", password or "")
    if detected.device_type != DeviceType.UNKNOWN:
        return _LogTarget(detected.device_type, host)

    selected_type = click.prompt(
        "Log source device type",
        type=click.Choice(["g1", "x1", "tks-ip"], case_sensitive=False),
    )
    return _LogTarget(DeviceType(selected_type), host)


def _fetch_tail_logs(
    profile: DeviceProfile,
    ip: str,
    username: str,
    password: str,
    timeout: float,
) -> bytes:
    return api_mod.ApiClient(
        ip,
        username,
        password,
        api_prefix=profile.api_prefix,
        timeout=timeout,
    ).get_logfile()


def _text_files(data: bytes, filters: tuple[str, ...]) -> dict[str, list[str]]:
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if filters and not any(pattern in name for pattern in filters):
                continue
            with suppress(Exception):
                result[name] = zf.read(name).decode("utf-8", errors="replace").splitlines()
    return result


def _tks_text_files(data: bytes, filters: tuple[str, ...]) -> dict[str, list[str]]:
    """Split a TKS-IP `/getlogfile` download into named text files.

    Unlike G1/X1 (a confirmed ZIP), the TKS-IP bootstrap daemon serves a
    single gzip-encoded stream with no confirmed archive format inside
    (confirmed via firmware string analysis, not a live capture — see
    research/tks-ip-v1/api-surface.md). Try ZIP first in case a bundle does
    turn out to hold multiple named files; otherwise treat the whole blob as
    one file so `--file`/tail selection still works either way.
    """
    with suppress(zipfile.BadZipFile):
        return _text_files(data, filters)
    name = "logfile"
    if filters and not any(pattern in name for pattern in filters):
        return {}
    return {name: data.decode("utf-8", errors="replace").splitlines()}


def _print_new_lines(files: dict[str, list[str]], seen: dict[str, int], lines: int) -> None:
    for name, file_lines in files.items():
        start = seen.get(name, max(0, len(file_lines) - lines))
        for line in file_lines[start:]:
            console.print(f"[dim]{name}:[/dim] {line}")
        seen[name] = len(file_lines)


def _register_tail_logs(main: click.Group) -> None:
    @main.command("tail-logs")
    @common_options
    @click.option(
        "--aes-key",
        default=None,
        metavar="KEY",
        help="TKS-IP AES-192 log key",
    )
    @click.option(
        "--interval",
        default=5.0,
        show_default=True,
        type=float,
        help="Poll interval in seconds",
    )
    @click.option(
        "--file",
        "files",
        multiple=True,
        metavar="PATTERN",
        help="Show only files whose name contains PATTERN (repeatable)",
    )
    @click.option(
        "-n",
        "--lines",
        default=0,
        show_default=True,
        type=int,
        help="Lines to show from each file on first fetch (0 = skip history)",
    )
    def tail_logs(**kwargs: object) -> None:
        """Poll device log ZIP and print only new lines (like tail -f)."""
        timeout = cast("float", kwargs["timeout"])
        interval = cast("float", kwargs["interval"])
        files = cast("tuple[str, ...]", kwargs["files"])
        lines = cast("int", kwargs["lines"])
        aes_key = cast("str | None", kwargs["aes_key"])
        ip = cast("str | None", kwargs.get("ip"))
        username = cast("str | None", kwargs.get("username"))
        password = cast("str | None", kwargs.get("password"))

        with suppress(KeyboardInterrupt, click.exceptions.Abort):
            target = _log_target(ip, username, password)
            if target.device_type == DeviceType.TKS_IP:
                host = resolve_tks_ip(target.host or ip)
                resolved_key = resolve_tks_aes_key(aes_key, host=host)
                data = cs.download_tks_logfile(host, aes_key=resolved_key)
                tks_seen: dict[str, int] = {}
                _print_new_lines(_tks_text_files(data, files), tks_seen, lines)

                while True:
                    time.sleep(interval)
                    data = cs.download_tks_logfile(host, aes_key=resolved_key)
                    _print_new_lines(_tks_text_files(data, files), tks_seen, 0)

            profile, ip, username, password = resolve_login(
                target.host or ip,
                username,
                password,
            )
            # First fetch — establish baseline (optionally show tail of existing content).
            data = _fetch_tail_logs(profile, ip, username, password, timeout)
            seen: dict[str, int] = {}
            _print_new_lines(_text_files(data, files), seen, lines)

            # Polling loop — only new lines.
            while True:
                time.sleep(interval)
                data = _fetch_tail_logs(profile, ip, username, password, timeout)
                _print_new_lines(_text_files(data, files), seen, 0)


def _register_tks_pull_logs(main: click.Group) -> None:
    @main.command("tks-pull-logs")
    @click.option("--tks-ip", default=None, help="TKS-IP gateway IP address")
    @click.option(
        "--aes-key",
        default=None,
        metavar="KEY",
        help="AES-192 key as 24-byte text or 48 hexadecimal characters",
    )
    @click.option("--output", default="tks-logs.dat", show_default=True, help="Output file path")
    def tks_pull_logs(tks_ip: str | None, aes_key: str | None, output: str) -> None:
        """Download and decrypt the diagnostic log file from the TKS-IP gateway."""
        host = resolve_tks_ip(tks_ip)
        resolved_key = resolve_tks_aes_key(aes_key)
        data = cs.download_tks_logfile(host, aes_key=resolved_key)
        Path(output).write_bytes(data)
        console.print(f"[green]Logs saved to {output!r} ({len(data):,} bytes)[/green]")


def _register_tks_tail_logs(main: click.Group) -> None:
    @main.command("tks-tail-logs")
    @click.option("--tks-ip", default=None, help="TKS-IP gateway IP address")
    @click.option(
        "--aes-key",
        default=None,
        metavar="KEY",
        help="AES-192 key as 24-byte text or 48 hexadecimal characters",
    )
    @click.option(
        "--interval",
        default=5.0,
        show_default=True,
        type=float,
        help="Poll interval in seconds",
    )
    @click.option(
        "--file",
        "files",
        multiple=True,
        metavar="PATTERN",
        help="Show only files whose name contains PATTERN (repeatable)",
    )
    @click.option(
        "-n",
        "--lines",
        default=0,
        show_default=True,
        type=int,
        help="Lines to show from each file on first fetch (0 = skip history)",
    )
    def tks_tail_logs(
        tks_ip: str | None,
        aes_key: str | None,
        interval: float,
        files: tuple[str, ...],
        lines: int,
    ) -> None:
        """Poll the TKS-IP gateway's log file and print only new lines (like tail -f)."""
        with suppress(KeyboardInterrupt, click.exceptions.Abort):
            host = resolve_tks_ip(tks_ip)
            resolved_key = resolve_tks_aes_key(aes_key)
            data = cs.download_tks_logfile(host, aes_key=resolved_key)
            seen: dict[str, int] = {}
            _print_new_lines(_tks_text_files(data, files), seen, lines)

            while True:
                time.sleep(interval)
                data = cs.download_tks_logfile(host, aes_key=resolved_key)
                _print_new_lines(_tks_text_files(data, files), seen, 0)


def _register_logging_commands(main: click.Group) -> None:
    @main.command("set-logging")
    @common_options
    @click.option(
        "--mode",
        type=click.Choice(["extended", "normal"], case_sensitive=False),
        default="extended",
        show_default=True,
        help="Select normal or extended device logging",
    )
    def set_logging(
        ip: str | None,
        password: str | None,
        username: str | None,
        timeout: float,
        mode: str,
    ) -> None:
        """Set device logging verbosity."""
        severity = 0 if mode.lower() == "extended" else NORMAL_SYSLOG_SEVERITY
        _device_client(ip, password, username, timeout).set_logging_severity(severity)
        console.print(f"[green]Logging mode set to {mode.lower()}.[/green]")

    @main.command("get-logging")
    @common_options
    def get_logging(
        ip: str | None,
        password: str | None,
        username: str | None,
        timeout: float,
    ) -> None:
        """Show device logging verbosity."""
        severity = _device_client(ip, password, username, timeout).get_logging_severity()
        mode = "extended" if severity < NORMAL_SYSLOG_SEVERITY else "normal"
        console.print(mode)


def _register_x1_program_commands(main: click.Group) -> None:
    @main.command("x1-export-program")
    @common_options
    @click.option(
        "--output",
        default=None,
        help="Output file path (default: x1-program-<ip>-<timestamp>.json)",
    )
    def x1_export_program(
        ip: str | None,
        password: str | None,
        username: str | None,
        timeout: float,
        output: str | None,
    ) -> None:
        """Export the GPA program from an X1 via GetUIConfiguration."""

        async def _do(client: GdsClient) -> list[object]:
            return await client.get_ui_configuration()

        profile, ip, username, password = resolve_login(ip, username, password)
        _require_x1(profile, "x1-export-program")
        config = run_gds(ip, username, password, _do, timeout=timeout)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = output or f"x1-program-{ip.replace('.', '-')}-{ts}.json"
        Path(out).write_text(json.dumps(config, indent=2))
        console.print(f"[green]Program saved to {out!r} ({len(config)} entries)[/green]")

    @main.command("x1-import-program")
    @common_options
    @click.argument("program_file", type=click.Path(exists=True))
    @click.option("--confirm", is_flag=True, help="Skip confirmation prompt (for scripting)")
    def x1_import_program(**kwargs: object) -> None:
        """Write a GPA program to an X1 via SetUIConfiguration (experimental).

        Datapoint IDs in the file must match the target device's KNX assignment.
        Use a program exported from an identically-programmed device.
        """
        program_file = str(kwargs["program_file"])
        timeout = cast("float", kwargs["timeout"])
        if not bool(kwargs["confirm"]):
            click.confirm(
                "This will overwrite the X1 GPA program. Continue?",
                abort=True,
            )

        try:
            config = json.loads(Path(program_file).read_text())
        except json.JSONDecodeError as exc:
            msg = f"invalid program JSON: {exc.msg}"
            raise click.BadParameter(msg, param_hint="program_file") from exc
        profile, ip, username, password = resolve_login(
            cast("str | None", kwargs.get("ip")),
            cast("str | None", kwargs.get("username")),
            cast("str | None", kwargs.get("password")),
        )
        _require_x1(profile, "x1-import-program")

        async def _do(client: GdsClient) -> None:
            await client.set_ui_configuration(config)

        run_gds(ip, username, password, _do, timeout=timeout)
        console.print("[green]Program applied.[/green]")
