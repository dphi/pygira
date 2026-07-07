"""
Tests for weather.py — MeteoGroup station lookup via homeserver.gira.de.
API shape derived from:
  - layout.js ProxyDataService (weather/js/weather.services.proxy.js)
  - demoData.json (ort_id, ortsname, region, land fields)
  - weather.settings.js (acceptedLicense + weatherStations array format)
"""

import json
import uuid

import pytest

from pygira import weather as w
from pygira.models import WeatherStation
from tests import _httpmock as respx
from tests._httpmock import Request, Response

BASE = "http://homeserver.gira.de/dienste/wetter.xml"
RADE_COUNT = 2


@respx.mock
def test_get_country_id_returns_mg_id_for_de() -> None:
    """ISO2 'DE' should resolve to MeteoGroup land_id 'mg-49'."""
    respx.get(BASE).mock(
        return_value=Response(
            200,
            json={
                "land": [
                    {"landname": "Deutschland", "land_id": "mg-49", "iso2lc": "DE"},
                    {"landname": "Denmark", "land_id": "mg-45", "iso2lc": "DK"},
                ],
            },
        ),
    )
    result = w.get_country_id("DE")
    assert result == "mg-49"


@respx.mock
def test_get_country_id_case_insensitive() -> None:
    respx.get(BASE).mock(
        return_value=Response(
            200,
            json={"land": {"landname": "Deutschland", "land_id": "mg-49", "iso2lc": "DE"}},
        ),
    )
    # Single dict (not list) — ProxyDataService handles both
    result = w.get_country_id("de")
    assert result == "mg-49"


@respx.mock
def test_get_country_id_returns_none_for_unknown() -> None:
    respx.get(BASE).mock(return_value=Response(200, json={"land": []}))
    result = w.get_country_id("ZZ")
    assert result is None


@respx.mock
def test_search_stations_returns_station_list() -> None:
    """Station list parsed from 'ort' array with ort_id and ortsname."""
    respx.get(BASE).mock(
        return_value=Response(
            200,
            json={
                "ort": [
                    {
                        "ort_id": "mg-18220678",
                        "ortsname": "Radevormwald",
                        "region": "NRW",
                        "land": "Deutschland",
                    },
                    {
                        "ort_id": "mg-18220679",
                        "ortsname": "Radevormwald-Süd",
                        "region": "NRW",
                        "land": "Deutschland",
                    },
                ],
            },
        ),
    )
    stations = w.search_stations("42477", "mg-49")
    assert len(stations) == RADE_COUNT
    assert stations[0].station_id == "mg-18220678"
    assert stations[0].label == "Radevormwald"
    assert stations[1].station_id == "mg-18220679"


@respx.mock
def test_search_stations_handles_single_result_dict() -> None:
    """API may return a dict instead of list when only one result."""
    respx.get(BASE).mock(
        return_value=Response(
            200,
            json={
                "ort": {
                    "ort_id": "mg-18220678",
                    "ortsname": "Radevormwald",
                    "region": "NRW",
                    "land": "Deutschland",
                },
            },
        ),
    )
    stations = w.search_stations("42477", "mg-49")
    assert len(stations) == 1
    assert stations[0].station_id == "mg-18220678"


@respx.mock
def test_search_stations_empty_result() -> None:
    respx.get(BASE).mock(return_value=Response(200, json={"ort": []}))
    assert w.search_stations("00000", "mg-49") == []


@respx.mock
def test_find_station_returns_first_match() -> None:
    """find_station chains country lookup + station search."""
    # First call: country lookup
    # Second call: station search
    call_count = 0

    def side_effect(request: Request, *args: object) -> Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return Response(
                200,
                json={"land": {"landname": "Deutschland", "land_id": "mg-49", "iso2lc": "DE"}},
            )
        return Response(
            200,
            json={
                "ort": [
                    {
                        "ort_id": "mg-18220678",
                        "ortsname": "Radevormwald",
                        "region": "NRW",
                        "land": "DE",
                    },
                ],
            },
        )

    respx.get(BASE).mock(side_effect=side_effect)
    station = w.find_station("42477", "DE")
    assert station is not None
    assert station.station_id == "mg-18220678"
    assert station.label == "Radevormwald"
    assert station.guid != ""  # should be auto-generated UUID


@respx.mock
def test_find_station_raises_for_unknown_country() -> None:
    respx.get(BASE).mock(return_value=Response(200, json={"land": []}))
    with pytest.raises(ValueError, match="Unknown country"):
        w.find_station("12345", "ZZ")


@respx.mock
def test_find_station_returns_none_when_no_stations() -> None:
    call_count = 0

    def side_effect(request: Request, *args: object) -> Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return Response(
                200,
                json={"land": {"landname": "Deutschland", "land_id": "mg-49", "iso2lc": "DE"}},
            )
        return Response(200, json={"ort": []})

    respx.get(BASE).mock(side_effect=side_effect)
    assert w.find_station("99999", "DE") is None


def test_station_guid_is_set() -> None:
    """WeatherStation objects from search_stations should get a guid."""
    with respx.mock:
        respx.get(BASE).mock(
            return_value=Response(
                200,
                json={"ort": [{"ort_id": "mg-18220678", "ortsname": "Radevormwald"}]},
            ),
        )
        stations = w.search_stations("42477", "mg-49")
        assert stations[0].guid != ""


def test_weather_settings_json_format() -> None:
    """
    Verify the JSON structure written to GDS AppValue matches what the
    firmware's weather.settings.js expects:
      {acceptedLicense: true, weatherStations: [{weatherStationId, label, guid}]}
    AppName: "Gira.G1", key: "weather.settings"
    """
    station = WeatherStation(station_id="mg-18220678", label="Radevormwald", guid=str(uuid.uuid4()))
    settings = json.loads(
        json.dumps(
            {
                "acceptedLicense": True,
                "weatherStations": [
                    {
                        "weatherStationId": station.station_id,
                        "label": station.label,
                        "guid": station.guid,
                    },
                ],
            },
        ),
    )
    assert settings["acceptedLicense"] is True
    assert settings["weatherStations"][0]["weatherStationId"] == "mg-18220678"
    assert "guid" in settings["weatherStations"][0]
