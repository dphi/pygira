"""
Tests for api.py — iscwebservice HTTP client (port 80).

Auth: "Basic <b64>" (capital B, per iscwebservice binary strings).
Endpoints from iscwebservice strings analysis:
  POST /api          — JSON command dispatch
  POST /api/upload/v2 — firmware ZIP upload (nginx rewrites to /api/upload, passes X-File-Name)
  GET  /api/commissioningtest — commissioning test
  GET  /api/progmode / /api/progmode_off — KNX programming mode

SSH service control: controlService command with service "S50sshd"
  - "enable"  : touches /opt/userdata/.ssh-enabled (persistent)
  - "start"   : starts sshd
  - "start-once" : starts sshd without persistence
  - "stop"/"disable" : remove marker + stop sshd
"""

import base64
import json
from pathlib import Path

import pytest

from pygira.api import ApiClient
from pygira.api import _auth_header as api_auth
from pygira.config_service import _auth_header as cs_auth
from pygira.exceptions import AuthenticationError, DeviceApiError
from pygira.models import NetworkConfig
from tests import _httpmock as respx
from tests._httpmock import Response
from tests.fixtures import (
    COMMISSIONING_TEST_RESPONSE,
    CONTROL_SERVICE_OK,
    FIRMWARE_INFO_ONLINE_NONE,
    FIRMWARE_INFO_ONLINE_RESPONSE,
    FIRMWARE_PROGRESS_RESPONSE,
)

HOST = "192.168.1.100"
USER = "admin"
PASS = "secret"

EXPECTED_AUTH = "Basic " + base64.b64encode(b"admin:secret").decode()
SESSION_AUTH_CALL_COUNT = 4
EXPECTED_MODEL_CALL_COUNT = 3
EXPECTED_PROGRESS = 25


# ── auth header ───────────────────────────────────────────────────────────────


def test_api_auth_header_is_capital_basic() -> None:
    """iscwebservice uses capital 'Basic' (confirmed in binary strings)."""
    client = ApiClient(HOST, USER, PASS)
    assert client._headers["Authorization"].startswith("Basic ")


def test_api_auth_differs_from_configservice() -> None:
    """config_service.py uses lowercase 'basic'; api.py uses capital 'Basic'."""
    assert cs_auth(USER, PASS).startswith("basic ")
    assert api_auth(USER, PASS).startswith("Basic ")


# ── firmware info ─────────────────────────────────────────────────────────────


@respx.mock
def test_check_online_update_sends_infoonline_command() -> None:
    route = respx.post(f"http://{HOST}/api").mock(
        return_value=Response(200, json=FIRMWARE_INFO_ONLINE_RESPONSE),
    )
    client = ApiClient(HOST, USER, PASS)
    result = client.check_online_update()

    assert route.called
    body = route.calls.last.request.read()

    assert json.loads(body)["command"] == "infoonline"
    assert result["state"] == "available"


@respx.mock
def test_check_online_update_up_to_date() -> None:
    respx.post(f"http://{HOST}/api").mock(
        return_value=Response(200, json=FIRMWARE_INFO_ONLINE_NONE),
    )
    result = ApiClient(HOST, USER, PASS).check_online_update()
    assert result["state"] == "upToDate"


@respx.mock
def test_trigger_online_update_sends_startonlineupdate() -> None:
    route = respx.post(f"http://{HOST}/api").mock(
        return_value=Response(200, json={"state": "started"}),
    )
    ApiClient(HOST, USER, PASS).trigger_online_update()

    assert json.loads(route.calls.last.request.read())["command"] == "startonlineupdate"


@respx.mock
def test_get_upgrade_progress_sends_progress_command() -> None:
    route = respx.post(f"http://{HOST}/api").mock(
        return_value=Response(200, json=FIRMWARE_PROGRESS_RESPONSE),
    )
    result = ApiClient(HOST, USER, PASS).get_upgrade_progress()

    assert json.loads(route.calls.last.request.read())["command"] == "progress"
    assert result["state"] == "done"


@respx.mock
def test_get_firmware_status_sends_get_firmware_status_command() -> None:
    route = respx.post(f"http://{HOST}/webservice").mock(
        return_value=Response(
            200,
            json={"data": {"currentVersion": "2.8.874.0", "isUpdating": False}},
        ),
    )
    result = ApiClient(HOST, USER, PASS, api_prefix="/webservice").get_firmware_status()

    assert json.loads(route.calls.last.request.read())["command"] == "getFirmwareStatus"
    assert result["data"]["currentVersion"] == "2.8.874.0"


@respx.mock
def test_get_logfile_decodes_base64_content() -> None:
    content = base64.b64encode(b"PK\x03\x04g1-log").decode()
    route = respx.post(f"http://{HOST}/api").mock(
        return_value=Response(200, json={"data": {"content": content, "filename": "logfiles.zip"}}),
    )
    payload = ApiClient(HOST, USER, PASS).get_logfile()

    assert json.loads(route.calls.last.request.read())["command"] == "getLogfile"
    assert payload == b"PK\x03\x04g1-log"


@respx.mock
def test_get_device_info_force_long_wraps_data_field() -> None:
    route = respx.post(f"http://{HOST}/api").mock(
        return_value=Response(200, json={"data": {"k": "v"}}),
    )
    ApiClient(HOST, USER, PASS).get_device_info(force_long=True)

    body = json.loads(route.calls.last.request.read())
    assert body == {"command": "getDeviceInfo", "data": {"forceLong": True}}


@respx.mock
def test_get_diagnostic_page_uses_completely_flag() -> None:
    route = respx.post(f"http://{HOST}/api").mock(return_value=Response(200, json={"data": {}}))
    ApiClient(HOST, USER, PASS).get_diagnostic_page(completely=True)

    body = json.loads(route.calls.last.request.read())
    assert body == {"command": "getDiagnosticPage", "data": {"completely": True}}


@respx.mock
def test_set_ntp_config_payload_shape() -> None:
    route = respx.post(f"http://{HOST}/api").mock(return_value=Response(200, json={}))
    ApiClient(HOST, USER, PASS).set_ntp_config(
        enabled=True,
        server="0.europe.pool.ntp.org",
        interval_minutes=10,
    )

    body = json.loads(route.calls.last.request.read())
    assert body == {
        "command": "setNtpConfig",
        "data": {
            "Ntp": True,
            "NtpServerAddress": "0.europe.pool.ntp.org",
            "NtpInterval": "10",
        },
    }


@respx.mock
def test_set_ip_config_static_payload_shape() -> None:
    route = respx.post(f"http://{HOST}/api").mock(return_value=Response(200, json={}))
    ApiClient(HOST, USER, PASS).set_ip_config(
        NetworkConfig(
            dhcp=False,
            ip_address="10.0.0.50",
            subnet_mask="255.255.0.0",
            default_gateway="10.0.0.1",
            primary_dns="1.1.1.1",
            secondary_dns="1.0.0.1",
        ),
    )

    body = json.loads(route.calls.last.request.read())
    assert body == {
        "command": "setIpConfig",
        "data": {
            "Dhcp": False,
            "IpAddress": "10.0.0.50",
            "SubnetMask": "255.255.0.0",
            "DefaultGateway": "10.0.0.1",
            "NameServer": "1.1.1.1",
            "SecondaryDns": "1.0.0.1",
        },
    }


@respx.mock
def test_set_ip_config_dhcp_payload_shape() -> None:
    route = respx.post(f"http://{HOST}/api").mock(return_value=Response(200, json={}))
    ApiClient(HOST, USER, PASS).set_ip_config(NetworkConfig(dhcp=True))

    body = json.loads(route.calls.last.request.read())
    assert body == {"command": "setIpConfig", "data": {"Dhcp": True}}


# ── firmware upload ───────────────────────────────────────────────────────────


@respx.mock
def test_upload_firmware_posts_to_upload_v2(tmp_path: Path) -> None:
    """Firmware upload must POST to /api/upload/v2 (nginx proxies to /api/upload)."""
    fw_file = tmp_path / "firmware.zip"
    fw_file.write_bytes(b"PK\x03\x04fake-zip")

    route = respx.post(f"http://{HOST}/api/upload/v2").mock(return_value=Response(200))
    ApiClient(HOST, USER, PASS).upload_firmware(fw_file)
    assert route.called


@respx.mock
def test_upload_firmware_uses_zip_content_type(tmp_path: Path) -> None:
    fw_file = tmp_path / "firmware.zip"
    fw_file.write_bytes(b"PK\x03\x04fake")

    route = respx.post(f"http://{HOST}/api/upload/v2").mock(return_value=Response(200))
    ApiClient(HOST, USER, PASS).upload_firmware(fw_file)
    assert "zip" in route.calls.last.request.headers.get("Content-Type", "")


@respx.mock
def test_initiate_local_install_sends_initlocalupload() -> None:
    route = respx.post(f"http://{HOST}/api").mock(return_value=Response(200, json={"state": "ok"}))
    ApiClient(HOST, USER, PASS).initiate_local_install()

    assert json.loads(route.calls.last.request.read())["command"] == "initlocalupload"


# ── SSH enable / disable ──────────────────────────────────────────────────────


@respx.mock
def test_enable_ssh_persistent_sends_enable_then_start() -> None:
    """
    Persistent SSH enable must:
    1. controlService S50sshd enable  (touches /opt/userdata/.ssh-enabled)
    2. controlService S50sshd start
    Confirmed: S50sshd init script has enable/start actions; SSH_ENABLED file gates startup.
    """
    route = respx.post(f"http://{HOST}/api").mock(
        return_value=Response(200, json=CONTROL_SERVICE_OK),
    )
    ApiClient(HOST, USER, PASS).enable_ssh(persistent=True)

    calls = [json.loads(c.request.read()) for c in route.calls]
    assert calls[0] == {"command": "controlService", "service": "S50sshd", "control": "enable"}
    assert calls[1] == {"command": "controlService", "service": "S50sshd", "control": "start"}


@respx.mock
def test_enable_ssh_oneshot_sends_start_once() -> None:
    """
    One-shot SSH (start-once) starts sshd without writing the persistence marker.
    This is the S50sshd 'start-once' action which calls start() unconditionally.
    """
    route = respx.post(f"http://{HOST}/api").mock(
        return_value=Response(200, json=CONTROL_SERVICE_OK),
    )
    ApiClient(HOST, USER, PASS).enable_ssh(persistent=False)

    calls = [json.loads(c.request.read()) for c in route.calls]
    assert len(calls) == 1
    assert calls[0] == {"command": "controlService", "service": "S50sshd", "control": "start-once"}


@respx.mock
def test_disable_ssh_sends_stop_then_disable() -> None:
    """Disable must stop the running daemon and remove the .ssh-enabled marker."""
    route = respx.post(f"http://{HOST}/api").mock(
        return_value=Response(200, json=CONTROL_SERVICE_OK),
    )
    ApiClient(HOST, USER, PASS).disable_ssh()

    calls = [json.loads(c.request.read()) for c in route.calls]
    assert calls[0] == {"command": "controlService", "service": "S50sshd", "control": "stop"}
    assert calls[1] == {"command": "controlService", "service": "S50sshd", "control": "disable"}


# ── commissioning test ────────────────────────────────────────────────────────


@respx.mock
def test_commissioning_test_uses_get() -> None:
    """
    /api/commissioningtest is a GET endpoint (confirmed: 'Received commissioningtest GET request').
    """
    route = respx.get(f"http://{HOST}/api/commissioningtest").mock(
        return_value=Response(200, json=COMMISSIONING_TEST_RESPONSE),
    )
    result = ApiClient(HOST, USER, PASS).commissioning_test()
    assert route.called
    assert result["state"] == "ok"


@respx.mock
def test_commissioning_test_sends_auth() -> None:
    respx.get(f"http://{HOST}/api/commissioningtest").mock(
        return_value=Response(200, json=COMMISSIONING_TEST_RESPONSE),
    )
    ApiClient(HOST, USER, PASS).commissioning_test()
    req = respx.calls.last.request
    assert req.headers["Authorization"] == EXPECTED_AUTH


# ── control_service generic ───────────────────────────────────────────────────


@respx.mock
def test_control_service_sends_correct_payload() -> None:
    route = respx.post(f"http://{HOST}/api").mock(
        return_value=Response(200, json=CONTROL_SERVICE_OK),
    )
    ApiClient(HOST, USER, PASS).control_service("some-service", "restart")

    body = json.loads(route.calls.last.request.read())
    assert body == {"command": "controlService", "service": "some-service", "control": "restart"}


@respx.mock
def test_api_client_respects_custom_api_prefix() -> None:
    route = respx.post(f"http://{HOST}/webservice").mock(
        return_value=Response(200, json={"state": "ok"}),
    )
    ApiClient(HOST, USER, PASS, api_prefix="/webservice").check_online_update()
    assert route.called


@respx.mock
def test_api_client_session_fallback_on_auth_error() -> None:
    route = respx.post(f"http://{HOST}/webservice").mock(
        side_effect=[
            Response(200, json={"error": "ERR_COMMUNICATION", "id": "235"}),
            Response(200, json={"data": {"salt": "A1", "sessionSalt": "B2", "version": "GDS_1"}}),
            Response(200, json={}),
            Response(200, json={"data": {"state": "ok"}}),
        ],
    )

    result = ApiClient(HOST, USER, PASS, api_prefix="/webservice").check_online_update()
    assert result == {"data": {"state": "ok"}}
    assert len(route.calls) == SESSION_AUTH_CALL_COUNT


@respx.mock
def test_api_client_session_fallback_preserves_nested_data_payload() -> None:
    route = respx.post(f"http://{HOST}/api").mock(
        side_effect=[
            Response(200, json={"error": "ERR_COMMUNICATION", "id": "235"}),
            Response(200, json={"data": {"salt": "A1", "sessionSalt": "B2", "version": "GDS_1"}}),
            Response(200, json={}),
            Response(200, json={"data": {"ok": True}}),
        ],
    )
    ApiClient(HOST, USER, PASS).get_device_info(force_long=True)

    retry_body = json.loads(route.calls[3].request.read())
    assert retry_body == {
        "command": "getDeviceInfo",
        "keepAlive": True,
        "data": {"forceLong": True},
    }


@respx.mock
def test_api_client_returns_normalized_models() -> None:
    route = respx.post(f"http://{HOST}/api").mock(
        side_effect=[
            Response(
                200,
                json={
                    "data": {
                        "CurrentFirmwareVersion": "3.5.63",
                        "IpAddress": "192.0.2.10",
                    },
                },
            ),
            Response(
                200,
                json={"data": {"currentVersion": "3.5.63", "progress": "25"}},
            ),
            Response(
                200,
                json={
                    "data": {
                        "diagnosticpage": [
                            {"title": "diagnostic.titles.system", "blob": "Linux test"},
                        ],
                    },
                },
            ),
        ],
    )
    client = ApiClient(HOST, USER, PASS)

    info = client.get_device_info_model()
    status = client.get_firmware_status_model()
    diagnostics = client.get_diagnostic_page_model()

    assert info.ip_address == "192.0.2.10"
    assert status.current_version == "3.5.63"
    assert status.progress == EXPECTED_PROGRESS
    assert diagnostics.sections[0].title == "diagnostic.titles.system"
    assert len(route.calls) == EXPECTED_MODEL_CALL_COUNT


@respx.mock
def test_api_client_raises_structured_device_error_without_auth_retry() -> None:
    route = respx.post(f"http://{HOST}/api").mock(
        return_value=Response(200, json={"error": "ERR_CONFIGURATION", "id": "228"}),
    )

    with pytest.raises(DeviceApiError) as exc_info:
        ApiClient(HOST, USER, PASS).check_online_update()

    assert exc_info.value.command == "infoonline"
    assert exc_info.value.code == "228"
    assert len(route.calls) == 1


@respx.mock
def test_api_client_validates_session_retry_response() -> None:
    respx.post(f"http://{HOST}/api").mock(
        side_effect=[
            Response(200, json={"error": "ERR_COMMUNICATION", "id": "235"}),
            Response(200, json={"data": {"salt": "A1", "sessionSalt": "B2"}}),
            Response(200, json={}),
            Response(200, json={"error": "ERR_CONFIGURATION", "id": "228"}),
        ],
    )

    with pytest.raises(DeviceApiError, match="ERR_CONFIGURATION"):
        ApiClient(HOST, USER, PASS).check_online_update()


@respx.mock
def test_api_client_raises_authentication_error_when_session_init_fails() -> None:
    respx.post(f"http://{HOST}/api").mock(
        side_effect=[
            Response(200, json={"error": "ERR_COMMUNICATION", "id": "235"}),
            Response(200, json={"error": "ERR_AUTHENTICATION", "id": "220"}),
        ],
    )

    with pytest.raises(AuthenticationError, match="ERR_AUTHENTICATION"):
        ApiClient(HOST, USER, PASS).check_online_update()


@respx.mock
def test_factory_reset_sends_factory_reset_command() -> None:
    route = respx.post(f"http://{HOST}/api").mock(return_value=Response(200, json={}))
    ApiClient(HOST, USER, PASS).factory_reset()

    body = json.loads(route.calls.last.request.read())
    assert body == {"command": "factoryReset"}
