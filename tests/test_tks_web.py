"""
Tests for tks_web.py — TKS-IP gateway on-demand web app (port 8080).
"""

import json
import stat
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import pytest

from pygira import tks_web
from pygira.tks_web import TksWebClient, _find_widget_id
from tests import _httpmock as respx
from tests._httpmock import Request, Response
from tests.fixtures import (
    TKS_DATE_TIME_HTML,
    TKS_DEVICE_INFO_HTML,
    TKS_LOGIN_HTML,
    TKS_NETWORK_HTML,
    TKS_OVERVIEW_HTML,
    TKS_ROOT_HTML,
    TKS_SIP_CALL_GROUP_ONE_HTML,
    TKS_SIP_CALL_GROUP_TWO_HTML,
    TKS_SIP_CALL_ONE_HTML,
    TKS_SIP_CALL_TWO_HTML,
    TKS_SIP_CLIENTS_HTML,
    TKS_SIP_INCOMING_HTML,
    TKS_SYSTEM_HTML,
)

HOST = "192.168.1.100"
SESSION_BOOTSTRAP_REQUESTS = 2
LOGIN_AND_REUSE_REQUESTS = 4
PRIVATE_FILE_MODE = 0o600


def _parse_data(url: str) -> list[object]:
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    data = qs.get("data")
    return json.loads(data[0]) if data else []


def _is_reload(url: str) -> bool:
    data = _parse_data(url)
    return bool(data) and data[0] == "reload"


def _body_response(html: str) -> Response:
    return Response(200, content=json.dumps([0, [0, 0, "body", [html], True]]).encode())


def _ack_response(n: int = 1) -> Response:
    return Response(200, content=json.dumps([n]).encode())


def _root_response() -> Response:
    return Response(200, content=TKS_ROOT_HTML.encode())


def _mock_state() -> respx.Route:
    return respx.get(f"http://{HOST}:8080/state?callback=setState").mock(
        return_value=Response(200, content=b'setState({"system.state":"0"})'),
    )


# ── _find_widget_id ───────────────────────────────────────────────────────────


def test_find_widget_id_finds_login_controls() -> None:
    assert _find_widget_id(TKS_LOGIN_HTML, "lLDCName") == "c61"
    assert _find_widget_id(TKS_LOGIN_HTML, "lLDCPassword") == "c62"
    assert _find_widget_id(TKS_LOGIN_HTML, "lLLoginButton") == "c66"


def test_find_widget_id_finds_system_buttons() -> None:
    assert _find_widget_id(TKS_SYSTEM_HTML, "aBSaveButton") == "c104"
    assert _find_widget_id(TKS_SYSTEM_HTML, "aBRestoreButton") == "c107"
    assert _find_widget_id(TKS_SYSTEM_HTML, "aUSUpdateButton") == "c114"


def test_find_widget_id_raises_when_class_missing() -> None:
    with pytest.raises(RuntimeError, match="not found"):
        _find_widget_id(TKS_SYSTEM_HTML, "noSuchClass")


def test_find_link_id_finds_overview_menu_link() -> None:
    assert tks_web._find_link_id(TKS_OVERVIEW_HTML, "Geräteinfos") == "l8"
    assert tks_web._find_link_id(TKS_OVERVIEW_HTML, "Update") == "l6"


def test_find_link_id_raises_when_label_missing() -> None:
    with pytest.raises(RuntimeError, match="not found"):
        tks_web._find_link_id(TKS_OVERVIEW_HTML, "Nonexistent Label")


def test_find_button_id_finds_overview_control_by_label() -> None:
    assert tks_web._find_button_id(TKS_DATE_TIME_HTML, "Übersicht") == "c2"


def test_find_assistant_action_id_finds_row_scoped_launch_button() -> None:
    assert tks_web._find_assistant_action_id(
        TKS_OVERVIEW_HTML,
        "IP-Telefone konfigurieren",
    ) == "c80"


def test_parse_device_info_pairs_names_with_values() -> None:
    info = tks_web._parse_device_info(TKS_DEVICE_INFO_HTML)
    assert info["Software-Version"] == "05.04.00.08"
    assert info["MAC-Adresse"] == "AA:BB:CC:DD:EE:FF"
    assert info["Busadresse"] == "0xEA81DF"


def test_collect_html_fragments_keeps_multi_fragment_content_commands() -> None:
    commands = [
        0,
        [0, 0, "#content", ["<section>first</section>", "<section>second</section>"], True],
        [8, 1, "#date", 2026, 7, 25, {"dayNames": ["Monday"]}],
    ]

    assert tks_web._collect_html_fragments(commands) == (
        "<section>first</section><section>second</section>"
    )


def test_parse_date_time_info_combines_dom_state_and_command_values() -> None:
    commands = [
        [26, 32, "#c158", "25.07.2026"],
        [26, 32, "#c160", "14"],
        [26, 32, "#c162", "30"],
    ]

    assert tks_web._parse_date_time_info(TKS_DATE_TIME_HTML, commands) == {
        "timezone": "Europe/Berlin",
        "automatic": True,
        "ntp_server": "time.example.test",
        "date": "25.07.2026",
        "time": "14:30",
    }


def test_parse_network_info_combines_dom_state_and_command_values() -> None:
    commands = [
        [26, 32, "#c206", "gateway-name"],
        [26, 32, "#c209", "192.0.2.10"],
        [26, 32, "#c211", "255.255.255.0"],
        [26, 32, "#c213", "192.0.2.53"],
        [26, 32, "#c216", "192.0.2.1"],
    ]

    assert tks_web._parse_network_info(TKS_NETWORK_HTML, commands) == {
        "gateway_id": "2",
        "network_name": "gateway-name",
        "dhcp": False,
        "ip_address": "192.0.2.10",
        "subnet_mask": "255.255.255.0",
        "nameserver": "192.0.2.53",
        "default_gateway": "192.0.2.1",
        "video_resolution": "VGA",
    }


def test_parse_sip_clients_discards_password_values() -> None:
    commands = [
        [25, 30, "#c104", "#e17"],
        [26, 32, "#c108", "Front desk"],
        [26, 32, "#c109", "Mobile"],
        [26, 32, "#c114", "sip-user"],
        [26, 32, "#c116", "do-not-return-this"],
        [26, 32, "#c119", "do-not-return-this"],
    ]

    info = tks_web._parse_sip_clients(TKS_SIP_CLIENTS_HTML, commands)

    assert info == {
        "clients": [
            {
                "name": "Front desk",
                "selected": False,
                "username": None,
                "password_configured": None,
            },
            {
                "name": "Mobile",
                "selected": True,
                "username": "sip-user",
                "password_configured": True,
            },
        ],
        "security_warning_acknowledged": True,
    }
    assert "do-not-return-this" not in repr(info)


def test_parse_sip_incoming_calls_preserves_group_assignments() -> None:
    commands = [
        [0, 21, "#c124", [TKS_SIP_CALL_GROUP_ONE_HTML]],
        [0, 21, "#c124", [TKS_SIP_CALL_GROUP_TWO_HTML]],
        [
            0,
            21,
            "#e19 > td.groupContentInternal > table",
            [TKS_SIP_CALL_ONE_HTML],
        ],
        [
            0,
            21,
            "#e21 > td.groupContentInternal > table",
            [TKS_SIP_CALL_TWO_HTML],
        ],
    ]

    assert tks_web._parse_sip_incoming_calls(commands) == [
        {
            "name": "Door station",
            "calls": [{"name": "Main entrance", "assigned": True}],
        },
        {
            "name": "Internal",
            "calls": [{"name": "Concierge", "assigned": False}],
        },
    ]


@respx.mock
def test_poll_bootstraps_state_before_opening_command_session() -> None:
    state = _mock_state()
    root = respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
    poll = respx.get(f"http://{HOST}:8080/json").mock(return_value=Response(200, json=[1]))

    result = TksWebClient(HOST).poll()

    assert result == [1]
    assert len(root.calls) == 1
    assert state.called
    assert poll.called
    assert "/state?" in respx.calls[0].request.url
    assert respx.calls[1].request.url == f"http://{HOST}:8080/"


@respx.mock
def test_connect_does_not_mistake_state_cookie_for_command_session() -> None:
    respx.get(f"http://{HOST}:8080/").mock(return_value=Response(200, content=b"<html></html>"))
    respx.get(f"http://{HOST}:8080/state?callback=setState").mock(
        return_value=Response(
            200,
            content=b'setState({"system.state":"0"})',
            headers={"Set-Cookie": "SID=cookie-session; Path=/"},
        ),
    )

    with pytest.raises(RuntimeError, match="could not find session id"):
        TksWebClient(HOST).poll()


@respx.mock
def test_connect_reports_existing_gateway_session() -> None:
    _mock_state()
    page = """
    <html>
      <div class="leCurrentUser">admin</div>
      <div class="leCurrentUserIP">192.168.1.254</div>
    </html>
    """
    respx.get(f"http://{HOST}:8080/").mock(return_value=Response(200, text=page))

    with pytest.raises(RuntimeError, match=r"already in use.*admin.*192\.168\.1\.254"):
        TksWebClient(HOST).poll()


@respx.mock
def test_login_reuses_persisted_authenticated_session(tmp_path: Path) -> None:
    session_path = tmp_path / "session.json"
    state = _mock_state()
    root = respx.get(f"http://{HOST}:8080/").mock(
        return_value=Response(
            200,
            content=TKS_ROOT_HTML.encode(),
            headers={"Set-Cookie": "SID=cookie-session; Path=/"},
        ),
    )
    json_route = respx.get(f"http://{HOST}:8080/json").mock(
        side_effect=[
            _body_response(TKS_LOGIN_HTML),
            _ack_response(),
            Response(200, json=[1, [0, 21, "#content", [TKS_OVERVIEW_HTML]]]),
            _body_response(TKS_OVERVIEW_HTML),
        ],
    )

    with patch("pygira.tks_web._default_session_cache_path", return_value=session_path):
        TksWebClient(HOST, persist_session=True).login("admin", "secret")
        TksWebClient(HOST, persist_session=True).login("ignored", "not-sent")

    assert len(state.calls) == 1
    assert len(root.calls) == 1
    assert len(json_route.calls) == LOGIN_AND_REUSE_REQUESTS
    assert _parse_data(json_route.calls[-1].request.url) == ["reload"]
    assert json_route.calls[-1].request.headers["Cookie"] == "SID=cookie-session"
    assert "ignored" not in repr([call.request.url for call in json_route.calls])
    assert stat.S_IMODE(session_path.stat().st_mode) == PRIVATE_FILE_MODE


@respx.mock
def test_poll_rejects_non_list_command_response() -> None:
    _mock_state()
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
    respx.get(f"http://{HOST}:8080/json").mock(return_value=Response(200, json={"bad": 1}))

    with pytest.raises(RuntimeError, match="non-list response"):
        TksWebClient(HOST).poll()


@respx.mock
def test_send_reconnects_once_on_session_closed_signal() -> None:
    """[0, [0, 18]] is CommandManager.sessionClosed (decoded from the live web
    app JS) — the client's fix mirrors the browser's own CommandManager.reloadPage()."""
    state = _mock_state()
    root = respx.get(f"http://{HOST}:8080/").mock(
        side_effect=[_root_response(), _root_response()],
    )
    responses = iter([Response(200, json=[0, [0, 18]]), _body_response(TKS_SYSTEM_HTML)])
    respx.get(f"http://{HOST}:8080/json").mock(side_effect=lambda request: next(responses))

    html = TksWebClient(HOST).reload()

    assert html == TKS_SYSTEM_HTML
    assert len(root.calls) == SESSION_BOOTSTRAP_REQUESTS
    assert len(state.calls) == SESSION_BOOTSTRAP_REQUESTS


@respx.mock
def test_send_raises_when_session_closed_repeats() -> None:
    _mock_state()
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
    respx.get(f"http://{HOST}:8080/json").mock(return_value=Response(200, json=[0, [0, 18]]))

    with pytest.raises(RuntimeError, match="session closed repeatedly"):
        TksWebClient(HOST).reload()


@respx.mock
def test_device_info_navigates_and_parses_panel() -> None:
    _mock_state()
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
    respx.get(f"http://{HOST}:8080/json").mock(
        side_effect=lambda request: (
            _body_response(TKS_OVERVIEW_HTML)
            if _is_reload(request.url)
            else Response(200, json=[1, [0, 21, "#c128", [TKS_DEVICE_INFO_HTML]]])
        ),
    )

    info = TksWebClient(HOST).device_info()

    assert info["Software-Version"] == "05.04.00.08"
    assert info["MAC-Adresse"] == "AA:BB:CC:DD:EE:FF"


@respx.mock
def test_device_info_waits_for_panel_from_command_poll() -> None:
    _mock_state()
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
    poll_count = {"n": 0}

    def json_side_effect(request: Request) -> Response:
        data = _parse_data(request.url)
        if data == ["reload"]:
            return _body_response(TKS_OVERVIEW_HTML)
        if data and data[0] == "link":
            return _ack_response()
        poll_count["n"] += 1
        return Response(200, json=[2, [0, 21, "#content", [TKS_DEVICE_INFO_HTML]]])

    respx.get(f"http://{HOST}:8080/json").mock(side_effect=json_side_effect)

    with patch("pygira.tks_web.time.sleep"):
        info = TksWebClient(HOST).device_info()

    assert info["Software-Version"] == "05.04.00.08"
    assert poll_count["n"] == 1


@respx.mock
def test_date_time_info_navigates_to_read_only_panel() -> None:
    client = TksWebClient(HOST)
    client._sid = "test-sid"
    client._navigation_html = TKS_OVERVIEW_HTML
    json_route = respx.get(f"http://{HOST}:8080/json").mock(
        return_value=Response(
            200,
            json=[
                1,
                [0, 21, "#content", [TKS_DATE_TIME_HTML]],
                [26, 32, "#c158", "25.07.2026"],
                [26, 32, "#c160", "14"],
                [26, 32, "#c162", "30"],
            ],
        ),
    )

    info = client.date_time_info()

    assert info["time"] == "14:30"
    assert _parse_data(json_route.calls.last.request.url) == ["link", "l10"]


@respx.mock
def test_network_info_navigates_to_read_only_panel() -> None:
    client = TksWebClient(HOST)
    client._sid = "test-sid"
    client._navigation_html = TKS_OVERVIEW_HTML
    json_route = respx.get(f"http://{HOST}:8080/json").mock(
        return_value=Response(
            200,
            json=[
                1,
                [0, 21, "#content", [TKS_NETWORK_HTML]],
                [26, 32, "#c206", "gateway-name"],
            ],
        ),
    )

    info = client.network_info()

    assert info["network_name"] == "gateway-name"
    assert _parse_data(json_route.calls.last.request.url) == ["link", "l2"]


@respx.mock
def test_sip_clients_launches_assistant_without_sending_configuration() -> None:
    client = TksWebClient(HOST)
    client._sid = "test-sid"
    client._navigation_html = TKS_OVERVIEW_HTML
    def json_side_effect(request: Request) -> Response:
        data = _parse_data(request.url)
        if data == ["click", "c80"]:
            return Response(
                200,
                json=[
                    1,
                    [0, 21, "#content", [TKS_SIP_CLIENTS_HTML]],
                    [25, 30, "#c104", "#e16"],
                    [26, 32, "#c108", "Front desk"],
                    [26, 32, "#c114", "sip-user"],
                    [26, 32, "#c116", "configured-password"],
                ],
            )
        return Response(
            200,
            json=[
                2,
                [0, 0, "#incoming", [TKS_SIP_INCOMING_HTML], True],
                [0, 21, "#c124", [TKS_SIP_CALL_GROUP_ONE_HTML]],
                [
                    0,
                    21,
                    "#e19 > td.groupContentInternal > table",
                    [TKS_SIP_CALL_ONE_HTML],
                ],
            ],
        )

    json_route = respx.get(f"http://{HOST}:8080/json").mock(side_effect=json_side_effect)

    info = client.sip_clients()

    assert info["clients"] == [
        {
            "name": "Front desk",
            "selected": True,
            "username": "sip-user",
            "password_configured": True,
        },
    ]
    assert info["incoming_calls"] == [
        {
            "name": "Door station",
            "calls": [{"name": "Main entrance", "assigned": True}],
        },
    ]
    assert [_parse_data(call.request.url) for call in json_route.calls] == [
        ["click", "c80"],
        ["value", "c110", "e46"],
    ]
    assert "configured-password" not in repr(info)


@respx.mock
def test_read_only_pages_return_to_overview_before_second_navigation() -> None:
    client = TksWebClient(HOST)
    client._sid = "test-sid"
    client._navigation_html = TKS_OVERVIEW_HTML

    def json_side_effect(request: Request) -> Response:
        data = _parse_data(request.url)
        if data == ["link", "l10"]:
            return Response(200, json=[1, [0, 21, "#content", [TKS_DATE_TIME_HTML]]])
        if data == ["click", "c2"]:
            return Response(200, json=[2, [0, 21, "#menu", [TKS_OVERVIEW_HTML]]])
        if data == ["link", "l2"]:
            return Response(200, json=[3, [0, 21, "#content", [TKS_NETWORK_HTML]]])
        return _ack_response()

    json_route = respx.get(f"http://{HOST}:8080/json").mock(side_effect=json_side_effect)

    client.date_time_info()
    client.network_info()

    assert [_parse_data(call.request.url) for call in json_route.calls] == [
        ["link", "l10"],
        ["click", "c2"],
        ["link", "l2"],
    ]


@respx.mock
def test_send_raises_on_invalid_site_id_without_retry() -> None:
    _mock_state()
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
    json_route = respx.get(f"http://{HOST}:8080/json").mock(
        return_value=Response(200, json=[0, [0, 19]]),
    )

    with pytest.raises(RuntimeError, match="invalidSiteID"):
        TksWebClient(HOST).reload()

    assert len(json_route.calls) == 1


@respx.mock
def test_expired_persisted_session_reconnects_once(tmp_path: Path) -> None:
    session_path = tmp_path / "session.json"
    with patch("pygira.tks_web._default_session_cache_path", return_value=session_path):
        previous = TksWebClient(HOST, persist_session=True)
        previous._sid = "expired-sid"
        previous._session_cookie = "expired-cookie"
        previous._persist_session()

        state = _mock_state()
        root = respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
        json_route = respx.get(f"http://{HOST}:8080/json").mock(
            side_effect=[
                Response(200, json=[0, [0, 19]]),
                _body_response(TKS_SYSTEM_HTML),
            ],
        )

        html = TksWebClient(HOST, persist_session=True).reload()

    assert html == TKS_SYSTEM_HTML
    assert state.called
    assert root.called
    assert len(json_route.calls) == SESSION_BOOTSTRAP_REQUESTS
    assert not session_path.exists()


@respx.mock
def test_expired_persisted_session_reconnects_after_http_404(tmp_path: Path) -> None:
    session_path = tmp_path / "session.json"
    with patch("pygira.tks_web._default_session_cache_path", return_value=session_path):
        previous = TksWebClient(HOST, persist_session=True)
        previous._sid = "expired-sid"
        previous._session_cookie = "expired-cookie"
        previous._persist_session()

        state = _mock_state()
        root = respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
        json_route = respx.get(f"http://{HOST}:8080/json").mock(
            side_effect=[
                Response(404, content=b""),
                _body_response(TKS_SYSTEM_HTML),
            ],
        )

        html = TksWebClient(HOST, persist_session=True).reload()

    assert html == TKS_SYSTEM_HTML
    assert state.called
    assert root.called
    assert len(json_route.calls) == SESSION_BOOTSTRAP_REQUESTS
    assert not session_path.exists()


# ── login ──────────────────────────────────────────────────────────────────


@respx.mock
def test_login_waits_for_authenticated_menu_from_command_poll() -> None:
    _mock_state()
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())

    reload_count = {"n": 0}
    poll_count = {"n": 0}

    def json_side_effect(request: Request) -> Response:
        if _is_reload(request.url):
            reload_count["n"] += 1
            return _body_response(TKS_LOGIN_HTML)
        if not _parse_data(request.url):
            poll_count["n"] += 1
            return Response(200, json=[4, [0, 21, "#menu", [TKS_OVERVIEW_HTML]]])
        return _ack_response()

    respx.get(f"http://{HOST}:8080/json").mock(side_effect=json_side_effect)

    with patch("pygira.tks_web.time.sleep"):
        client = TksWebClient(HOST)
        client.login("admin", "secret")

    assert reload_count["n"] == 1
    assert poll_count["n"] == 1
    assert client._navigation_html is not None


@respx.mock
def test_login_raises_when_authenticated_menu_never_arrives() -> None:
    _mock_state()
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
    respx.get(f"http://{HOST}:8080/json").mock(
        side_effect=lambda request: (
            _body_response(TKS_LOGIN_HTML) if _is_reload(request.url) else _ack_response()
        ),
    )

    with patch("pygira.tks_web.time.sleep"), pytest.raises(RuntimeError, match="login failed"):
        client = TksWebClient(HOST)
        client.login("admin", "wrong", timeout=0.05)


# ── backup / restore / update ─────────────────────────────────────────────────


@respx.mock
def test_backup_save_downloads_file() -> None:
    _mock_state()
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())

    def json_side_effect(request: Request) -> Response:
        data = _parse_data(request.url)
        if data == ["reload"]:
            return _body_response(TKS_OVERVIEW_HTML)
        if data and data[0] == "link":
            return Response(200, json=[1, [0, 21, "#content", [TKS_SYSTEM_HTML]]])
        return _ack_response()

    respx.get(f"http://{HOST}:8080/json").mock(side_effect=json_side_effect)
    respx.get(f"http://{HOST}:8080/files/backup.img").mock(
        return_value=Response(200, content=b"BACKUPDATA"),
    )

    with patch("pygira.tks_web.time.sleep"):
        client = TksWebClient(HOST)
        data = client.backup_save()

    assert data == b"BACKUPDATA"


@respx.mock
def test_backup_restore_uploads_then_clicks_restore_button() -> None:
    def json_side_effect(request: Request) -> Response:
        data = _parse_data(request.url)
        if data == ["reload"]:
            return _body_response(TKS_OVERVIEW_HTML)
        if data and data[0] == "link":
            return Response(200, json=[1, [0, 21, "#content", [TKS_SYSTEM_HTML]]])
        return _ack_response()

    respx.get(f"http://{HOST}:8080/json").mock(side_effect=json_side_effect)
    upload_route = respx.post(f"http://{HOST}:8080/upload?id=backup").mock(
        return_value=Response(200, content=b""),
    )

    client = TksWebClient(HOST)
    client._sid = "test-sid"  # skip the root-page fetch, not under test here
    client.backup_restore(b"BACKUPBYTES", "backup.img")

    assert upload_route.called
    assert b"BACKUPBYTES" in upload_route.calls.last.request.read()


@respx.mock
def test_firmware_update_uploads_then_clicks_update_button() -> None:
    def json_side_effect(request: Request) -> Response:
        data = _parse_data(request.url)
        if data == ["reload"]:
            return _body_response(TKS_OVERVIEW_HTML)
        if data and data[0] == "link":
            return Response(200, json=[1, [0, 21, "#content", [TKS_SYSTEM_HTML]]])
        return _ack_response()

    respx.get(f"http://{HOST}:8080/json").mock(side_effect=json_side_effect)
    upload_route = respx.post(f"http://{HOST}:8080/update").mock(
        return_value=Response(200, content=b""),
    )

    client = TksWebClient(HOST)
    client._sid = "test-sid"
    client.firmware_update(b"FIRMWAREBYTES", "firmware.bin")

    assert upload_route.called
    assert b"FIRMWAREBYTES" in upload_route.calls.last.request.read()


def test_multipart_body_contains_filename_and_data() -> None:
    body, content_type = tks_web._multipart_body("backup.img", b"HELLO")
    assert b'filename="backup.img"' in body
    assert b"HELLO" in body
    assert content_type.startswith("multipart/form-data; boundary=")
