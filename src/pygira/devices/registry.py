"""Device profile registry."""

from pygira.core.types import DeviceType
from pygira.devices.base import DeviceProfile
from pygira.devices.g1 import PROFILE as G1_PROFILE
from pygira.devices.x1 import PROFILE as X1_PROFILE

_PROFILES = {
    DeviceType.G1: G1_PROFILE,
    DeviceType.X1: X1_PROFILE,
}


def get_profile(device_type: DeviceType) -> DeviceProfile:
    """Return a profile for the given device type."""
    return _PROFILES[device_type]
