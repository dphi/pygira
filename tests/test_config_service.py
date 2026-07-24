"""
Tests for config_service.py — configurationservice REST client.
API: HTTPS port 4433, auth: "basic <b64>", XML namespace http://service.schema.gira.de/configuration
"""

import base64
import gzip
from typing import NoReturn
from unittest.mock import patch

import pytest
from lxml import etree

from pygira import _http
from pygira import _http as httpx
from pygira import config_service as cs
from pygira.models import NetworkConfig
from tests import _httpmock as respx
from tests._httpmock import Request, Response
from tests.fixtures import DEVICE_XML

HOST = "192.168.1.100"
USER = "admin"
PASS = "secret"

EXPECTED_AUTH = "basic " + base64.b64encode(b"admin:secret").decode()
TKS_STATE_POLL_COUNT = 2


# ── auth header format ────────────────────────────────────────────────────────


def _xml_text(root: etree._Element, tag: str) -> str:
    node = root.find(tag)
    assert node is not None
    return node.text or ""


def test_auth_header_format() -> None:
    """Auth header must be lowercase 'basic', not 'Basic' (per firmware source)."""
    header = cs._auth_header(USER, PASS)
    assert header.startswith("basic ")
    decoded = base64.b64decode(header[6:]).decode()
    assert decoded == f"{USER}:{PASS}"


# ── XML parsing ───────────────────────────────────────────────────────────────


def test_parse_device_info_reads_all_fields() -> None:
    root = etree.fromstring(DEVICE_XML.encode())
    info = cs.parse_device_info(root)

    assert info.firmware_version == "3.5.62.0"
    assert info.mac_address == "AA:BB:CC:DD:EE:FF"
    assert info.ip_address == "192.168.1.100"
    assert info.subnet_mask == "255.255.255.0"
    assert info.default_gateway == "192.168.1.1"
    assert info.primary_dns == "8.8.8.8"
    assert info.secondary_dns == "8.8.4.4"
    assert info.dhcp is False
    assert info.device_name == "Living Room Panel"
    assert info.entity_id == "abc123"


def test_parse_device_info_dhcp_true() -> None:
    xml = DEVICE_XML.replace(
        '<conf:DHCP GpaOnly="true">false</conf:DHCP>',
        '<conf:DHCP GpaOnly="true">true</conf:DHCP>',
    )
    root = etree.fromstring(xml.encode())
    info = cs.parse_device_info(root)
    assert info.dhcp is True


def test_parse_device_info_missing_field_returns_empty() -> None:
    """Missing optional fields should return empty string, not raise."""
    xml = DEVICE_XML.replace('<conf:SecondaryDNS GpaOnly="true">8.8.4.4</conf:SecondaryDNS>', "")
    root = etree.fromstring(xml.encode())
    info = cs.parse_device_info(root)
    assert info.secondary_dns == ""


# ── IP config mutation ────────────────────────────────────────────────────────


def test_set_ip_config_static() -> None:
    root = etree.fromstring(DEVICE_XML.encode())
    cfg = NetworkConfig(
        dhcp=False,
        ip_address="10.0.0.50",
        subnet_mask="255.255.0.0",
        default_gateway="10.0.0.1",
        primary_dns="1.1.1.1",
        secondary_dns="1.0.0.1",
    )
    cs.set_ip_config(root, cfg)

    NS = "http://service.schema.gira.de/configuration"
    assert _xml_text(root, f"{{{NS}}}DHCP") == "false"
    assert _xml_text(root, f"{{{NS}}}IpAddress") == "10.0.0.50"
    assert _xml_text(root, f"{{{NS}}}SubnetMask") == "255.255.0.0"
    assert _xml_text(root, f"{{{NS}}}DefaultGateway") == "10.0.0.1"
    assert _xml_text(root, f"{{{NS}}}PrimaryDNS") == "1.1.1.1"
    assert _xml_text(root, f"{{{NS}}}SecondaryDNS") == "1.0.0.1"


def test_set_ip_config_dhcp_does_not_set_ip_fields() -> None:
    """When DHCP=True, IP/mask/gateway fields should not be overwritten."""
    root = etree.fromstring(DEVICE_XML.encode())
    cfg = NetworkConfig(dhcp=True)  # no static fields provided
    cs.set_ip_config(root, cfg)

    NS = "http://service.schema.gira.de/configuration"
    assert _xml_text(root, f"{{{NS}}}DHCP") == "true"
    # Original IP should be untouched since cfg has empty strings
    assert _xml_text(root, f"{{{NS}}}IpAddress") == "192.168.1.100"


# ── HTTP round-trip ───────────────────────────────────────────────────────────


@respx.mock
def test_get_device_xml_sends_correct_auth() -> None:
    respx.get(f"https://{HOST}:4433/service").mock(
        return_value=Response(200, content=DEVICE_XML.encode()),
    )
    root = cs.get_device_xml(HOST, USER, PASS)
    assert root is not None

    request = respx.calls.last.request
    assert request.headers["Authorization"] == EXPECTED_AUTH


@respx.mock
def test_push_device_xml_uses_put() -> None:
    """configurationservice update must use PUT (GPA protocol)."""
    route = respx.put(f"https://{HOST}:4433/service").mock(return_value=Response(200))
    root = etree.fromstring(DEVICE_XML.encode())
    cs.push_device_xml(HOST, USER, PASS, root)
    assert route.called


@respx.mock
def test_push_device_xml_sends_xml_content_type() -> None:
    respx.put(f"https://{HOST}:4433/service").mock(return_value=Response(200))
    root = etree.fromstring(DEVICE_XML.encode())
    cs.push_device_xml(HOST, USER, PASS, root)
    request = respx.calls.last.request
    assert "xml" in request.headers.get("Content-Type", "")


@respx.mock
def test_get_device_xml_raises_on_auth_failure() -> None:
    respx.get(f"https://{HOST}:4433/service").mock(return_value=Response(401))
    with pytest.raises(httpx.HTTPError):
        cs.get_device_xml(HOST, USER, "wrong")


@respx.mock
def test_download_logs_returns_bytes() -> None:
    fake_zip = b"PK\x03\x04fake-zip-data"
    respx.get(f"https://{HOST}:4433/discovery/download/logfiles").mock(
        return_value=Response(200, content=fake_zip),
    )
    data = cs.download_logs(HOST, USER, PASS)
    assert data == fake_zip


@respx.mock
def test_download_logs_x1_uses_session_auth_flow() -> None:
    content = base64.b64encode(b"PK\x03\x04x1-log-data").decode()
    respx.post(f"https://{HOST}/webservice").mock(
        side_effect=[
            Response(200, json={"data": {"salt": "A1", "sessionSalt": "B2"}}),
            Response(200, json={"data": {"authenticated": True}}),
            Response(200, json={"data": {"content": content, "filename": "logfiles.zip"}}),
        ],
    )

    data = cs.download_logs_x1(HOST, USER, PASS)
    assert data == b"PK\x03\x04x1-log-data"


@respx.mock
def test_download_tks_logfile_decompresses_gzip_body_with_no_content_encoding_header() -> None:
    """Confirmed live (2026-07-20): the daemon sends a gzip *body* without ever
    announcing `Content-Encoding: gzip` — detection has to be by magic bytes."""
    raw = b"opaque device log bundle bytes"
    respx.get(f"http://{HOST}/getlogfile").mock(
        return_value=Response(200, content=gzip.compress(raw)),
    )

    data = cs.download_tks_logfile(HOST)

    assert data == raw


@respx.mock
def test_download_tks_logfile_passes_through_non_gzip_body() -> None:
    respx.get(f"http://{HOST}/getlogfile").mock(return_value=Response(200, content=b"plain log"))

    data = cs.download_tks_logfile(HOST)

    assert data == b"plain log"


def test_decrypt_tks_logfile_matches_nist_aes_192_vector() -> None:
    key = "000102030405060708090a0b0c0d0e0f1011121314151617"
    ciphertext = bytes.fromhex("dda97ca4864cdfe06eaf70a0ec0d7191")

    assert cs.decrypt_tks_logfile(ciphertext, key) == bytes.fromhex(
        "00112233445566778899aabbccddeeff",
    )


@pytest.mark.parametrize("key", ["short", "0" * 46])
def test_decrypt_tks_logfile_rejects_invalid_key_length(key: str) -> None:
    with pytest.raises(ValueError, match="24 bytes"):
        cs.decrypt_tks_logfile(b"\0" * 16, key)


def test_decrypt_tks_logfile_rejects_partial_block() -> None:
    with pytest.raises(ValueError, match="multiple of 16"):
        cs.decrypt_tks_logfile(b"partial-block", b"k" * 24)


@respx.mock
def test_download_tks_logfile_decrypts_after_gzip() -> None:
    key = "000102030405060708090a0b0c0d0e0f1011121314151617"
    ciphertext = bytes.fromhex("dda97ca4864cdfe06eaf70a0ec0d7191")
    respx.get(f"http://{HOST}/getlogfile").mock(
        return_value=Response(200, content=gzip.compress(ciphertext)),
    )

    assert cs.download_tks_logfile(HOST, aes_key=key) == bytes.fromhex(
        "00112233445566778899aabbccddeeff",
    )


@respx.mock
def test_set_syslog_severity_x1_uses_session_auth_flow() -> None:
    route = respx.post(f"https://{HOST}/webservice").mock(
        side_effect=[
            Response(200, json={"data": {"salt": "A1", "sessionSalt": "B2", "version": "GDS_1"}}),
            Response(200, json={}),
            Response(200, json={"data": {}}),
        ],
    )

    cs.set_syslog_severity_x1(HOST, USER, PASS, 0)
    assert route.called


@respx.mock
def test_get_syslog_severity_x1_reads_device_info_field() -> None:
    respx.post(f"https://{HOST}/webservice").mock(
        side_effect=[
            Response(200, json={"data": {"salt": "A1", "sessionSalt": "B2", "version": "GDS_1"}}),
            Response(200, json={}),
            Response(200, json={"data": {"SyslogSeverity": 0}}),
        ],
    )

    severity = cs.get_syslog_severity_x1(HOST, USER, PASS)
    assert severity == 0


# ── TKS-IP web interface activation ───────────────────────────────────────────


@respx.mock
def test_activate_tks_webinterface_sends_document_ready_and_waits_for_state() -> None:
    hook = respx.get(
        f"http://{HOST}/json?sid=undefined&rid=undefined&data=%5B%22documentReady%22%5D",
    ).mock(return_value=Response(200, content=b"[]"))
    state = respx.get(f"http://{HOST}:8080/state?callback=setState").mock(
        side_effect=[
            Response(200, content=b'setState({"system.state":"1"})'),
            Response(200, content=b'setState({"system.state":"0"})'),
        ],
    )

    with patch("pygira.config_service.time.sleep"):
        result = cs.activate_tks_webinterface(HOST, timeout=10, poll_interval=0.1)

    assert hook.called
    assert len(state.calls) == TKS_STATE_POLL_COUNT
    assert result.state == "0"
    assert result.url == f"http://{HOST}:8080/"
    assert result.elapsed_seconds >= 0


@respx.mock
def test_activate_tks_webinterface_times_out_with_last_state() -> None:
    respx.get(
        f"http://{HOST}/json?sid=undefined&rid=undefined&data=%5B%22documentReady%22%5D",
    ).mock(return_value=Response(200, content=b"[]"))
    respx.get(f"http://{HOST}:8080/state?callback=setState").mock(
        return_value=Response(200, content=b'setState({"system.state":"1"})'),
    )

    with patch("pygira.config_service.time.sleep"), pytest.raises(RuntimeError) as exc:
        cs.activate_tks_webinterface(HOST, timeout=0.01, poll_interval=0.01)

    assert "last state was '1'" in str(exc.value)


@respx.mock
def test_activate_tks_webinterface_fails_on_error_state() -> None:
    respx.get(
        f"http://{HOST}/json?sid=undefined&rid=undefined&data=%5B%22documentReady%22%5D",
    ).mock(return_value=Response(200, content=b"[]"))
    respx.get(f"http://{HOST}:8080/state?callback=setState").mock(
        return_value=Response(200, content=b'setState({"system.state":"2"})'),
    )

    with patch("pygira.config_service.time.sleep"), pytest.raises(RuntimeError) as exc:
        cs.activate_tks_webinterface(HOST, timeout=1, poll_interval=0.01)

    assert "error state 2" in str(exc.value)


# ── TKS-IP read-only status check ─────────────────────────────────────────────


def _refuse(request: Request) -> NoReturn:
    msg = "Connection refused"
    raise _http.HTTPError(msg)


@respx.mock
def test_get_tks_status_app_running() -> None:
    respx.get(f"http://{HOST}/").mock(return_value=Response(200, content=b"<html></html>"))
    respx.get(f"http://{HOST}:8080/state?callback=setState").mock(
        return_value=Response(200, content=b'setState({"system.state":"0"})'),
    )

    status = cs.get_tks_status(HOST)

    assert status.bootstrap_reachable is True
    assert status.app_running is True
    assert status.state_code == "0"
    assert status.state_description == "ready"


@respx.mock
def test_get_tks_status_app_not_running() -> None:
    respx.get(f"http://{HOST}/").mock(return_value=Response(200, content=b"<html></html>"))
    respx.get(f"http://{HOST}:8080/state?callback=setState").mock(side_effect=_refuse)

    status = cs.get_tks_status(HOST)

    assert status.bootstrap_reachable is True
    assert status.app_running is False
    assert status.state_code is None
    assert status.state_description is None


@respx.mock
def test_get_tks_status_gateway_unreachable() -> None:
    respx.get(f"http://{HOST}/").mock(side_effect=_refuse)
    respx.get(f"http://{HOST}:8080/state?callback=setState").mock(side_effect=_refuse)

    status = cs.get_tks_status(HOST)

    assert status.bootstrap_reachable is False
    assert status.app_running is False
