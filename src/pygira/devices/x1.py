"""X1 device profile and unified access class."""

from pathlib import Path

from pygira.api import ApiClient
from pygira.core.types import DeviceCapabilities, DeviceType
from pygira.devices.base import DeviceProfile
from pygira.models import DeviceInfo, DiagnosticPage, FirmwareStatus, NetworkConfig

PROFILE = DeviceProfile(
    device_type=DeviceType.X1,
    display_name="Gira X1",
    capabilities=DeviceCapabilities(weather=False, tks=False),
    api_prefix="/webservice",
    default_username="device",
)


class X1:
    """Unified access to the Gira X1 — iscwebservice only (port 80, path /webservice).

    The X1 GDS WebSocket (port 4432) is event-push only and not used here.
    Port 443 /api hosts the GDS-REST-API for third-party KNX apps — also not used.

    Session auth (getPasswordSalt → doAuthenticateSession) is handled automatically
    by ApiClient for commands that return error 220/235.
    """

    def __init__(
        self,
        host: str,
        username: str = "device",
        password: str = "",
        timeout: float = 15.0,
    ) -> None:
        """Connect to an X1 at host with given credentials."""
        self.api = ApiClient(host, username, password, api_prefix="/webservice", timeout=timeout)

    def _post(self, payload: dict) -> dict:
        return self.api._post(payload)

    # ------------------------------------------------------------------ #
    # Inspection — shared iscwebservice commands                          #
    # ------------------------------------------------------------------ #

    def device_info(self, *, long: bool = False) -> dict:
        """Full device info (39 fields) including network, NTP, syslog, serial, firmware.

        Requires session auth (handled automatically). Returns short 3-field response
        without auth, full response with session auth.
        """
        return self.api.get_device_info(force_long=long)

    def device_info_model(self) -> DeviceInfo:
        """Return normalized, typed device information."""
        return self.api.get_device_info_model()

    def diagnostic_page(self, *, completely: bool = True) -> dict:
        """Free-text diagnostic blob: running processes, system/NTP/IP info."""
        return self.api.get_diagnostic_page(completely=completely)

    def diagnostic_page_model(self) -> DiagnosticPage:
        """Return normalized diagnostic sections."""
        return self.api.get_diagnostic_page_model()

    def logfile(self) -> bytes:
        """Log bundle as raw bytes (ZIP). Requires session auth."""
        return self.api.get_logfile()

    def firmware_status(self) -> dict:
        """Return firmware status: currentVersion, isUpdating, isDownloading, progress."""
        return self.api.get_firmware_status()

    def firmware_status_model(self) -> FirmwareStatus:
        """Return normalized, typed firmware status."""
        return self.api.get_firmware_status_model()

    def check_update(self) -> dict:
        """Return the X1 firmware update status."""
        return self.api.get_firmware_status()

    def upgrade_progress(self) -> dict:
        """Return the current upgrade progress."""
        return self.api.get_upgrade_progress()

    # ------------------------------------------------------------------ #
    # Inspection — X1-only iscwebservice commands                         #
    # ------------------------------------------------------------------ #

    def sonos_channels(self) -> list:
        """List of configured Sonos channels."""
        r = self._post({"command": "getSonosChannels"})
        return (r.get("data") or {}).get("channels", [])

    def logic_engine_page(self) -> dict:
        """Logic engine status: started-at, config-at, num-pages, num-nodes."""
        return self._post({"command": "getLogicEnginePage"})

    def openvpn_validity(self) -> dict:
        """OpenVPN certificate validity: enabled, notBefore, notAfter."""
        r = self._post({"command": "getOpenVpnCertificateValidity"})
        return r.get("data") or {}

    def app_value(self, app_name: str, key: str) -> str:
        """Read a key from the device's persistent key-value store."""
        r = self._post({"command": "getAppValue", "data": {"appName": app_name, "key": key}})
        return (r.get("data") or {}).get("value", "")

    # ------------------------------------------------------------------ #
    # Controls — shared iscwebservice commands                            #
    # ------------------------------------------------------------------ #

    def set_ntp(self, *, enabled: bool, server: str, interval_minutes: int = 10) -> dict:
        """Update the NTP configuration."""
        return self.api.set_ntp_config(
            enabled=enabled,
            server=server,
            interval_minutes=interval_minutes,
        )

    def set_ip(self, cfg: NetworkConfig) -> dict:
        """Update the IP configuration."""
        return self.api.set_ip_config(cfg)

    def enable_ssh(self, *, persistent: bool = True) -> None:
        """Enable SSH access."""
        self.api.enable_ssh(persistent=persistent)

    def disable_ssh(self) -> None:
        """Disable SSH access."""
        self.api.disable_ssh()

    def reboot(self) -> None:
        """Reboot the device."""
        self.api.reboot()

    def trigger_online_update(self) -> dict:
        """Trigger an online firmware update."""
        return self.api.trigger_online_update()

    def upload_firmware(self, path: Path) -> None:
        """Upload a firmware archive."""
        self.api.upload_firmware(path)

    def initiate_local_install(self) -> dict:
        """Start a local firmware installation."""
        return self.api.initiate_local_install()

    def wait_for_completion(self, poll_interval: float = 5.0, max_wait: float = 300.0) -> bool:
        """Wait for the device to finish an in-flight operation."""
        return self.api.wait_for_completion(poll_interval=poll_interval, max_wait=max_wait)

    def commissioning_test(self) -> dict:
        """Run the built-in commissioning test."""
        return self.api.commissioning_test()

    # ------------------------------------------------------------------ #
    # Controls — X1-only iscwebservice commands                           #
    # ------------------------------------------------------------------ #

    def set_syslog_severity(self, severity: int) -> None:
        """Set syslog verbosity (0=most verbose, 4=off)."""
        self._post({"command": "setSyslogSeverity", "data": {"syslogSeverity": severity}})

    def set_timezone(self, timezone_id: str) -> None:
        """Set timezone by numeric ID (e.g. '133' = Europe/Berlin, from device_info TimeZoneID)."""
        self._post({"command": "setTimeZone", "data": {"timeZoneID": timezone_id}})

    def set_programming_mode(self, enabled: bool) -> None:
        """Enable or disable KNX programming mode."""
        self._post({"command": "setProgrammingMode", "data": {"mode": enabled}})

    def set_app_value(self, app_name: str, key: str, value: str) -> None:
        """Write a key to the device's persistent key-value store."""
        self._post(
            {"command": "setAppValue", "data": {"appName": app_name, "key": key, "value": value}},
        )

    def restart_logic_engine(self) -> None:
        """Restart the on-device logic engine (mono process)."""
        self._post({"command": "restartLogicEngine"})

    def factory_reset(self) -> None:
        """Reset device to factory defaults via iscwebservice (X1-only; G1 uses GDS)."""
        self.api.factory_reset()
