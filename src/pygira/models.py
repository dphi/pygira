"""Shared Pydantic models for G1 provisioning data."""

import sys
from ipaddress import IPv4Address, ip_address
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from pydantic import BaseModel, field_validator


def _check_ip(v: str) -> str:
    if v:
        ip_address(v)
    return v


class DeviceConfig(BaseModel):
    """Named device entry from devices.toml."""

    type: str
    host: str = ""
    ip: str = ""
    username: str = ""
    password: str = ""
    admin_password: str = ""
    app_username: str = ""
    app_password: str = ""

    @property
    def address(self) -> str:
        """Hostname or IP address used to reach the device."""
        return self.host or self.ip


class LocationConfig(BaseModel):
    """Optional named grouping for devices."""

    name: str = ""
    devices: dict[str, DeviceConfig] = {}


class PygiraConfig(BaseModel):
    """Complete devices.toml configuration."""

    devices: dict[str, DeviceConfig] = {}
    locations: dict[str, LocationConfig] = {}


def load_config(path: str | Path) -> PygiraConfig:
    """Load the full pygira configuration file."""
    with Path(path).open("rb") as f:
        data = tomllib.load(f)
    return PygiraConfig(**data)


class NetworkConfig(BaseModel):
    """Network interface configuration for a G1 device."""

    dhcp: bool = True
    ip_address: str = ""  # "" = keep current value on device
    subnet_mask: str = ""
    default_gateway: str = ""
    primary_dns: str = ""
    secondary_dns: str = ""

    @field_validator("ip_address", "default_gateway", "primary_dns", "secondary_dns")
    @classmethod
    def _valid_ip(cls, v: str) -> str:
        if v:
            ip_address(v)  # raises ValueError -> ValidationError before we push to the device
        return v

    @field_validator("subnet_mask")
    @classmethod
    def _valid_mask(cls, v: str) -> str:
        if v:
            IPv4Address(v)
        return v


class WeatherStation(BaseModel):
    """MeteoGroup weather station reference."""

    station_id: str
    label: str
    guid: str = ""


class DeviceInfo(BaseModel):
    """Read-only snapshot of G1 device identity and network state."""

    firmware_version: str = ""
    mac_address: str = ""
    ip_address: str = ""
    subnet_mask: str = ""
    default_gateway: str = ""
    primary_dns: str = ""
    secondary_dns: str = ""
    dhcp: bool = True
    device_name: str = ""
    entity_id: str = ""
