"""MeteoGroup weather station lookup via homeserver.gira.de."""

import uuid
from typing import Any, cast

from pygira import _http as httpx
from pygira.exceptions import InvalidInputError
from pygira.models import WeatherStation

_BASE = "http://homeserver.gira.de/dienste/wetter.xml"
_PARAMS_BASE = {"clientversion": "2", "lang": "de", "jsonon": "1"}


def _get(params: dict[str, object]) -> dict[str, Any]:
    with httpx.Client(timeout=15.0) as client:
        resp = client.get(_BASE, params={**_PARAMS_BASE, **params})
        resp.raise_for_status()
        return cast("dict[str, Any]", resp.json())


def get_country_id(iso2_code: str) -> str | None:
    """Return the MeteoGroup land_id (e.g. 'mg-49') for a given ISO 3166-1 alpha-2 code.

    Searches by country name/code prefix.
    """
    data = _get({"query": "laender", "searchmethod": "begins", "searchstring": iso2_code.upper()})
    countries = data.get("land", [])
    if not isinstance(countries, list):
        countries = [countries]
    for c in countries:
        iso = c.get("iso2lc", "").upper()
        if iso == iso2_code.upper():
            return c.get("land_id")
    return countries[0].get("land_id") if countries else None


def search_stations(zip_code: str, land_id: str) -> list[WeatherStation]:
    """Return weather stations matching zip_code within the given country."""
    data = _get(
        {
            "query": "wetterstationen",
            "searchmethod": "begins",
            "searchstring": zip_code,
            "land": land_id,
        },
    )
    raw = data.get("ort", [])
    if not isinstance(raw, list):
        raw = [raw]
    stations = []
    for item in raw:
        ort_id = item.get("ort_id")
        if ort_id:
            stations.append(
                WeatherStation(
                    station_id=ort_id,
                    label=item.get("ortsname", ort_id),
                    guid=str(uuid.uuid4()),
                ),
            )
    return stations


def find_station(zip_code: str, country_iso2: str = "DE") -> WeatherStation | None:
    """Find best-matching station for a zip code. Returns None if not found."""
    land_id = get_country_id(country_iso2)
    if not land_id:
        msg = f"Unknown country: {country_iso2!r}"
        raise InvalidInputError(msg)
    stations = search_stations(zip_code, land_id)
    return stations[0] if stations else None
