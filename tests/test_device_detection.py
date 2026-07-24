"""Tests for device detection and resolution."""

from unittest.mock import patch

import pytest
from lxml import etree

from pygira.core.detect import DetectionResult, ProbeAttempt, detect_device_type
from pygira.core.resolve import resolve_device_type
from pygira.core.types import DeviceType
from pygira.exceptions import DeviceDetectionError
from tests._httpmock import Request, Response


def _xml(device_type: str, logical_name: str = "") -> etree._Element:
    data = f"""<?xml version='1.0' encoding='utf-8'?>
<conf:Device xmlns:conf='http://service.schema.gira.de/configuration'>
  <conf:DeviceType>{device_type}</conf:DeviceType>
  <conf:LogicalName>{logical_name}</conf:LogicalName>
</conf:Device>
"""
    return etree.fromstring(data.encode())


def test_detect_device_g1_from_device_type() -> None:
    with patch("pygira.core.detect.cs.get_device_xml", return_value=_xml("GIG1LXKXIP")):
        result = detect_device_type("host", "admin", "pw")
    assert result.device_type == DeviceType.G1


def test_detect_device_x1_from_name() -> None:
    with patch("pygira.core.detect.cs.get_device_xml", return_value=_xml("", "GiraX1")):
        result = detect_device_type("host", "admin", "pw")
    assert result.device_type == DeviceType.X1


def test_resolve_mismatch_raises() -> None:
    detected = DetectionResult(DeviceType.X1, "DeviceType=X1")
    with pytest.raises(DeviceDetectionError, match="Detected device"):
        resolve_device_type(DeviceType.G1, detected)


def test_resolve_unknown_without_request_raises() -> None:
    detected = DetectionResult(DeviceType.UNKNOWN, "n/a")
    with pytest.raises(DeviceDetectionError, match="Could not auto-detect"):
        resolve_device_type(None, detected)


def test_detect_device_falls_back_to_webservice_probe() -> None:
    with (
        patch(
            "pygira.core.detect.cs.get_device_xml",
            side_effect=RuntimeError("no config service"),
        ),
        patch("pygira.core.detect.httpx.Client") as c,
    ):
        client = c.return_value.__enter__.return_value
        client.post.return_value = Response(
            200,
            json={"data": {"AppName": "Gira X1", "CurrentFirmwareVersion": "2.8.874.0"}},
            request=Request("POST", "http://host/webservice"),
        )
        result = detect_device_type("host", "admin", "")
    assert result.device_type == DeviceType.X1
    assert result.firmware_version == "2.8.874.0"


def test_detect_device_falls_back_to_api_probe_for_g1() -> None:
    with (
        patch(
            "pygira.core.detect.cs.get_device_xml",
            side_effect=RuntimeError("no config service"),
        ),
        patch("pygira.core.detect.httpx.Client") as c,
    ):
        client = c.return_value.__enter__.return_value
        client.post.side_effect = [
            Response(404, request=Request("POST", "http://host/webservice")),
            Response(
                200,
                json={
                    "data": {
                        "KIM-FriendlyName": "Gira G1",
                        "CurrentFirmwareVersion": "3.5.62.0",
                    },
                },
                request=Request("POST", "http://host/api"),
            ),
        ]
        result = detect_device_type("host", "admin", "")

    assert result.device_type == DeviceType.G1
    assert result.firmware_version == "3.5.62.0"
    assert result.attempts == (
        ProbeAttempt("/webservice", "failed", "HTTPError: HTTP 404"),
        ProbeAttempt("/api", "matched", "/api AppName=Gira G1"),
    )


def test_detect_device_reports_each_expected_probe_failure() -> None:
    with (
        patch(
            "pygira.core.detect.cs.get_device_xml",
            side_effect=etree.XMLSyntaxError("invalid XML", 0, 0, 0),
        ),
        patch("pygira.core.detect.httpx.Client") as client_type,
    ):
        client = client_type.return_value.__enter__.return_value
        client.post.return_value = Response(
            200,
            json=[],
            request=Request("POST", "http://host/probe"),
        )

        result = detect_device_type("host", "admin", "")

    assert result.device_type == DeviceType.UNKNOWN
    assert [attempt.endpoint for attempt in result.attempts] == [
        "/webservice",
        "/api",
        "configurationservice",
    ]
    assert "response was not a JSON object" in result.evidence
    assert "invalid XML" in result.evidence


def test_detect_device_does_not_hide_unexpected_probe_failures() -> None:
    with patch("pygira.core.detect.httpx.Client") as client_type:
        client = client_type.return_value.__enter__.return_value
        client.post.side_effect = RuntimeError("programming bug")

        with pytest.raises(RuntimeError, match="programming bug"):
            detect_device_type("host", "admin", "")
