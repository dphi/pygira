"""Maintenance and integration commands."""

import io
import json
import time
import uuid
import zipfile
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import ParamSpec, TypeVar, cast

import click

from pygira import api as api_mod
from pygira import config_service as cs
from pygira import weather as weather_mod
from pygira.context import (
    console,
    require_capability,
    resolve_login,
    resolve_tks_ip,
    resolve_tks_login,
)
from pygira.core.types import DeviceType
from pygira.devices.base import DeviceProfile
from pygira.exceptions import UnsupportedCapabilityError
from pygira.gds import GdsClient, run_gds
from pygira.options import common_options
from pygira.tks_web import TksWebClient

P = ParamSpec("P")
R = TypeVar("R")
ClickCommand = Callable[P, R]
NORMAL_SYSLOG_SEVERITY = 4
TKS_LOGIN_OPTIONS = [
    click.option("--tks-ip", default=None, help="TKS-IP gateway IP address"),
    click.option("--tks-user", default=None, help="TKS-IP gateway username"),
    click.option("--tks-pass", default=None, help="TKS-IP gateway password"),
]


def _tks_login_options(f: ClickCommand[P, R]) -> ClickCommand[P, R]:
    for opt in reversed(TKS_LOGIN_OPTIONS):
        f = opt(f)
    return f


def _require_x1(profile: DeviceProfile, command_name: str) -> None:
    if profile.device_type != DeviceType.X1:
        msg = f"{command_name} is supported on X1 only"
        raise UnsupportedCapabilityError(msg)


def register(main: click.Group) -> None:
    """Register maintenance and integration commands."""
    _register_set_tks(main)
    _register_tks_web(main)
    _register_tks_backup_save(main)
    _register_tks_backup_restore(main)
    _register_tks_firmware_update(main)
    _register_weather(main)
    _register_basic_maintenance(main)
    _register_pull_logs(main)
    _register_tail_logs(main)
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
    @click.option("--tks-ip", default=None, help="TKS-IP gateway IP address")
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
    @click.option("--tks-ip", default=None, help="TKS-IP gateway IP address")
    def tks_status(tks_ip: str | None) -> None:
        """Check TKS-IP gateway status without starting the web app."""
        host = resolve_tks_ip(tks_ip)
        status = cs.get_tks_status(host)
        if not status.bootstrap_reachable:
            console.print(f"[red]Gateway unreachable[/red] at {host}")
        elif status.app_running:
            console.print(
                f"[green]Web app running[/green] "
                f"(state={status.state_code} — {status.state_description})",
            )
        else:
            console.print(
                "[yellow]Bootstrap daemon reachable, web app not running[/yellow] "
                "— run 'activate-tks-web' to start it",
            )


def _register_tks_backup_save(main: click.Group) -> None:
    tks_login_options = [
        click.option("--tks-ip", default=None, help="TKS-IP gateway IP address"),
        click.option("--tks-user", default=None, help="TKS-IP gateway username"),
        click.option("--tks-pass", default=None, help="TKS-IP gateway password"),
    ]

    def _tks_login_options(f: ClickCommand[P, R]) -> ClickCommand[P, R]:
        for opt in reversed(tks_login_options):
            f = opt(f)
        return f

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
        client = TksWebClient(host)
        client.login(user, pw)
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
        client = TksWebClient(host)
        client.login(user, pw)
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
        client = TksWebClient(host)
        client.login(user, pw)
        client.firmware_update(Path(firmware_file).read_bytes(), Path(firmware_file).name)
        console.print("[green]Firmware update triggered.[/green]")


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
    @click.option("--output", default="pygira-logs.zip", show_default=True, help="Output file path")
    def pull_logs(
        ip: str | None,
        password: str | None,
        username: str | None,
        timeout: float,
        output: str,
    ) -> None:
        """Download diagnostic log bundle from the device."""
        profile, ip, username, password = resolve_login(ip, username, password)
        if profile.device_type == DeviceType.X1:
            data = cs.download_logs_x1(ip, username, password, timeout=timeout)
        else:
            client = api_mod.ApiClient(
                ip,
                username,
                password,
                api_prefix=profile.api_prefix,
                timeout=timeout,
            )
            data = client.get_logfile()
        Path(output).write_bytes(data)
        console.print(f"[green]Logs saved to {output!r} ({len(data):,} bytes)[/green]")


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

        with suppress(KeyboardInterrupt, click.exceptions.Abort):
            profile, ip, username, password = resolve_login(
                cast("str | None", kwargs.get("ip")),
                cast("str | None", kwargs.get("username")),
                cast("str | None", kwargs.get("password")),
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


def _register_logging_commands(main: click.Group) -> None:
    @main.command("set-logging")
    @common_options
    @click.option(
        "--mode",
        type=click.Choice(["extended", "normal"], case_sensitive=False),
        default="extended",
        show_default=True,
        help="Enable or disable erweiterte Protokollierung on X1",
    )
    def set_logging(
        ip: str | None,
        password: str | None,
        username: str | None,
        timeout: float,
        mode: str,
    ) -> None:
        """Set X1 logging verbosity."""
        profile, ip, username, password = resolve_login(ip, username, password)
        _require_x1(profile, "set-logging")

        severity = 0 if mode.lower() == "extended" else NORMAL_SYSLOG_SEVERITY
        cs.set_syslog_severity_x1(ip, username, password, severity, timeout=timeout)
        if severity == 0:
            console.print("[green]Extended logging enabled.[/green]")
        else:
            console.print("[green]Extended logging disabled (normal mode).[/green]")

    @main.command("get-logging")
    @common_options
    def get_logging(
        ip: str | None,
        password: str | None,
        username: str | None,
        timeout: float,
    ) -> None:
        """Show X1 logging verbosity mode."""
        profile, ip, username, password = resolve_login(ip, username, password)
        _require_x1(profile, "get-logging")

        severity = cs.get_syslog_severity_x1(ip, username, password, timeout=timeout)
        mode = "extended" if severity < NORMAL_SYSLOG_SEVERITY else "normal"
        console.print(f"Logging mode: [bold]{mode}[/bold] (SyslogSeverity={severity})")


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
