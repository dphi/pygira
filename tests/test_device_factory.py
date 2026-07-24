"""Tests for resolved device construction."""

import pytest

from pygira.core.types import DeviceType
from pygira.devices.base import DeviceProfile, ResolvedTarget
from pygira.devices.g1 import G1
from pygira.devices.g1 import PROFILE as G1_PROFILE
from pygira.devices.registry import create_device
from pygira.devices.x1 import PROFILE as X1_PROFILE
from pygira.devices.x1 import X1
from pygira.exceptions import UnsupportedCapabilityError

TIMEOUT = 12.0


@pytest.mark.parametrize(
    ("profile", "expected_type"),
    [(G1_PROFILE, G1), (X1_PROFILE, X1)],
)
def test_create_device_uses_resolved_profile(
    profile: DeviceProfile,
    expected_type: type[G1] | type[X1],
) -> None:
    target = ResolvedTarget(profile, "device.local", "device", "secret", TIMEOUT)

    device = create_device(target)

    assert isinstance(device, expected_type)
    assert device.api.host == "device.local"
    assert device.api.timeout == TIMEOUT


def test_create_device_rejects_profiles_without_a_facade() -> None:
    profile = DeviceProfile(
        device_type=DeviceType.TKS_IP,
        display_name="TKS-IP",
        capabilities={"weather": False, "tks": False},
        api_prefix="",
    )
    target = ResolvedTarget(profile, "tks.local", "admin", "secret", TIMEOUT)

    with pytest.raises(UnsupportedCapabilityError, match="No device facade"):
        create_device(target)
