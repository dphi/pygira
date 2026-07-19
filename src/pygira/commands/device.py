"""Device and network commands."""

import json
import re
from typing import cast

import click
from rich.table import Table
from rich.text import Text

from pygira import api as api_mod
from pygira.context import console, die, resolve_login
from pygira.core.detect import detect_device_type
from pygira.models import NetworkConfig
from pygira.options import common_options, network_options

FILESYSTEM_COLUMN_COUNT = 6
PROCESS_COLUMN_COUNT = 8
MAX_COMMAND_LENGTH = 120
TRUNCATED_COMMAND_LENGTH = 117
KIB_PER_MIB = 1024
KIB_PER_GIB = 1024 * KIB_PER_MIB


def _print_diagnostics(data: dict) -> None:
    """Pretty-print diagnostic page data with parsed tables."""
    items = data.get("diagnosticpage", [])
    if not items:
        console.print("[dim]No diagnostic data returned.[/dim]")
        return

    for item in items:
        title_key = item.get("title", "")
        blob = item.get("blob", "")
        if not blob:
            continue

        title = _resolve_title(title_key)
        if title_key.endswith(".memory"):
            _print_memory(title, blob)
        elif title_key.endswith(".filesystem"):
            _print_filesystem(title, blob)
        elif title_key.endswith(".system"):
            _print_system(title, blob)
        elif title_key.endswith(".device"):
            _print_device(title, blob)
        else:
            console.print(Text(blob.strip()))


def _resolve_title(key: str) -> str:
    return {
        "diagnostic.titles.memory": "Memory",
        "diagnostic.titles.filesystem": "Filesystem",
        "diagnostic.titles.system": "System",
        "diagnostic.titles.device": "Device",
    }.get(key, key)


def _print_memory(title: str, blob: str) -> None:
    t = Table(title=title, show_header=False, box=None, padding=(0, 2))
    t.add_column(style="bold")
    t.add_column()
    for line in blob.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            t.add_row(k.strip(), v.strip())
    console.print(t)
    console.print()


def _print_filesystem(title: str, blob: str) -> None:
    t = Table(title=title, box=None)
    t.add_column("Filesystem")
    t.add_column("Size", justify="right")
    t.add_column("Used", justify="right")
    t.add_column("Avail", justify="right")
    t.add_column("Use%", justify="right")
    t.add_column("Mounted on")
    for line in blob.strip().splitlines()[1:]:  # skip header
        cols = line.split()
        if len(cols) < FILESYSTEM_COLUMN_COUNT:
            continue
        size = int(cols[1])
        if size == 0:  # skip pseudo-fs
            continue
        t.add_row(
            cols[0],
            _human_size(size),
            _human_size(int(cols[2])),
            _human_size(int(cols[3])),
            cols[4],
            cols[5],
        )
    console.print(t)
    console.print()


def _print_system(title: str, blob: str) -> None:
    lines = blob.strip().splitlines()
    summary: list[str] = []
    procs: list[str] = []
    in_procs = False
    for line in lines:
        if in_procs:
            procs.append(line)
        elif line.startswith("  PID"):
            in_procs = True
            procs.append(line)
        else:
            summary.append(line)

    console.print(f"[bold]{title}[/bold]")
    for s in summary:
        console.print(f"  {s}")

    if procs:
        t = Table(title="Top Processes", box=None)
        t.add_column("PID", style="dim")
        t.add_column("Command")
        t.add_column("%MEM", justify="right")
        t.add_column("%CPU", justify="right")
        for line in procs[1:]:  # skip header
            cols = line.split()
            if len(cols) < PROCESS_COLUMN_COUNT:
                continue
            pid, ppid, user, stat, vsz, pct_vsz, pct_cpu = cols[0:7]
            cmd = " ".join(cols[7:])
            # Truncate long commands
            if len(cmd) > MAX_COMMAND_LENGTH:
                cmd = cmd[:TRUNCATED_COMMAND_LENGTH] + "..."
            # Skip kernel threads (PPID 2) and tiny processes
            if ppid == "2":
                continue
            t.add_row(pid, cmd, pct_vsz, pct_cpu)
        console.print(t)
    console.print()


def _dv(val: object) -> str:
    """Extract display value from section value (string or {_val: ...} dict)."""
    if isinstance(val, dict):
        raw = val.get("_val", "")
        return str(raw)
    return str(val) if val is not None else ""


def _value_table(title: str) -> Table:
    table = Table(title=title, show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    return table


def _add_existing_rows(
    table: Table,
    section: dict,
    keys: tuple[str, ...],
    *,
    prefix: str = "",
) -> None:
    for key in keys:
        if key in section:
            table.add_row(f"{prefix}{key}", _dv(section[key]))


def _print_hardware(sections: dict[str, dict]) -> None:
    hw = sections.get("Hardware", {})
    if not hw:
        return
    table = _value_table("Hardware")
    _add_existing_rows(
        table,
        hw,
        (
            "Device",
            "Manufacturer",
            "Version",
            "Serial Number",
            "MAC Address(es)",
            "KNX Serial Number(s)",
        ),
    )
    console.print(table)


def _print_firmware(sections: dict[str, dict]) -> None:
    sw = sections.get("Software", {})
    current = sw.get("Current System", {})
    fallback = sw.get("Fallback System", {})
    if not (current or fallback):
        return
    table = _value_table("Firmware")
    if "Firmware Version" in current:
        table.add_row("Current", _dv(current["Firmware Version"]))
    if "Firmware Version" in fallback:
        table.add_row("Fallback", _dv(fallback["Firmware Version"]))
    if "Current System" in sw:
        table.add_row("Active slot", _dv(sw["Current System"]))
    console.print(table)


def _print_network(sections: dict[str, dict]) -> None:
    ip = sections.get("IP Configuration", {})
    svc = sections.get("Service", {})
    if not (ip or svc):
        return
    table = _value_table("Network")
    _add_existing_rows(table, ip, ("DHCP", "IP Address", "Subnet Mask", "Default Gateway"))
    dns = _dv(ip.get("DNS Server", ""))
    if dns:
        table.add_row("DNS", dns)
    _add_link_rows(table, sections, svc)
    console.print(table)


def _add_link_rows(table: Table, sections: dict[str, dict], svc: dict) -> None:
    wired = sections.get("Technology: Wired", {})
    wifi = sections.get("Technology: WiFi", {})
    if wired:
        conn = "yes" if _dv(wired.get("Connected", "")).lower() == "yes" else "no"
        iface = _dv(svc.get("Interface", ""))
        table.add_row("Ethernet", f"{conn}" + (f" ({iface})" if iface else ""))
    if wifi:
        conn = "yes" if _dv(wifi.get("Connected", "")).lower() == "yes" else "no"
        table.add_row("WiFi", conn)


def _print_ntp_location(sections: dict[str, dict]) -> None:
    ntp = sections.get("NTP", {})
    loc = sections.get("Location", {})
    if not (ntp or loc):
        return
    table = _value_table("NTP & Location")
    _add_existing_rows(table, ntp, ("Enabled", "Server", "Interval"), prefix="NTP ")
    _add_existing_rows(table, loc, ("Time Zone", "Latitude", "Longitude"))
    console.print(table)


def _print_services(blob: str) -> None:
    procs = re.findall(
        r"Command Line: (\S+)\s*\n\s*Status:\s+(\S+).*?"
        r"(?:\n\s*State:\s+(\S+))?.*?"
        r"\n\s*Process ID:\s+(\d+)",
        blob,
        re.DOTALL,
    )
    if not procs:
        return
    table = Table(title="Services", box=None)
    table.add_column("Process")
    table.add_column("Status")
    table.add_column("PID", style="dim")
    for binary, status, state, pid in procs:
        name = binary.rsplit("/", 1)[-1]
        display_status = state or status
        table.add_row(name, display_status, pid)
    console.print(table)


def _print_device(title: str, blob: str) -> None:
    sections = _parse_device_sections(blob)
    _print_hardware(sections)
    _print_firmware(sections)
    _print_network(sections)
    _print_ntp_location(sections)
    _print_services(blob)
    console.print()


# ── device blob parser ────────────────────────────────────────────────────────


def _ensure_section(parent: dict, key: str) -> dict:
    section = parent.get(key)
    if not isinstance(section, dict):
        section = {}
        parent[key] = section
    return section


def _set_section_value(parent: dict, key: str, value: str) -> dict:
    section = _ensure_section(parent, key)
    section["_val"] = value
    return section


def _parse_device_line(sections: dict, stack: list[tuple[int, dict]], line: str) -> None:
    stripped = line.lstrip("\t")
    if not stripped:
        return
    depth = len(line) - len(stripped)
    while stack and stack[-1][0] >= depth:
        stack.pop()
    parent = stack[-1][1] if stack else sections
    if ":" not in stripped:
        stack.append((depth, _ensure_section(parent, stripped)))
        return
    key, _, val = stripped.partition(":")
    key, val = key.strip(), val.strip()
    section = _set_section_value(parent, key, val) if val else _ensure_section(parent, key)
    stack.append((depth, section))


def _parse_device_sections(blob: str) -> dict[str, dict]:
    """Parse tab-indented hierarchy into nested dict keyed by section name."""
    sections: dict[str, dict] = {}
    stack: list[tuple[int, dict]] = []
    for line in blob.replace("\r\n", "\n").splitlines():
        if line.strip():
            _parse_device_line(sections, stack, line)
    return sections


def _human_size(kb: int) -> str:
    if kb >= KIB_PER_GIB:
        return f"{kb / KIB_PER_GIB:.1f}G"
    if kb >= KIB_PER_MIB:
        return f"{kb / KIB_PER_MIB:.1f}M"
    return str(kb)


def _device_client(
    ip: str | None,
    password: str | None,
    username: str | None,
    timeout: float,
) -> api_mod.ApiClient:
    profile, ip, username, password = resolve_login(ip, username, password)
    return api_mod.ApiClient(
        ip,
        username,
        password,
        api_prefix=profile.api_prefix,
        timeout=timeout,
    )


def _register_detect(main: click.Group) -> None:
    @main.command("detect")
    @click.option("--ip", prompt="Device IP address", help="Device IP address")
    @click.option("--username", default="admin", show_default=True, help="Device username")
    @click.option("--password", default="", help="Device password (optional)")
    def detect(ip: str, username: str, password: str) -> None:
        """Detect device model and firmware (tries unauthenticated probe first)."""
        try:
            result = detect_device_type(ip, username or "", password)
        except Exception as e:
            die(e)

        if result.device_type.value == "unknown":
            die(f"Could not detect device type. Evidence: {result.evidence}")

        table = Table(show_header=False, box=None)
        table.add_column(style="bold")
        table.add_column()
        table.add_row("Device type", result.device_type.value)
        if result.app_name:
            table.add_row("App name", result.app_name)
        if result.firmware_version:
            table.add_row("Firmware", result.firmware_version)
        table.add_row("Evidence", result.evidence)
        console.print(table)


def _register_info(main: click.Group) -> None:
    @main.command()
    @common_options
    @click.option(
        "--long",
        "long_info",
        is_flag=True,
        help="Fetch extended device info from webservice",
    )
    def info(
        ip: str | None,
        password: str | None,
        username: str | None,
        timeout: float,
        long_info: bool,
    ) -> None:
        """Show device info: firmware version, MAC, IP config."""
        try:
            client = _device_client(ip, password, username, timeout)
            if long_info:
                result = client.get_device_info(force_long=True)
                console.print_json(json.dumps(result.get("data", result)))
                return
            _print_info_table(client.get_device_info(force_long=True).get("data", {}))
        except Exception as e:
            die(e)


def _print_info_table(data: dict) -> None:
    table = Table(show_header=False, box=None)
    table.add_column(style="bold")
    table.add_column()
    rows = [
        ("Firmware", data.get("CurrentFirmwareVersion")),
        ("MAC", data.get("MacAddress")),
        ("Device name", data.get("KIM-FriendlyName") or data.get("DeviceName")),
        ("DHCP", str(data["Dhcp"]) if "Dhcp" in data else None),
        ("IP", data.get("IpAddress")),
        ("Subnet", data.get("SubnetMask")),
        ("Gateway", data.get("DefaultGateway")),
        ("DNS", data.get("NameServer")),
    ]
    for key, value in rows:
        if value:
            table.add_row(key, value)
    console.print(table)


def _register_diagnostics(main: click.Group) -> None:
    @main.command("diagnostics")
    @common_options
    @click.option(
        "--full/--no-full",
        default=True,
        show_default=True,
        help="Request complete diagnostic page",
    )
    @click.option("--json", "json_output", is_flag=True, help="Output raw JSON (no formatting)")
    def diagnostics(**kwargs: object) -> None:
        """Fetch diagnostic page data from webservice."""
        try:
            client = _device_client(
                cast("str | None", kwargs.get("ip")),
                cast("str | None", kwargs.get("password")),
                cast("str | None", kwargs.get("username")),
                cast("float", kwargs["timeout"]),
            )
            result = client.get_diagnostic_page(completely=bool(kwargs["full"]))
            data = result.get("data", result)
            if kwargs["json_output"]:
                console.print_json(json.dumps(data))
                return
            _print_diagnostics(data)
        except Exception as e:
            die(e)


def _register_set_ntp(main: click.Group) -> None:
    @main.command("set-ntp")
    @common_options
    @click.option("--server", prompt="NTP server hostname or IP", help="NTP server hostname or IP")
    @click.option(
        "--interval",
        "interval_minutes",
        default=10,
        show_default=True,
        type=int,
        help="NTP sync interval in minutes",
    )
    @click.option(
        "--enabled/--disabled",
        default=True,
        show_default=True,
        help="Enable or disable NTP",
    )
    def set_ntp(**kwargs: object) -> None:
        """Set NTP server configuration."""
        try:
            client = _device_client(
                cast("str | None", kwargs.get("ip")),
                cast("str | None", kwargs.get("password")),
                cast("str | None", kwargs.get("username")),
                cast("float", kwargs["timeout"]),
            )
            server = str(kwargs["server"])
            interval = cast("int", kwargs["interval_minutes"])
            enabled = bool(kwargs["enabled"])
            client.set_ntp_config(enabled=enabled, server=server, interval_minutes=interval)
            state = "enabled" if enabled else "disabled"
            console.print(f"[green]NTP {state}.[/green] server={server!r} interval={interval}m")
        except Exception as e:
            die(e)


def _register_get_ntp(main: click.Group) -> None:
    @main.command("get-ntp")
    @common_options
    def get_ntp(
        ip: str | None,
        password: str | None,
        username: str | None,
        timeout: float,
    ) -> None:
        """Show current NTP configuration."""
        try:
            client = _device_client(ip, password, username, timeout)
            result = client.get_device_info(force_long=True)
            data = result.get("data", result)
            ntp = {
                "Ntp": data.get("Ntp"),
                "NtpServerAddress": data.get("NtpServerAddress"),
                "NtpInterval": data.get("NtpInterval"),
            }
            console.print_json(json.dumps(ntp))
        except Exception as e:
            die(e)


def _network_config_from_kwargs(kwargs: dict[str, object], current: dict) -> NetworkConfig:
    dhcp = kwargs.get("dhcp")
    use_dhcp = dhcp if dhcp is not None else current.get("Dhcp", False)
    return NetworkConfig(
        dhcp=bool(use_dhcp),
        ip_address=cast("str", kwargs.get("static_ip") or current.get("IpAddress", "")),
        subnet_mask=cast("str", kwargs.get("subnet") or current.get("SubnetMask", "")),
        default_gateway=cast("str", kwargs.get("gateway") or current.get("DefaultGateway", "")),
        primary_dns=cast("str", kwargs.get("dns1") or current.get("NameServer", "")),
        secondary_dns=cast("str", kwargs.get("dns2") or current.get("SecondaryDns", "")),
    )


def _register_set_ip(main: click.Group) -> None:
    @main.command("set-ip")
    @common_options
    @network_options
    def set_ip(**kwargs: object) -> None:
        """Configure network settings (IP, DHCP, DNS...)."""
        network_keys = ["dhcp", "static_ip", "subnet", "gateway", "dns1", "dns2"]
        if not any(kwargs.get(key) is not None for key in network_keys):
            msg = (
                "No network flags given — nothing to change.\n"
                "  Specify at least one of: "
                "--dhcp/--no-dhcp, --static-ip, --subnet, --gateway, --dns1, --dns2"
            )
            raise click.UsageError(msg)
        try:
            client = _device_client(
                cast("str | None", kwargs.get("ip")),
                cast("str | None", kwargs.get("password")),
                cast("str | None", kwargs.get("username")),
                cast("float", kwargs["timeout"]),
            )
            current = client.get_device_info(force_long=True).get("data", {})
            client.set_ip_config(_network_config_from_kwargs(kwargs, current))
            console.print("[green]IP configuration updated.[/green]")
        except Exception as e:
            die(e)


def register(main: click.Group) -> None:
    """Register device-related commands."""
    _register_detect(main)
    _register_info(main)
    _register_diagnostics(main)
    _register_set_ntp(main)
    _register_get_ntp(main)
    _register_set_ip(main)
