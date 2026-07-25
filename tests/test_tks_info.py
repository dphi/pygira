"""Focused coverage for TKS-IP Administration device information."""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from pygira import tks_web
from pygira.tks_web import TksWebClient
from tests import _httpmock as respx
from tests._httpmock import Request, Response

HOST = "192.168.1.100"
ROOT_HTML = '<script>decodeCommand(0, 6, "287aca0a-9de4-4cc1-9028-8471048eb545", 0);</script>'
OVERVIEW_HTML = '<a id="l8"><span>Geräteinfos</span></a>'
DEVICE_INFO_HTML = """\
<td class="aDICECName"><span>Software-Version:</span></td>
<td class="aDICECValue"><span>05.04.00.08</span></td>
<td class="aDICECName"><span>MAC-Adresse:</span></td>
<td class="aDICECValue"><span>AA:BB:CC:DD:EE:FF</span></td>
"""


def _request_data(request: Request) -> list[object]:
    values = parse_qs(urlparse(request.url).query).get("data", ["[]"])
    return json.loads(values[0])


def test_tks_info_helpers_find_link_and_parse_values() -> None:
    assert tks_web._find_link_id(OVERVIEW_HTML, "Geräteinfos") == "l8"
    assert tks_web._parse_device_info(DEVICE_INFO_HTML) == {
        "Software-Version": "05.04.00.08",
        "MAC-Adresse": "AA:BB:CC:DD:EE:FF",
    }


def test_tks_info_link_lookup_reports_missing_label() -> None:
    with pytest.raises(RuntimeError, match="not found"):
        tks_web._find_link_id(OVERVIEW_HTML, "Update")


@respx.mock
def test_tks_client_navigates_to_device_info_panel() -> None:
    respx.get(f"http://{HOST}:8080/state?callback=setState").mock(
        return_value=Response(200, text='setState({"system.state":"0"})'),
    )
    respx.get(f"http://{HOST}:8080/").mock(return_value=Response(200, text=ROOT_HTML))

    def response(request: Request) -> Response:
        if _request_data(request) == ["reload"]:
            return Response(200, json=[0, [0, 0, "body", [OVERVIEW_HTML], True]])
        return Response(200, json=[1, [0, 21, "#content", [DEVICE_INFO_HTML]]])

    route = respx.get(f"http://{HOST}:8080/json").mock(side_effect=response)

    info = TksWebClient(HOST).device_info()

    assert info["Software-Version"] == "05.04.00.08"
    assert _request_data(route.calls.last.request) == ["link", "l8"]
