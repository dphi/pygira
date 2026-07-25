"""
Tests for tks_web.py — TKS-IP gateway on-demand web app (port 8080).
"""

import json
import urllib.parse
from unittest.mock import patch

import pytest

from pygira import tks_web
from pygira.tks_web import TksWebClient, _find_widget_id
from tests import _httpmock as respx
from tests._httpmock import Request, Response
from tests.fixtures import (
    TKS_DEVICE_INFO_HTML,
    TKS_LOGIN_HTML,
    TKS_OVERVIEW_HTML,
    TKS_ROOT_HTML,
    TKS_SYSTEM_HTML,
)

HOST = "192.168.1.100"
SUCCESSFUL_LOGIN_RELOADS = 2
SESSION_BOOTSTRAP_REQUESTS = 2


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


def test_parse_device_info_pairs_names_with_values() -> None:
    info = tks_web._parse_device_info(TKS_DEVICE_INFO_HTML)
    assert info["Software-Version"] == "05.04.00.08"
    assert info["MAC-Adresse"] == "AA:BB:CC:DD:EE:FF"
    assert info["Busadresse"] == "0xEA81DF"


@respx.mock
def test_poll_bootstraps_state_cookie_when_running_app_has_no_session() -> None:
    root = respx.get(f"http://{HOST}:8080/").mock(
        side_effect=[Response(200, content=b"<html></html>"), _root_response()],
    )
    state = respx.get(f"http://{HOST}:8080/state?callback=setState").mock(
        return_value=Response(200, content=b'setState({"system.state":"0"})'),
    )
    poll = respx.get(f"http://{HOST}:8080/json").mock(return_value=Response(200, json=[1]))

    result = TksWebClient(HOST).poll()

    assert result == [1]
    assert len(root.calls) == SESSION_BOOTSTRAP_REQUESTS
    assert state.called
    assert poll.called


@respx.mock
def test_poll_uses_sid_cookie_from_state_bootstrap() -> None:
    root = respx.get(f"http://{HOST}:8080/").mock(
        return_value=Response(200, content=b"<html></html>"),
    )
    respx.get(f"http://{HOST}:8080/state?callback=setState").mock(
        return_value=Response(
            200,
            content=b'setState({"system.state":"0"})',
            headers={"Set-Cookie": "SID=cookie-session; Path=/"},
        ),
    )
    poll = respx.get(f"http://{HOST}:8080/json").mock(return_value=Response(200, json=[1]))

    with patch.object(tks_web.httpx.Client, "_cookie_value", return_value="cookie-session"):
        result = TksWebClient(HOST).poll()

    assert result == [1]
    assert len(root.calls) == 1
    assert "sid=cookie-session" in poll.calls.last.request.url


@respx.mock
def test_poll_rejects_non_list_command_response() -> None:
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
    respx.get(f"http://{HOST}:8080/json").mock(return_value=Response(200, json={"bad": 1}))

    with pytest.raises(RuntimeError, match="non-list response"):
        TksWebClient(HOST).poll()


@respx.mock
def test_send_reconnects_once_on_session_closed_signal() -> None:
    """[0, [0, 18]] is CommandManager.sessionClosed (decoded from the live web
    app JS) — the client's fix mirrors the browser's own CommandManager.reloadPage()."""
    root = respx.get(f"http://{HOST}:8080/").mock(
        side_effect=[_root_response(), _root_response()],
    )
    responses = iter([Response(200, json=[0, [0, 18]]), _body_response(TKS_SYSTEM_HTML)])
    respx.get(f"http://{HOST}:8080/json").mock(side_effect=lambda request: next(responses))

    html = TksWebClient(HOST).reload()

    assert html == TKS_SYSTEM_HTML
    assert len(root.calls) == SESSION_BOOTSTRAP_REQUESTS


@respx.mock
def test_send_raises_when_session_closed_repeats() -> None:
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
    respx.get(f"http://{HOST}:8080/json").mock(return_value=Response(200, json=[0, [0, 18]]))

    with pytest.raises(RuntimeError, match="session closed repeatedly"):
        TksWebClient(HOST).reload()


@respx.mock
def test_device_info_navigates_and_parses_panel() -> None:
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
def test_send_raises_on_invalid_site_id_without_retry() -> None:
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
    json_route = respx.get(f"http://{HOST}:8080/json").mock(
        return_value=Response(200, json=[0, [0, 19]]),
    )

    with pytest.raises(RuntimeError, match="invalidSiteID"):
        TksWebClient(HOST).reload()

    assert len(json_route.calls) == 1


# ── login ──────────────────────────────────────────────────────────────────


@respx.mock
def test_login_succeeds_once_login_page_is_gone() -> None:
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())

    reload_count = {"n": 0}

    def json_side_effect(request: Request) -> Response:
        if _is_reload(request.url):
            reload_count["n"] += 1
            html = TKS_LOGIN_HTML if reload_count["n"] == 1 else TKS_SYSTEM_HTML
            return _body_response(html)
        return _ack_response()

    respx.get(f"http://{HOST}:8080/json").mock(side_effect=json_side_effect)

    with patch("pygira.tks_web.time.sleep"):
        client = TksWebClient(HOST)
        client.login("admin", "secret")

    assert reload_count["n"] == SUCCESSFUL_LOGIN_RELOADS


@respx.mock
def test_login_raises_when_still_on_login_page() -> None:
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
    respx.get(f"http://{HOST}:8080/").mock(return_value=_root_response())
    respx.get(f"http://{HOST}:8080/json").mock(
        side_effect=lambda request: (
            _body_response(TKS_SYSTEM_HTML) if _is_reload(request.url) else _ack_response()
        ),
    )
    respx.get(f"http://{HOST}:8080/files/backup.img").mock(
        return_value=Response(200, content=b"BACKUPDATA"),
    )

    with patch("pygira.tks_web.time.sleep"):
        client = TksWebClient(HOST)
        data = client.backup_save()

    assert data == b"BACKUPDATA"


@respx.mock
def test_backup_restore_uploads_then_clicks_restore_button() -> None:
    respx.get(f"http://{HOST}:8080/json").mock(
        side_effect=lambda request: (
            _body_response(TKS_SYSTEM_HTML) if _is_reload(request.url) else _ack_response()
        ),
    )
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
    respx.get(f"http://{HOST}:8080/json").mock(
        side_effect=lambda request: (
            _body_response(TKS_SYSTEM_HTML) if _is_reload(request.url) else _ack_response()
        ),
    )
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
