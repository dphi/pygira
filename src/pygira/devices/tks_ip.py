"""TKS-IP device profile and unified access class."""

from pathlib import Path
from typing import NoReturn

from pygira import config_service as cs
from pygira.core.types import DeviceCapabilities, DeviceType
from pygira.devices.base import DeviceProfile
from pygira.exceptions import (
    InvalidInputError,
    TransportError,
    UnsupportedCapabilityError,
)
from pygira.models import DeviceInfo, DiagnosticPage, FirmwareStatus, NetworkConfig
from pygira.tks_web import TksWebClient

_WEB_LOGIN_ATTEMPTS = 2

PROFILE = DeviceProfile(
    device_type=DeviceType.TKS_IP,
    display_name="Gira TKS-IP gateway",
    capabilities=DeviceCapabilities(weather=False, tks=False),
    api_prefix="",
    default_username="admin",
)


class TksIp:
    """Unified access to the TKS-IP bootstrap service and web assistant.

    Passive health and encrypted logs use the always-on port-80 service.
    Configuration inspection, backups, and firmware updates use the on-demand
    port-8080 assistant with a persisted authenticated session.
    """

    def __init__(  # noqa: PLR0913 - explicit connection and log-decryption options
        self,
        host: str,
        username: str = "admin",
        password: str = "",
        timeout: float = 30.0,
        *,
        aes_key: str | bytes | None = None,
        persist_session: bool = True,
    ) -> None:
        """Create a TKS-IP facade for a single gateway."""
        self.host = host
        self.username = username
        self.timeout = timeout
        self._password = password
        self._aes_key = aes_key
        self._persist_session = persist_session

    def activate_web(
        self,
        *,
        timeout: float | None = None,
        poll_interval: float = 1.0,
    ) -> cs.TksWebInterfaceActivation:
        """Start the on-demand web assistant and wait until it is ready."""
        return cs.activate_tks_webinterface(
            self.host,
            timeout=timeout or self.timeout,
            poll_interval=poll_interval,
        )

    def _web(self) -> TksWebClient:
        """Return an authenticated web client, recovering one startup race."""
        last_error: TransportError | None = None
        for _ in range(_WEB_LOGIN_ATTEMPTS):
            self.activate_web()
            client = TksWebClient(
                self.host,
                timeout=self.timeout,
                persist_session=self._persist_session,
            )
            try:
                client.login(self.username, self._password)
            except TransportError as exc:
                last_error = exc
            else:
                return client
        assert last_error is not None
        raise last_error

    @staticmethod
    def _device_data(
        raw: dict[str, str],
        network: dict[str, object],
        *,
        include_raw: bool,
    ) -> dict[str, object]:
        data: dict[str, object] = {
            "CurrentFirmwareVersion": raw.get("Software-Version", ""),
            "MacAddress": raw.get("MAC-Adresse", ""),
            "DeviceName": network.get("network_name") or "",
            "Dhcp": network.get("dhcp"),
            "IpAddress": network.get("ip_address") or "",
            "SubnetMask": network.get("subnet_mask") or "",
            "DefaultGateway": network.get("default_gateway") or "",
            "NameServer": network.get("nameserver") or "",
            "AppName": "Gira TKS-IP",
        }
        if include_raw:
            data["TksIp"] = raw
            data["VideoResolution"] = network.get("video_resolution")
            data["GatewayId"] = network.get("gateway_id")
        return data

    def device_info(self, *, long: bool = True) -> dict:
        """Return a G1/X1-compatible device-information envelope."""
        client = self._web()
        raw = client.device_info()
        network = client.network_info()
        return {"data": self._device_data(raw, network, include_raw=long)}

    def device_info_model(self, *, long: bool = True) -> DeviceInfo:
        """Return normalized, typed device information."""
        return DeviceInfo.from_webservice(self.device_info(long=long))

    def raw_device_info(self) -> dict[str, str]:
        """Return the TKS-IP assistant's native labelled information."""
        return self._web().device_info()

    def network_info(self) -> dict[str, object]:
        """Return the read-only network and video configuration."""
        return self._web().network_info()

    def ntp_info(self) -> dict[str, object]:
        """Return normalized read-only clock and NTP configuration."""
        info = self._web().date_time_info()
        return {
            "enabled": info.get("automatic"),
            "server": info.get("ntp_server"),
            "interval_minutes": None,
            "timezone": info.get("timezone"),
            "date": info.get("date"),
            "time": info.get("time"),
        }

    def sip_clients(self) -> dict[str, object]:
        """Return configured SIP clients and incoming-call assignments."""
        return self._web().sip_clients()

    def status(self) -> cs.TksDeviceStatus:
        """Return a passive health snapshot without contacting port 8080."""
        return cs.get_tks_device_status(
            self.host,
            timeout=self.timeout,
            aes_key=self._aes_key,
        )

    @staticmethod
    def _diagnostic_sections(status: cs.TksDeviceStatus) -> list[dict[str, str]]:
        gateway = [
            f"Bootstrap reachable: {status.bootstrap_reachable}",
            f"Identified as TKS-IP: {status.identified_as_tks_ip}",
            f"HTTP status: {status.http_status}",
            f"Device time: {status.device_time.isoformat() if status.device_time else ''}",
            f"Clock skew seconds: {status.clock_skew_seconds}",
            f"SSH listener: {status.ssh_reachable}",
            f"SDA listener: {status.sda_listener_reachable}",
        ]
        sections = [{"title": "TKS-IP gateway", "blob": "\n".join(gateway)}]
        diagnostics = status.diagnostics
        if diagnostics is not None:
            runtime = [
                f"Observed at: {diagnostics.observed_at}",
                f"Free memory KiB: {diagnostics.free_memory_kib}",
                f"Load averages: {diagnostics.load_averages}",
                f"Tasks: {diagnostics.runnable_tasks}/{diagnostics.total_tasks}",
                f"SIP PID: {diagnostics.sip_pid}",
                f"SIP memory KiB: {diagnostics.sip_memory_kib}",
                f"SIP responsive: {diagnostics.sip_responsive}",
                f"TKS bus state: {diagnostics.tks_bus_state}",
                "Recent failures: " + (", ".join(diagnostics.recent_failures) or "none"),
            ]
            sections.append({"title": "TKS-IP runtime", "blob": "\n".join(runtime)})
        elif status.diagnostics_error:
            sections.append(
                {
                    "title": "TKS-IP runtime",
                    "blob": f"Diagnostic log unavailable: {status.diagnostics_error}",
                },
            )
        return sections

    def diagnostic_page(self, *, completely: bool = True) -> dict:
        """Return passive health information in the shared diagnostic envelope."""
        del completely
        return {"data": {"diagnosticpage": self._diagnostic_sections(self.status())}}

    def diagnostic_page_model(self) -> DiagnosticPage:
        """Return normalized passive diagnostic sections."""
        return DiagnosticPage.from_webservice(self.diagnostic_page())

    def logfile(self) -> bytes:
        """Return the decrypted diagnostic log."""
        if self._aes_key is None:
            msg = "TKS-IP logfile access requires an AES key"
            raise InvalidInputError(msg)
        return cs.download_tks_logfile(
            self.host,
            timeout=self.timeout,
            aes_key=self._aes_key,
        )

    def firmware_status(self) -> dict[str, object]:
        """Return the installed firmware version; update availability is not exposed."""
        info = self._web().device_info()
        return {
            "data": {
                "currentVersion": info.get("Software-Version", ""),
                "state": "unknown",
            },
        }

    def firmware_status_model(self) -> FirmwareStatus:
        """Return normalized installed firmware information."""
        return FirmwareStatus.from_webservice(self.firmware_status())

    def backup_save(self) -> bytes:
        """Download a configuration backup."""
        return self._web().backup_save(timeout=self.timeout)

    def backup_restore(self, data: bytes, filename: str = "backup.img") -> None:
        """Upload and restore a configuration backup."""
        self._web().backup_restore(data, filename)

    def firmware_update(self, path: Path) -> None:
        """Upload and apply a local firmware image."""
        self._web().firmware_update(path.read_bytes(), path.name)

    def install_firmware(self, path: Path) -> dict:
        """Upload and apply a local firmware image."""
        self.firmware_update(path)
        return {"started": True}

    @property
    def can_wait_for_upgrade(self) -> bool:
        """Whether firmware completion can be observed."""
        return False

    def _unsupported(self, capability: str) -> NoReturn:
        msg = f"{capability} is not supported by the confirmed TKS-IP API"
        raise UnsupportedCapabilityError(msg)

    def set_ntp(self, *, enabled: bool, server: str, interval_minutes: int = 10) -> dict:
        """Reject unconfirmed TKS-IP clock writes."""
        del enabled, server, interval_minutes
        self._unsupported("NTP configuration")

    def set_ip(self, cfg: NetworkConfig) -> dict:
        """Reject unconfirmed TKS-IP network writes."""
        del cfg
        self._unsupported("Network configuration")

    def get_logging_severity(self) -> int:
        """Reject unsupported TKS-IP logging-level inspection."""
        self._unsupported("Logging-level inspection")

    def set_logging_severity(self, severity: int) -> None:
        """Reject unsupported TKS-IP logging-level changes."""
        del severity
        self._unsupported("Logging-level configuration")

    def check_update(self) -> dict:
        """Reject unavailable online update discovery."""
        self._unsupported("Online firmware checks")

    def trigger_online_update(self) -> dict:
        """Reject unavailable online firmware updates."""
        self._unsupported("Online firmware updates")

    def wait_for_completion(
        self,
        poll_interval: float = 5.0,
        max_wait: float = 300.0,
    ) -> bool:
        """Reject unavailable firmware progress polling."""
        del poll_interval, max_wait
        self._unsupported("Firmware progress polling")

    def commissioning_test(self) -> dict:
        """Reject unavailable commissioning tests."""
        self._unsupported("Commissioning tests")

    def enable_ssh(self, *, persistent: bool = True) -> None:
        """Reject unavailable SSH listener control."""
        del persistent
        self._unsupported("SSH control")

    def disable_ssh(self) -> None:
        """Reject unavailable SSH listener control."""
        self._unsupported("SSH control")

    def reboot(self) -> None:
        """Reject unavailable reboot control."""
        self._unsupported("Restart")

    def factory_reset(self) -> None:
        """Reject unavailable factory-reset control."""
        self._unsupported("Factory reset")
