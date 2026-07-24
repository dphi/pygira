"""Pure application operations shared by CLI workflows."""

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pygira.models import NetworkConfig, WeatherStation


@dataclass(frozen=True)
class NetworkPatch:
    """Optional network values to merge with a device snapshot."""

    dhcp: bool | None = None
    ip_address: str | None = None
    subnet_mask: str | None = None
    default_gateway: str | None = None
    primary_dns: str | None = None
    secondary_dns: str | None = None


def _string_value(source: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value is not None:
            return str(value)
    return ""


def merge_network_config(
    current: Mapping[str, object],
    patch: NetworkPatch,
) -> NetworkConfig:
    """Apply optional network changes to a raw device-info snapshot."""
    current_dhcp = current.get("Dhcp", False)
    return NetworkConfig(
        dhcp=patch.dhcp if patch.dhcp is not None else bool(current_dhcp),
        ip_address=patch.ip_address or _string_value(current, "IpAddress"),
        subnet_mask=patch.subnet_mask or _string_value(current, "SubnetMask"),
        default_gateway=patch.default_gateway or _string_value(current, "DefaultGateway"),
        primary_dns=patch.primary_dns or _string_value(current, "NameServer", "PrimaryDNS"),
        secondary_dns=patch.secondary_dns or _string_value(current, "SecondaryDns", "SecondaryDNS"),
    )


def build_weather_settings(
    station: WeatherStation,
    *,
    guid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> str:
    """Build the persistent G1 weather settings payload without mutating the station."""
    guid = station.guid or str(guid_factory())
    return json.dumps(
        {
            "acceptedLicense": True,
            "weatherStations": [
                {
                    "weatherStationId": station.station_id,
                    "label": station.label,
                    "guid": guid,
                },
            ],
        },
    )
