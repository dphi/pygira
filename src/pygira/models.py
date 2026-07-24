"""Shared Pydantic models for G1 provisioning data."""

import sys
from collections.abc import Mapping
from ipaddress import IPv4Address, ip_address
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pygira.core.types import DeviceType


def _as_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_string(value: object) -> str:
    return "" if value is None else str(value)


class ConfigModel(BaseModel):
    """Base model for configuration files with typo-safe parsing."""

    model_config = ConfigDict(extra="forbid")


class DeviceConfig(ConfigModel):
    """Named device entry from devices.toml."""

    type: DeviceType
    host: str = ""
    ip: str = ""
    username: str = ""
    password: str = ""
    admin_password: str = ""
    app_username: str = ""
    app_password: str = ""

    @model_validator(mode="after")
    def _valid_device(self) -> "DeviceConfig":
        if self.type == DeviceType.UNKNOWN:
            msg = "Device type 'unknown' cannot be configured"
            raise ValueError(msg)
        if bool(self.host) == bool(self.ip):
            msg = "Exactly one of host or ip must be configured"
            raise ValueError(msg)
        address = self.address
        if "://" in address or "/" in address or any(char.isspace() for char in address):
            msg = "Device host must be a hostname or IP address without a URL scheme or path"
            raise ValueError(msg)
        password = self.admin_password or self.password
        if not password:
            msg = f"Configured {self.type.value} device is missing a password"
            raise ValueError(msg)
        return self

    @property
    def address(self) -> str:
        """Hostname or IP address used to reach the device."""
        return self.host or self.ip


class LocationConfig(ConfigModel):
    """Optional named grouping for devices."""

    name: str = ""
    devices: dict[str, DeviceConfig] = Field(default_factory=dict)


class LegacyG1Config(ConfigModel):
    """G1 credentials from the original apartment-oriented file format."""

    ip: str
    password: str
    username: str = ""


class LegacyX1Config(ConfigModel):
    """X1 credentials from the original apartment-oriented file format."""

    ip: str
    admin_password: str
    app_username: str = ""
    app_password: str = ""


class LegacyTksConfig(ConfigModel):
    """TKS-IP credentials from the original apartment-oriented file format."""

    ip: str
    username: str
    password: str


class LegacyApartmentConfig(ConfigModel):
    """One entry from the original ``[[apartments]]`` configuration."""

    id: str | int
    name: str = ""
    g1: LegacyG1Config | None = None
    x1: LegacyX1Config | None = None
    tks_ip: LegacyTksConfig | None = None


class PygiraConfig(ConfigModel):
    """Complete devices.toml configuration."""

    devices: dict[str, DeviceConfig] = Field(default_factory=dict)
    locations: dict[str, LocationConfig] = Field(default_factory=dict)
    apartments: list[LegacyApartmentConfig] = Field(default_factory=list, exclude=True)

    @model_validator(mode="after")
    def _normalize_legacy_apartments(self) -> "PygiraConfig":
        if not self.apartments:
            return self
        if self.devices or self.locations:
            msg = "apartments cannot be combined with devices or locations"
            raise ValueError(msg)

        locations: dict[str, LocationConfig] = {}
        for apartment in self.apartments:
            key = str(apartment.id)
            if key in locations:
                msg = f"Apartment id {key!r} is duplicated"
                raise ValueError(msg)

            devices: dict[str, DeviceConfig] = {}
            if apartment.g1 is not None:
                devices["g1"] = DeviceConfig(
                    type=DeviceType.G1,
                    ip=apartment.g1.ip,
                    username=apartment.g1.username,
                    password=apartment.g1.password,
                )
            if apartment.x1 is not None:
                devices["x1"] = DeviceConfig(
                    type=DeviceType.X1,
                    ip=apartment.x1.ip,
                    admin_password=apartment.x1.admin_password,
                    app_username=apartment.x1.app_username,
                    app_password=apartment.x1.app_password,
                )
            if apartment.tks_ip is not None and apartment.tks_ip.password:
                devices["tks_ip"] = DeviceConfig(
                    type=DeviceType.TKS_IP,
                    ip=apartment.tks_ip.ip,
                    username=apartment.tks_ip.username,
                    password=apartment.tks_ip.password,
                )
            locations[key] = LocationConfig(name=apartment.name, devices=devices)

        self.locations = locations
        return self


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
    """Normalized read-only device identity and network snapshot."""

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
    app_name: str = ""
    serial_number: str = ""

    @classmethod
    def from_webservice(cls, response: Mapping[str, Any]) -> "DeviceInfo":
        """Normalize a G1 or X1 getDeviceInfo response envelope."""
        nested = response.get("data", response)
        data = nested if isinstance(nested, Mapping) else {}
        return cls(
            firmware_version=_as_string(data.get("CurrentFirmwareVersion")),
            mac_address=_as_string(data.get("MacAddress")),
            ip_address=_as_string(data.get("IpAddress")),
            subnet_mask=_as_string(data.get("SubnetMask")),
            default_gateway=_as_string(data.get("DefaultGateway")),
            primary_dns=_as_string(data.get("NameServer", data.get("PrimaryDNS"))),
            secondary_dns=_as_string(data.get("SecondaryDns", data.get("SecondaryDNS"))),
            dhcp=_as_bool(data.get("Dhcp"), default=True),
            device_name=_as_string(data.get("KIM-FriendlyName", data.get("DeviceName"))),
            entity_id=_as_string(data.get("EntityId", data.get("DeviceId"))),
            app_name=_as_string(data.get("AppName")),
            serial_number=_as_string(data.get("SerialNumber")),
        )


class FirmwareStatus(BaseModel):
    """Normalized firmware-update state shared by G1 and X1 responses."""

    current_version: str = ""
    available_version: str = ""
    state: str = ""
    progress: float | None = None
    is_updating: bool = False
    is_downloading: bool = False

    @classmethod
    def from_webservice(cls, response: Mapping[str, Any]) -> "FirmwareStatus":
        """Normalize a firmware status response envelope."""
        nested = response.get("data", response)
        data = nested if isinstance(nested, Mapping) else {}
        progress_value = data.get("progress")
        try:
            progress = float(progress_value) if progress_value is not None else None
        except (TypeError, ValueError):
            progress = None
        return cls(
            current_version=_as_string(
                data.get("currentVersion", data.get("CurrentFirmwareVersion", "")),
            ),
            available_version=_as_string(
                data.get("offlineVersion", data.get("availableVersion", "")),
            ),
            state=_as_string(data.get("state", data.get("status"))),
            progress=progress,
            is_updating=_as_bool(data.get("isUpdating")),
            is_downloading=_as_bool(data.get("isDownloading")),
        )


class DiagnosticSection(BaseModel):
    """One named free-text section from a diagnostic page."""

    title: str
    blob: str


class DiagnosticPage(BaseModel):
    """Normalized collection of device diagnostic sections."""

    sections: list[DiagnosticSection] = Field(default_factory=list)

    @classmethod
    def from_webservice(cls, response: Mapping[str, Any]) -> "DiagnosticPage":
        """Normalize a G1 or X1 diagnostic-page response envelope."""
        nested = response.get("data", response)
        data = nested if isinstance(nested, Mapping) else {}
        raw_sections = data.get("diagnosticpage", [])
        if not isinstance(raw_sections, list):
            raw_sections = []
        sections = [
            DiagnosticSection(
                title=_as_string(section.get("title")),
                blob=_as_string(section.get("blob")),
            )
            for section in raw_sections
            if isinstance(section, Mapping)
        ]
        return cls(sections=sections)


class TksConnectionStatus(BaseModel):
    """Normalized G1-to-TKS-IP connection state."""

    present: bool
    state: str | None = None
    disconnect_reason: str | None = None

    @classmethod
    def from_gds(cls, response: Mapping[str, Any]) -> "TksConnectionStatus":
        """Normalize the fixed G1 TKS connection datapoints."""
        state = response.get("state")
        reason = response.get("disconnect_reason")
        return cls(
            present=_as_bool(response.get("present")),
            state=_as_string(state) if state is not None else None,
            disconnect_reason=_as_string(reason) if reason is not None else None,
        )
