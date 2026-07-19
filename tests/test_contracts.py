"""Contract tests for normalized responses from known firmware families."""

import json
from pathlib import Path
from typing import Any

import pytest

from pygira.models import DeviceInfo, DiagnosticPage, FirmwareStatus, TksConnectionStatus

CONTRACTS = sorted((Path(__file__).parent / "contracts").glob("*/*/device.json"))


@pytest.mark.parametrize("contract_path", CONTRACTS, ids=lambda path: str(path.parent))
def test_device_contract_normalization(contract_path: Path) -> None:
    contract: dict[str, Any] = json.loads(contract_path.read_text())

    info = DeviceInfo.from_webservice(contract["device_info_response"])
    firmware = FirmwareStatus.from_webservice(contract["firmware_status_response"])
    diagnostics = DiagnosticPage.from_webservice(contract["diagnostic_page_response"])

    assert info.firmware_version == contract["firmware"]
    assert firmware.current_version == contract["firmware"]
    assert info.ip_address.startswith("192.0.2.")
    assert diagnostics.sections

    if "tks_connection_status" in contract:
        tks = TksConnectionStatus.from_gds(contract["tks_connection_status"])
        assert tks.present
        assert tks.state == "registered"
