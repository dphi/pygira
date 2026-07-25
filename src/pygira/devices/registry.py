"""Device profile registry."""

from pygira.core.types import DeviceType
from pygira.devices.base import DeviceProfile, ResolvedTarget
from pygira.devices.g1 import G1
from pygira.devices.g1 import PROFILE as G1_PROFILE
from pygira.devices.tks_ip import PROFILE as TKS_IP_PROFILE
from pygira.devices.tks_ip import TksIp
from pygira.devices.x1 import PROFILE as X1_PROFILE
from pygira.devices.x1 import X1
from pygira.exceptions import UnsupportedCapabilityError

Device = G1 | X1 | TksIp

_PROFILES = {
    DeviceType.G1: G1_PROFILE,
    DeviceType.X1: X1_PROFILE,
    DeviceType.TKS_IP: TKS_IP_PROFILE,
}


def get_profile(device_type: DeviceType) -> DeviceProfile:
    """Return a profile for the given device type."""
    return _PROFILES[device_type]


def create_device(target: ResolvedTarget) -> Device:
    """Construct the supported device facade for resolved connection settings."""
    if target.profile.device_type == DeviceType.G1:
        return G1(
            target.host,
            target.username,
            target.password,
            timeout=target.timeout,
        )
    if target.profile.device_type == DeviceType.X1:
        return X1(
            target.host,
            target.username,
            target.password,
            timeout=target.timeout,
        )
    if target.profile.device_type == DeviceType.TKS_IP:
        return TksIp(
            target.host,
            target.username,
            target.password,
            timeout=target.timeout,
            aes_key=target.aes_key,
        )
    msg = f"No device facade is available for {target.profile.device_type.value!r}"
    raise UnsupportedCapabilityError(msg)
