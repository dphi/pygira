"""G1 device profile and unified access class."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from ssl import SSLContext
from typing import TypeVar

from pygira.api import ApiClient
from pygira.core.types import DeviceCapabilities, DeviceType
from pygira.devices.base import DeviceProfile
from pygira.gds import GdsClient, run_gds
from pygira.models import (
    DeviceInfo,
    DiagnosticPage,
    FirmwareStatus,
    NetworkConfig,
    TksConnectionStatus,
)

T = TypeVar("T")

PROFILE = DeviceProfile(
    device_type=DeviceType.G1,
    display_name="Gira G1",
    capabilities=DeviceCapabilities(weather=True, tks=True),
    api_prefix="/api",
    default_username="device",
)


class G1:
    """Unified access to the Gira G1 — iscwebservice (port 80) and GDS WebSocket (port 4432).

    Inspection methods return raw dicts/bytes from the device.
    Control methods raise public PygiraError subclasses on operational failure.
    """

    def __init__(  # noqa: PLR0913 - explicit connection and TLS options
        self,
        host: str,
        username: str = "device",
        password: str = "",
        timeout: float = 15.0,
        *,
        verify_tls: bool = False,
        ssl_context: SSLContext | None = None,
    ) -> None:
        """Create a G1 facade for a single host."""
        self.api = ApiClient(host, username, password, api_prefix="/api", timeout=timeout)
        self._host = host
        self._username = username
        self._password = password
        self._timeout = timeout
        self._verify_tls = verify_tls
        self._ssl_context = ssl_context

    def _gds(self, coro: Callable[[GdsClient], Awaitable[T]]) -> T:
        return run_gds(
            self._host,
            self._username,
            self._password,
            coro,
            self._timeout,
            verify_tls=self._verify_tls,
            ssl_context=self._ssl_context,
        )

    # ------------------------------------------------------------------ #
    # Inspection — iscwebservice (port 80)                                #
    # ------------------------------------------------------------------ #

    def device_info(self, *, long: bool = True) -> dict:
        """Return device information from iscwebservice."""
        return self.api.get_device_info(force_long=long)

    def device_info_model(self, *, long: bool = True) -> DeviceInfo:
        """Return normalized, typed device information."""
        return self.api.get_device_info_model(force_long=long)

    def diagnostic_page(self) -> dict:
        """Return the diagnostic page from iscwebservice."""
        return self.api.get_diagnostic_page()

    def diagnostic_page_model(self) -> DiagnosticPage:
        """Return normalized diagnostic sections."""
        return self.api.get_diagnostic_page_model()

    def logfile(self) -> bytes:
        """Return the diagnostic log bundle from iscwebservice."""
        return self.api.get_logfile()

    def firmware_status(self) -> dict:
        """Return the current firmware status."""
        return self.api.get_firmware_status()

    def firmware_status_model(self) -> FirmwareStatus:
        """Return normalized, typed firmware status."""
        return self.api.get_firmware_status_model()

    def check_update(self) -> dict:
        """Return available online firmware update information."""
        return self.api.check_online_update()

    def upgrade_progress(self) -> dict:
        """Return the current upgrade progress."""
        return self.api.get_upgrade_progress()

    # ------------------------------------------------------------------ #
    # Inspection — GDS WebSocket (port 4432)                              #
    # ------------------------------------------------------------------ #

    def process_view(self) -> dict:
        """Return the full GDS process view (devices, channels, datapoints with live values)."""
        return self._gds(lambda c: c.get_process_view())

    def tks_status(self) -> dict[str, object]:
        """Return the live DcsVHsGUI.Connection:State datapoint when available."""
        return self._gds(lambda c: c.get_tks_status())

    def tks_status_model(self) -> TksConnectionStatus:
        """Return normalized TKS-IP connection state."""
        return self._gds(lambda c: c.get_tks_status_model())

    def device_config(self) -> dict[str, str]:
        """Return the flat GDS ipc device-config dict (~100 keys)."""
        return self._gds(lambda c: c.get_device_config())

    def app_value(self, app_name: str, key: str) -> object:
        """Return a persistent GDS app value."""
        return self._gds(lambda c: c.get_app_value(app_name, key))

    # ------------------------------------------------------------------ #
    # Controls — iscwebservice (port 80)                                  #
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
        """Reboot via iscwebservice. For GDS-path reboot use restart()."""
        self.api.reboot()

    def trigger_online_update(self) -> dict:
        """Trigger an online firmware update check."""
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
    # Controls — GDS WebSocket (port 4432)                                #
    # ------------------------------------------------------------------ #

    def set_location(self, lat: float, lon: float) -> None:
        """Set device location coordinates (used for weather feed). Format: 6 decimal places."""
        self._gds(lambda c: c.set_location(lat, lon))

    def set_device_config(self, values: dict[str, str]) -> None:
        """Write one or more flat ipc device-config keys via GDS SetDeviceConfig."""
        self._gds(lambda c: c.set_device_config(values))

    def set_app_value(self, app_name: str, key: str, value: str) -> None:
        """Write a persistent GDS app value."""
        self._gds(lambda c: c.set_app_value(app_name, key, value))

    def configure_tks(self, tks_ip: str, tks_user: str, tks_pass: str) -> None:
        """Write TKS-IP credentials to the Connect channel and trigger reconnect."""
        self._gds(lambda c: c.configure_tks(tks_ip, tks_user, tks_pass))

    def restart(self) -> None:
        """Restart via GDS (type: Device). For iscwebservice-path reboot use reboot()."""
        self._gds(lambda c: c.restart())

    def factory_reset(self) -> None:
        """Reset the device via GDS."""
        self._gds(lambda c: c.factory_reset())
