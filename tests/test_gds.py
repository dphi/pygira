"""
Tests for gds.py — GDS WebSocket client (port 4432).

Auth: "basic <b64>" header during WebSocket upgrade (lowercase per layout.js).
All requests: {"request": {"command": "...", ...}}
All responses: {"response": {"request": {"command": "..."}, ...}}

Key app names and keys from firmware (layout.js globals):
  - Weather: appName="Gira.G1", key="weather.settings"   (LegacyAppValueAppName)
  - DCS/TKS: appName="Gira.UniversalApp", key="dcs.settings"  (ApplicationName)

DCS channel URNs from g1_device.xml (DcsVHsGUI.Connection, StartId=501010):
  - Channel: urn:gds:chn:<deviceId>:DcsVHsGUI.Connection
  - Connect datapoint: urn:gds:dp:<deviceId>:DcsVHsGUI.Connection:Connect
"""

import base64
import json
import ssl
from contextlib import AbstractContextManager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pygira.devices.g1 import G1
from pygira.exceptions import ProtocolError, TransportError
from pygira.gds import GdsClient, _make_url
from tests.fixtures import (
    gds_process_view_response,
    gds_register_response,
    gds_set_app_value_response,
    gds_set_configuration_response,
    gds_set_value_response,
)

DEVICE_ID = "device-001"


# ── auth header ───────────────────────────────────────────────────────────────


def test_gds_url_uses_lowercase_ui_token() -> None:
    url = _make_url("192.168.1.100", "admin", "secret")
    assert url.startswith("wss://192.168.1.100:4432/gds/api?ui")
    payload = base64.b64decode(url.split("?ui", 1)[1]).decode()
    assert payload == "admin:secret"


def test_gds_tls_can_use_system_verification_or_custom_context() -> None:
    verified = GdsClient("192.0.2.1", "device", "secret", verify_tls=True)
    assert verified.ssl_context.check_hostname
    assert verified.ssl_context.verify_mode == ssl.CERT_REQUIRED

    custom = ssl.create_default_context()
    assert GdsClient("192.0.2.1", "device", "secret", ssl_context=custom).ssl_context is custom


@pytest.mark.asyncio
async def test_gds_async_context_manager_closes_connection() -> None:
    ws = make_ws_mock(gds_register_response())
    with ws_connect_patch(ws):
        async with GdsClient("192.0.2.1", "device", "secret"):
            pass
    ws.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_gds_connection_error_redacts_credentials() -> None:
    async def fail(url: str, **kwargs: object) -> None:
        raise RuntimeError(url)

    with patch("pygira.gds.websockets.connect", fail):
        client = GdsClient("192.0.2.1", "device", "top-secret")
        with pytest.raises(TransportError) as exc_info:
            await client.connect()

    assert "top-secret" not in str(exc_info.value)
    assert "<token>" in str(exc_info.value)


def test_gds_url_not_capital_basic() -> None:
    url = _make_url("192.168.1.100", "admin", "secret")
    assert "?Basic" not in url


# ── message format helpers ────────────────────────────────────────────────────


def make_ws_mock(*responses: dict[str, object]) -> MagicMock:
    """Return a mock websocket that yields responses in order."""
    ws = MagicMock()
    json_responses = [json.dumps(r) for r in responses]
    ws.recv = AsyncMock(side_effect=json_responses)
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


def ws_connect_patch(ws: MagicMock) -> AbstractContextManager[object]:
    """
    Patch target for websockets.connect.
    websockets v16 uses an awaitable class; patching needs to return a
    coroutine so `await websockets.connect(...)` resolves to `ws`.
    """

    async def _factory(*args: object, **kwargs: object) -> MagicMock:
        return ws

    return patch("pygira.gds.websockets.connect", _factory)


def extract_sent_request(ws_mock: MagicMock, call_index: int = 0) -> dict[str, object]:
    """Get the parsed request from the nth send() call."""
    raw = ws_mock.send.call_args_list[call_index].args[0]
    return json.loads(raw)["request"]


def gds_error_response(command: str, **request_fields: object) -> dict[str, object]:
    return {
        "response": {
            "request": {"command": command, **request_fields},
            "error": {"code": "103", "text": "Forbidden", "hint": "read-only"},
        },
    }


# ── RegisterApplication ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_application_message_format() -> None:
    """
    RegisterApplication must include applicationId, applicationType, and instanceId.
    From layout.js __registrationMessage:
      {command:"RegisterApplication", applicationId:e, applicationType:"ui",
       pretty:"true", instanceId:<uuid>}
    """
    ws = make_ws_mock(gds_register_response())

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()

    reg = extract_sent_request(ws, 0)
    assert reg["command"] == "RegisterApplication"
    assert reg["applicationId"] == "Gira.UniversalApp"
    assert reg["applicationType"] == "ui"
    assert "instanceId" in reg


# ── SetAppValue ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_app_value_message_format() -> None:
    """SetAppValue must include appName, key, value."""
    ws = make_ws_mock(
        gds_register_response(),
        gds_set_app_value_response("Gira.G1", "weather.settings"),
    )

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        await client.set_app_value("Gira.G1", "weather.settings", '{"test":1}')

    req = extract_sent_request(ws, 1)
    assert req["command"] == "SetAppValue"
    assert req["appName"] == "Gira.G1"
    assert req["key"] == "weather.settings"
    assert req["value"] == '{"test":1}'


@pytest.mark.asyncio
async def test_weather_app_name_and_key() -> None:
    """
    Weather uses appName="Gira.G1" (LegacyAppValueAppName) and key="weather.settings".
    Confirmed in weather/js/weather.settings.js module.
    """
    ws = make_ws_mock(
        gds_register_response(),
        gds_set_app_value_response("Gira.G1", "weather.settings"),
    )

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        await client.set_app_value("Gira.G1", "weather.settings", "{}")

    req = extract_sent_request(ws, 1)
    assert req["appName"] == "Gira.G1"
    assert req["key"] == "weather.settings"


@pytest.mark.asyncio
async def test_dcs_settings_app_name_and_key() -> None:
    """
    TKS-IP settings use appName="Gira.UniversalApp" (ApplicationName) and key="dcs.settings".
    Confirmed in dcs/js/dcs.settings.js module.
    """
    ws = make_ws_mock(
        gds_register_response(),
        gds_set_app_value_response("Gira.UniversalApp", "dcs.settings"),
    )

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        await client.set_app_value(
            "Gira.UniversalApp",
            "dcs.settings",
            '{"ip":"10.0.0.1","username":"u","password":"p"}',
        )

    req = extract_sent_request(ws, 1)
    assert req["appName"] == "Gira.UniversalApp"
    assert req["key"] == "dcs.settings"


@pytest.mark.asyncio
async def test_dcs_settings_value_json_structure() -> None:
    """
    TKS-IP AppValue JSON must have ip, username, password keys.
    From dcs/js/dcs.settings.js: JSON.parse(n.value):{ip:"",username:"",password:""}
    """
    ws = make_ws_mock(
        gds_register_response(),
        gds_set_app_value_response("Gira.UniversalApp", "dcs.settings"),
    )

    tks_ip, tks_user, tks_pass = "10.0.0.1", "user1", "pass1"

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        value = json.dumps({"ip": tks_ip, "username": tks_user, "password": tks_pass})
        await client.set_app_value("Gira.UniversalApp", "dcs.settings", value)

    req = extract_sent_request(ws, 1)
    value_payload = req["value"]
    assert isinstance(value_payload, str)
    parsed = json.loads(value_payload)
    assert parsed["ip"] == tks_ip
    assert parsed["username"] == tks_user
    assert parsed["password"] == tks_pass


# ── SetConfiguration ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_configuration_message_format() -> None:
    """
    SetConfiguration for DCS from layout.js:
      {command:"SetConfiguration", object:{urn:<connectChannelUrn>,
       metadata:[{key:"IpAddress",...},{key:"Username",...},{key:"Password",...}],
       pretty:"true"}}
    """
    urn = f"urn:gds:chn:{DEVICE_ID}:DcsVHsGUI.Connection"
    ws = make_ws_mock(
        gds_register_response(),
        gds_set_configuration_response(urn),
    )

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        await client.set_configuration(
            urn,
            {
                "IpAddress": "10.0.0.1",
                "Username": "user1",
                "Password": "pass1",
            },
        )

    req = extract_sent_request(ws, 1)
    assert req["command"] == "SetConfiguration"
    request_object = req["object"]
    assert isinstance(request_object, dict)
    assert request_object["urn"] == urn
    raw_metadata = request_object["metadata"]
    assert isinstance(raw_metadata, list)
    metadata = {}
    for item in raw_metadata:
        assert isinstance(item, dict)
        metadata[item["key"]] = item["value"]
    assert metadata["IpAddress"] == "10.0.0.1"
    assert metadata["Username"] == "user1"
    assert metadata["Password"] == "pass1"


# ── SetValue ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_value_message_format() -> None:
    """SetValue used for DCS connect/disconnect trigger."""
    connect_urn = f"urn:gds:dp:{DEVICE_ID}:DcsVHsGUI.Connection:Connect"
    ws = make_ws_mock(
        gds_register_response(),
        gds_set_value_response(connect_urn),
    )

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        await client.set_value(connect_urn, "0")

    req = extract_sent_request(ws, 1)
    assert req["command"] == "SetValue"
    assert req["urn"] == connect_urn
    assert req["value"] == "0"


# ── Restart / FactoryReset ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_app_value_raises_on_gds_error() -> None:
    ws = make_ws_mock(
        gds_register_response(),
        gds_error_response("SetAppValue", appName="Gira.G1", key="weather.settings"),
    )

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        with pytest.raises(ProtocolError, match="SetAppValue failed"):
            await client.set_app_value("Gira.G1", "weather.settings", "{}")


@pytest.mark.asyncio
async def test_set_configuration_raises_on_gds_error() -> None:
    urn = f"urn:gds:chn:{DEVICE_ID}:DcsVHsGUI.Connection"
    ws = make_ws_mock(
        gds_register_response(),
        gds_error_response("SetConfiguration", object={"urn": urn}),
    )

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        with pytest.raises(ProtocolError, match="SetConfiguration failed"):
            await client.set_configuration(urn, {"IpAddress": "10.0.0.1"})


@pytest.mark.asyncio
async def test_set_value_raises_on_gds_error() -> None:
    urn = f"urn:gds:dp:{DEVICE_ID}:DcsVHsGUI.Connection:Connect"
    ws = make_ws_mock(
        gds_register_response(),
        gds_error_response("SetValue", urn=urn),
    )

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        with pytest.raises(ProtocolError, match="SetValue failed"):
            await client.set_value(urn, "0")


@pytest.mark.asyncio
async def test_restart_sends_restart_command() -> None:
    ws = make_ws_mock(gds_register_response())
    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        await client.restart()

    req = extract_sent_request(ws, 1)
    assert req["command"] == "Restart"
    assert req["type"] == "Device"


@pytest.mark.asyncio
async def test_factory_reset_sends_restart_with_type() -> None:
    """
    Factory reset confirmed from facrst binary:
      dscsampleapp -r='{"command":"Restart","type":"FactoryReset"}'
    """
    ws = make_ws_mock(gds_register_response())
    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        await client.factory_reset()

    req = extract_sent_request(ws, 1)
    assert req["command"] == "Restart"
    assert req["type"] == "FactoryReset"


@pytest.mark.asyncio
async def test_get_tks_status_extracts_state_datapoint() -> None:
    # Uses fixed IDs: 500003=ConnectionState, 500004=DisconnectReason
    def _gv(dp_id: str, value: str) -> dict[str, object]:
        return {
            "response": {
                "request": {"command": "GetValue", "id": dp_id},
                "error": {"code": "0"},
                "value": value,
            },
        }

    ws = make_ws_mock(gds_register_response(), _gv("500003", "3"), _gv("500004", "0"))

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        result = await client.get_tks_status()

    assert result["present"] is True
    assert result["state"] == "registered"
    assert result["disconnect_reason"] is None


@pytest.mark.asyncio
async def test_get_tks_status_does_not_return_unrelated_process_view_values() -> None:
    def _gv(dp_id: str, value: str) -> dict[str, object]:
        return {
            "response": {
                "request": {"command": "GetValue", "id": dp_id},
                "error": {"code": "0"},
                "value": value,
            },
        }

    ws = make_ws_mock(gds_register_response(), _gv("500003", "1"), _gv("500004", "0"))

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        result = await client.get_tks_status()

    assert result == {"present": True, "state": "unregistered", "disconnect_reason": None}


@pytest.mark.asyncio
async def test_get_tks_status_returns_absence_when_channel_missing() -> None:
    ws = make_ws_mock(
        gds_register_response(),
        {
            "response": {
                "request": {"command": "GetValue", "id": "500003"},
                "error": {"code": "111", "text": "Not found"},
            },
        },
    )

    with ws_connect_patch(ws):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        result = await client.get_tks_status()

    assert result == {"present": False, "state": None, "disconnect_reason": None}


def test_g1_tks_status_wrapper_delegates_to_gds(monkeypatch: pytest.MonkeyPatch) -> None:
    g1 = G1("192.168.1.100")
    sentinel = {"present": True, "value": "1"}
    monkeypatch.setattr(g1, "_gds", lambda coro: sentinel)

    assert g1.tks_status() is sentinel


# ── configure_tks ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_configure_tks_sends_all_steps() -> None:
    """
    configure_tks must:
    1. SetConfiguration on the fixed G1 channel URN
    2. SetValue(id=500002, "0") — disconnect
    3. SetValue(id=500002, "1") — reconnect (after 2s sleep)
    No GetProcessView needed — uses fixed IDs from g1_device.xml.
    """
    channel_urn = "urn:gds:chn:GIG1LXKXIP:Connect"

    def _sv(value: str) -> dict[str, object]:
        return {
            "response": {
                "request": {"command": "SetValue", "id": "500002", "value": value},
                "error": {"code": "0"},
            },
        }

    ws = make_ws_mock(
        gds_register_response(),
        gds_set_configuration_response(channel_urn),
        _sv("0"),
        _sv("1"),
    )

    with ws_connect_patch(ws), patch("pygira.gds.asyncio.sleep", new_callable=AsyncMock):
        client = GdsClient("192.168.1.100", "admin", "secret")
        await client.connect()
        await client.configure_tks("10.0.0.1", "user1", "pass1")

    commands = [json.loads(call.args[0])["request"]["command"] for call in ws.send.call_args_list]
    assert commands[0] == "RegisterApplication"
    assert commands[1] == "SetConfiguration"
    set_config_req = json.loads(ws.send.call_args_list[1].args[0])["request"]
    assert set_config_req["object"]["urn"] == channel_urn
    assert commands[2] == "SetValue"
    assert commands[3] == "SetValue"

    disconnect_req = json.loads(ws.send.call_args_list[2].args[0])["request"]
    reconnect_req = json.loads(ws.send.call_args_list[3].args[0])["request"]
    assert disconnect_req["value"] == "0"
    assert reconnect_req["value"] == "1"
    assert disconnect_req["id"] == "500002"
    assert reconnect_req["id"] == "500002"


# ── URN discovery ─────────────────────────────────────────────────────────────


def test_find_urn_locates_connect_channel() -> None:
    """_find_urn must traverse nested process view structure."""
    client = GdsClient("192.168.1.100", "admin", "secret")
    pv = gds_process_view_response(DEVICE_ID)
    urn = client._find_urn(pv, "DcsVHsGUI.Connection:Connect")
    assert urn == f"urn:gds:dp:{DEVICE_ID}:DcsVHsGUI.Connection:Connect"


def test_find_urn_locates_channel_without_datapoint() -> None:
    client = GdsClient("192.168.1.100", "admin", "secret")
    pv = gds_process_view_response(DEVICE_ID)
    urn = client._find_urn(pv, "DcsVHsGUI.Connection")
    assert urn is not None
    assert "DcsVHsGUI.Connection" in urn


def test_find_urn_returns_none_when_not_found() -> None:
    client = GdsClient("192.168.1.100", "admin", "secret")
    pv = gds_process_view_response(DEVICE_ID)
    assert client._find_urn(pv, "NonExistentChannel") is None
