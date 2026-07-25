"""Opt-in, read-only smoke tests against explicitly configured hardware."""

import os
from dataclasses import dataclass, field

import pytest

from pygira import G1, X1
from pygira import config_service as cs

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
    if device_type not in {"g1", "x1", "tks-ip"} or not host:
        pytest.skip("hardware tests require a supported device type and host")
    if device_type != "tks-ip" and not password:
        pytest.skip("G1/X1 hardware tests require a device password environment variable")
    username = os.environ.get("PYGIRA_HARDWARE_USERNAME", "device")
    return HardwareTarget(device_type, host, username, password)


def test_hardware_read_only_status(hardware_target: HardwareTarget) -> None:
    """Read device identity or TKS-IP health without changing device state."""
    if hardware_target.device_type == "tks-ip":
        status = cs.get_tks_status(hardware_target.host)
        assert status.bootstrap_reachable
        return

    facade_type = G1 if hardware_target.device_type == "g1" else X1
    device = facade_type(
        hardware_target.host,
        username=hardware_target.username,
        password=hardware_target.password,
    )

    info = device.device_info_model()

    assert info.firmware_version
    assert info.ip_address or info.serial_number or info.entity_id
