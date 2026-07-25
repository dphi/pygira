"""Tests for the unified TKS-IP facade."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pygira.config_service import TksDeviceStatus
from pygira.devices.tks_ip import TksIp
from pygira.exceptions import InvalidInputError, TransportError

HOST = "192.0.2.30"
TIMEOUT = 12.0
EXPECTED_LOGIN_ATTEMPTS = 2


def _web_client() -> MagicMock:
    client = MagicMock()
    client.device_info.return_value = {
        "Software-Version": "05.04.00.08",
        "MAC-Adresse": "00:0A:B3:10:4C:D3",
    }
    client.network_info.return_value = {
        "network_name": "Front door",
        "dhcp": False,
        "ip_address": HOST,
        "subnet_mask": "255.255.255.0",
        "default_gateway": "192.0.2.1",
        "nameserver": "192.0.2.53",
        "video_resolution": "VGA",
        "gateway_id": "gateway",
    }
    client.date_time_info.return_value = {
        "automatic": True,
        "ntp_server": "pool.ntp.org",
        "timezone": "Europe/Berlin",
        "date": "2026-07-25",
        "time": "12:00",
    }
    return client


def test_tks_device_info_matches_shared_envelope_and_model() -> None:
    client = _web_client()
    with (
        patch("pygira.devices.tks_ip.cs.activate_tks_webinterface"),
        patch("pygira.devices.tks_ip.TksWebClient", return_value=client),
    ):
        device = TksIp(HOST, "admin", "secret", timeout=TIMEOUT)
        raw = device.device_info()
        model = device.device_info_model(long=False)

    assert raw["data"]["CurrentFirmwareVersion"] == "05.04.00.08"
    assert raw["data"]["IpAddress"] == HOST
    assert model.firmware_version == "05.04.00.08"
    assert model.mac_address == "00:0A:B3:10:4C:D3"
    assert model.ip_address == HOST
    client.login.assert_called_with("admin", "secret")


def test_tks_network_ntp_and_sip_inspection_use_authenticated_web_client() -> None:
    client = _web_client()
    client.sip_clients.return_value = {"clients": [], "incoming_calls": []}
    with (
        patch("pygira.devices.tks_ip.cs.activate_tks_webinterface"),
        patch("pygira.devices.tks_ip.TksWebClient", return_value=client),
    ):
        device = TksIp(HOST, password="secret")

        assert device.network_info()["ip_address"] == HOST
        assert device.ntp_info()["server"] == "pool.ntp.org"
        assert device.sip_clients()["clients"] == []


def test_tks_web_login_retries_one_transient_transport_failure() -> None:
    first = MagicMock()
    first.login.side_effect = TransportError("connection closed")
    second = _web_client()
    with (
        patch("pygira.devices.tks_ip.cs.activate_tks_webinterface") as activate,
        patch("pygira.devices.tks_ip.TksWebClient", side_effect=[first, second]),
    ):
        info = TksIp(HOST, password="secret").device_info()

    assert info["data"]["IpAddress"] == HOST
    assert activate.call_count == EXPECTED_LOGIN_ATTEMPTS


def test_tks_status_and_diagnostics_use_passive_service() -> None:
    status = TksDeviceStatus(True, True, 200, None, None, True, True)
    with patch("pygira.devices.tks_ip.cs.get_tks_device_status", return_value=status) as inspect:
        device = TksIp(HOST, aes_key="0123456789abcdefghijklmn", timeout=TIMEOUT)

        assert device.status() is status
        diagnostics = device.diagnostic_page()

    assert "TKS-IP gateway" in diagnostics["data"]["diagnosticpage"][0]["title"]
    inspect.assert_called_with(
        HOST,
        timeout=TIMEOUT,
        aes_key="0123456789abcdefghijklmn",
    )


def test_tks_logfile_requires_aes_key() -> None:
    with pytest.raises(InvalidInputError, match="AES key"):
        TksIp(HOST).logfile()


def test_tks_backup_and_firmware_operations_delegate_to_web_client(tmp_path: Path) -> None:
    client = _web_client()
    client.backup_save.return_value = b"backup"
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"firmware")
    with (
        patch("pygira.devices.tks_ip.cs.activate_tks_webinterface"),
        patch("pygira.devices.tks_ip.TksWebClient", return_value=client),
    ):
        device = TksIp(HOST, password="secret", timeout=TIMEOUT)

        assert device.backup_save() == b"backup"
        device.backup_restore(b"restore", "backup.img")
        device.firmware_update(firmware)

    client.backup_save.assert_called_once_with(timeout=TIMEOUT)
    client.backup_restore.assert_called_once_with(b"restore", "backup.img")
    client.firmware_update.assert_called_once_with(b"firmware", "firmware.bin")
