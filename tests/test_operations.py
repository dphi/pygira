"""Tests for shared application operations."""

import json
import uuid

from pygira.models import WeatherStation
from pygira.operations import NetworkPatch, build_weather_settings, merge_network_config

WEATHER_GUID = uuid.UUID("12345678-1234-5678-1234-567812345678")


def test_merge_network_config_preserves_unmodified_snapshot_values() -> None:
    current = {
        "Dhcp": True,
        "IpAddress": "192.0.2.10",
        "SubnetMask": "255.255.255.0",
        "DefaultGateway": "192.0.2.1",
        "NameServer": "192.0.2.53",
        "SecondaryDNS": "192.0.2.54",
    }

    result = merge_network_config(
        current,
        NetworkPatch(dhcp=False, ip_address="192.0.2.20"),
    )

    assert result.dhcp is False
    assert result.ip_address == "192.0.2.20"
    assert result.subnet_mask == "255.255.255.0"
    assert result.default_gateway == "192.0.2.1"
    assert result.primary_dns == "192.0.2.53"
    assert result.secondary_dns == "192.0.2.54"


def test_build_weather_settings_is_deterministic_and_does_not_mutate_input() -> None:
    station = WeatherStation(station_id="station-1", label="Berlin")

    result = json.loads(
        build_weather_settings(station, guid_factory=lambda: WEATHER_GUID),
    )

    assert result == {
        "acceptedLicense": True,
        "weatherStations": [
            {
                "weatherStationId": "station-1",
                "label": "Berlin",
                "guid": str(WEATHER_GUID),
            },
        ],
    }
    assert station.guid == ""


def test_build_weather_settings_preserves_existing_guid() -> None:
    station = WeatherStation(
        station_id="station-1",
        label="Berlin",
        guid="existing-guid",
    )

    result = json.loads(build_weather_settings(station))

    assert result["weatherStations"][0]["guid"] == "existing-guid"
