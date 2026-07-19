"""iscwebservice HTTP client — port 80 (via nginx proxy).

Auth: Authorization: Basic <base64(user:password)>  (capital "Basic")
Handles firmware update commands.
"""

import base64
import time
from pathlib import Path
from typing import Any, Literal, cast

from pygira import _http as httpx
from pygira.auth import AUTH_ERROR_CODES, authenticated_request
from pygira.exceptions import DeviceApiError, ProtocolError
from pygira.models import DeviceInfo, DiagnosticPage, FirmwareStatus, NetworkConfig

_PROTOCOL = "iscwebservice"


def _auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


class ApiClient:
    """HTTP client for the iscwebservice API on port 80."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        timeout: float = 60.0,
        api_prefix: str = "/api",
    ) -> None:
        """Initialize the ApiClient.

        Args:
            host: Hostname or IP of the device (no scheme, e.g. "192.168.1.100").
            username: Username for basic auth.
            password: Password for basic auth.
            timeout: Request timeout in seconds.
            api_prefix: URL path prefix, e.g. "/api" (G1) or "/webservice" (X1).

        """
        self.host = host
        self.username = username
        self.password = password
        self.api_prefix = api_prefix.rstrip("/")
        self._headers = {
            "Authorization": _auth_header(username, password),
            "Content-Type": "application/json",
        }
        self.timeout = timeout

    def _post(self, payload: dict[str, Any]) -> dict:
        command = str(payload.get("command", "unknown"))
        with httpx.Client(base_url=f"http://{self.host}", timeout=self.timeout) as client:
            resp = client.post(f"{self.api_prefix}", json=payload, headers=self._headers)
            resp.raise_for_status()
            data = cast("dict[str, Any]", resp.json() if resp.content else {})
            if data.get("error"):
                if str(data.get("id", "")) in AUTH_ERROR_CODES and self.api_prefix in {
                    "/api",
                    "/webservice",
                }:
                    return self._post_with_session_fallback(payload, data)
                raise DeviceApiError(command, data)
            return data

    def _post_with_session_fallback(
        self,
        payload: dict[str, Any],
        first_error: dict[str, Any],
    ) -> dict:
        command = str(payload.get("command", "unknown"))
        path = self.api_prefix
        with httpx.Client(base_url=f"http://{self.host}", timeout=self.timeout) as client:
            retry_data = {k: v for k, v in payload.items() if k != "command"}
            ws_data = (
                retry_data.get("data")
                if set(retry_data.keys()) == {"data"}
                else (retry_data or None)
            )
            return authenticated_request(
                client,
                path,
                self.username,
                self.password,
                command,
                ws_data,
                first_error=first_error,
            )

    def check_online_update(self) -> dict:
        """Query available online firmware update info."""
        return self._post({"command": "infoonline"})

    def get_firmware_status(self) -> dict:
        """Query firmware status (used by X1 web UI polling)."""
        return self._post({"command": "getFirmwareStatus"})

    def get_firmware_status_model(self) -> FirmwareStatus:
        """Query and normalize firmware status."""
        return FirmwareStatus.from_webservice(self.get_firmware_status())

    def get_device_info(self, *, force_long: bool = False) -> dict:
        """Fetch device info from webservice API."""
        payload: dict[str, Any] = {"command": "getDeviceInfo"}
        if force_long:
            payload["data"] = {"forceLong": True}
        return self._post(payload)

    def get_device_info_model(self, *, force_long: bool = False) -> DeviceInfo:
        """Fetch and normalize device information."""
        return DeviceInfo.from_webservice(self.get_device_info(force_long=force_long))

    def get_diagnostic_page(self, *, completely: bool = True) -> dict:
        """Fetch diagnostic page data from webservice API."""
        return self._post({"command": "getDiagnosticPage", "data": {"completely": completely}})

    def get_diagnostic_page_model(self, *, completely: bool = True) -> DiagnosticPage:
        """Fetch and normalize device diagnostic sections."""
        return DiagnosticPage.from_webservice(self.get_diagnostic_page(completely=completely))

    def set_ntp_config(self, *, enabled: bool, server: str, interval_minutes: int) -> dict:
        """Set NTP configuration via webservice API."""
        return self._post(
            {
                "command": "setNtpConfig",
                "data": {
                    "Ntp": enabled,
                    "NtpServerAddress": server,
                    "NtpInterval": str(interval_minutes),
                },
            },
        )

    def set_ip_config(self, cfg: NetworkConfig) -> dict:
        """Set network configuration via webservice API."""
        data: dict[str, Any] = {
            "Dhcp": cfg.dhcp,
        }
        if not cfg.dhcp:
            data["IpAddress"] = cfg.ip_address
            data["SubnetMask"] = cfg.subnet_mask
            data["DefaultGateway"] = cfg.default_gateway
            data["NameServer"] = cfg.primary_dns
            if cfg.secondary_dns:
                data["SecondaryDns"] = cfg.secondary_dns
        return self._post({"command": "setIpConfig", "data": data})

    def get_logfile(self) -> bytes:
        """Fetch diagnostic logfile ZIP returned by webservice command getLogfile."""
        result = self._post({"command": "getLogfile"})
        content_b64 = ((result or {}).get("data") or {}).get("content")
        if not content_b64:
            raise ProtocolError(
                _PROTOCOL,
                "getLogfile",
                "missing-content",
                result,
            )
        return base64.b64decode(content_b64)

    def trigger_online_update(self) -> dict:
        """Start online firmware update from Gira download server."""
        return self._post({"command": "startonlineupdate"})

    def get_upgrade_progress(self) -> dict:
        """Get current firmware update progress."""
        return self._post({"command": "progress"})

    def upload_firmware(self, firmware_path: Path) -> None:
        """Upload a local firmware ZIP file to the device."""
        data = firmware_path.read_bytes()
        with httpx.Client(base_url=f"http://{self.host}", timeout=300.0) as client:
            resp = client.post(
                f"{self.api_prefix}/upload/v2",
                content=data,
                headers={
                    "Authorization": self._headers["Authorization"],
                    "Content-Type": "application/x-zip-compressed",
                },
            )
            resp.raise_for_status()

    def initiate_local_install(self) -> dict:
        """Trigger installation of the previously uploaded firmware file."""
        return self._post({"command": "initlocalupload"})

    def wait_for_completion(self, poll_interval: float = 5.0, max_wait: float = 300.0) -> bool:
        """Poll progress until done or timeout. Returns True if completed."""
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            try:
                resp = self.get_upgrade_progress()
                state = resp.get("state", resp.get("status", ""))
                if state in ("done", "completed", "finished", "success"):
                    return True
                if state in ("error", "failed"):
                    raise ProtocolError(
                        _PROTOCOL,
                        "progress",
                        state,
                        resp,
                    )
            except httpx.HTTPError:
                # Device may be rebooting
                pass
            time.sleep(poll_interval)
        return False

    def control_service(self, service: str, control: str) -> dict:
        """Control a system service (e.g. enable/disable/start/stop)."""
        return self._post({"command": "controlService", "service": service, "control": control})

    def enable_ssh(self, *, persistent: bool = True) -> None:
        """Enable SSH access on the device.

        persistent=True: touches /opt/userdata/.ssh-enabled (survives reboot) then starts sshd.
        persistent=False: starts sshd once without persisting (start-once).
        """
        if persistent:
            self.control_service("S50sshd", "enable")
            self.control_service("S50sshd", "start")
        else:
            self.control_service("S50sshd", "start-once")

    def disable_ssh(self) -> None:
        """Stop sshd and remove the persistent-enable marker."""
        self.control_service("S50sshd", "stop")
        self.control_service("S50sshd", "disable")

    def start_ssh(self, mode: Literal["persistent", "once"] = "persistent") -> None:
        """Start SSH in the given mode.

        Args:
            mode: "persistent" to enable and start sshd persistently, or
                  "once" to start sshd for this session only.

        """
        if mode == "persistent":
            self.enable_ssh(persistent=True)
        else:
            self.enable_ssh(persistent=False)

    def reboot(self) -> None:
        """Reboot the device via JSON API."""
        self._post({"command": "reboot"})

    def factory_reset(self) -> None:
        """Reset the device via JSON API."""
        self._post({"command": "factoryReset"})

    def commissioning_test(self) -> dict:
        """Run the built-in commissioning test (GET /api/commissioningtest)."""
        with httpx.Client(base_url=f"http://{self.host}", timeout=self.timeout) as client:
            resp = client.get(f"{self.api_prefix}/commissioningtest", headers=self._headers)
            resp.raise_for_status()
            return cast("dict[str, Any]", resp.json() if resp.content else {})
