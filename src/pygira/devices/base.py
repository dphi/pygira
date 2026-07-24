"""Device profile interface."""

from dataclasses import dataclass

from pygira.core.types import DeviceCapabilities, DeviceType


@dataclass(frozen=True)
class DeviceProfile:
    """Profile metadata for a supported device family."""

    device_type: DeviceType
    display_name: str
    capabilities: DeviceCapabilities
    api_prefix: str
    # G1 with UserManagement=false logs in as the fixed "device" account, not "admin".
    default_username: str = "admin"


@dataclass(frozen=True)
class ResolvedTarget:
    """Effective connection settings for one resolved device."""

    profile: DeviceProfile
    host: str
    username: str
    password: str
    timeout: float
