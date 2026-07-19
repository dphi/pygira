"""Opt-in, read-only smoke tests against explicitly configured hardware."""

import os
from dataclasses import dataclass, field

import pytest

from pygira import G1, X1

pytestmark = pytest.mark.hardware


@dataclass(frozen=True)
class HardwareTarget:
    """Credential-safe hardware target loaded only from environment variables."""

    device_type: str
    host: str
    username: str
    password: str = field(repr=False)


@pytest.fixture(scope="module")
def hardware_target() -> HardwareTarget:
    """Load an explicitly enabled hardware target or skip the module."""
    if os.environ.get("PYGIRA_HARDWARE_TESTS") != "1":
        pytest.skip("set PYGIRA_HARDWARE_TESTS=1 to enable hardware tests")

    device_type = os.environ.get("PYGIRA_HARDWARE_DEVICE", "").lower()
    host = os.environ.get("PYGIRA_HARDWARE_HOST", "")
    password = os.environ.get("PYGIRA_HARDWARE_PASSWORD", "")
    if device_type not in {"g1", "x1"} or not host or not password:
        pytest.skip("hardware tests require device type, host, and password environment variables")
    username = os.environ.get("PYGIRA_HARDWARE_USERNAME", "device")
    return HardwareTarget(device_type, host, username, password)


def test_hardware_device_info_is_readable(hardware_target: HardwareTarget) -> None:
    """Read and normalize identity without changing device state."""
    facade_type = G1 if hardware_target.device_type == "g1" else X1
    device = facade_type(
        hardware_target.host,
        username=hardware_target.username,
        password=hardware_target.password,
    )

    info = device.device_info_model()

    assert info.firmware_version
    assert info.ip_address or info.serial_number or info.entity_id
