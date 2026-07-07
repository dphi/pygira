"""Device type detection."""

from dataclasses import dataclass
from typing import Any, cast

from lxml import etree

from pygira import _http as httpx
from pygira import config_service as cs
from pygira.core.types import DeviceType


@dataclass(frozen=True)
class DetectionResult:
    """Detected device type plus evidence string."""

    device_type: DeviceType
    evidence: str
    app_name: str = ""
    firmware_version: str = ""


def _text(root: etree._Element, tag: str) -> str:
    el = root.find(f"conf:{tag}", cs.NSMAP)
    return (el.text or "").strip() if el is not None else ""


def _classify(device_type: str, logical_name: str, entity_name: str, app_name: str) -> DeviceType:
    fingerprint = " ".join([device_type, logical_name, entity_name, app_name]).lower()
    if "gig1" in device_type.lower() or "g1" in fingerprint:
        return DeviceType.G1
    if "x1" in fingerprint or "girax1" in fingerprint:
        return DeviceType.X1
    return DeviceType.UNKNOWN


def _try_json_probe(host: str, username: str, password: str, path: str) -> DetectionResult | None:
    payload: dict[str, Any] = {"command": "getDeviceInfo"}
    auth = (username, password) if password else None
    try:
        with httpx.Client(base_url=f"http://{host}", timeout=8.0, auth=auth) as client:
            resp = client.post(path, json=payload)
            resp.raise_for_status()
            response_data = cast("dict[str, Any]", resp.json())
            data = response_data.get("data", {})
    except Exception:
        return None

    app_name = str(data.get("AppName", "")).strip()
    friendly_name = str(data.get("KIM-FriendlyName", "")).strip()
    fw = str(data.get("CurrentFirmwareVersion", "")).strip()
    marker = app_name or friendly_name
    detected = _classify("", "", "", marker)
    if detected == DeviceType.UNKNOWN and marker:
        lowered = marker.lower()
        if "x1" in lowered:
            detected = DeviceType.X1
        elif "g1" in lowered:
            detected = DeviceType.G1
    if detected == DeviceType.UNKNOWN and not marker and not fw:
        return None
    return DetectionResult(detected, f"{path} AppName={marker or 'n/a'}", marker, fw)


def detect_device_type(host: str, username: str, password: str) -> DetectionResult:
    """Detect Gira device family — JSON probes first, configurationservice XML as fallback."""
    probe = _try_json_probe(host, username, password, "/webservice")
    if probe is None:
        probe = _try_json_probe(host, username, password, "/api")
    if probe is not None:
        return probe

    try:
        root = cs.get_device_xml(host, username, password)
    except Exception:
        return DetectionResult(DeviceType.UNKNOWN, "no probe succeeded")

    device_type = _text(root, "DeviceType").upper()
    logical_name = _text(root, "LogicalName")
    entity_name = _text(root, "EntityName")
    app_name = _text(root, "AppDeviceName")
    fw = _text(root, "FirmwareVersion")
    detected = _classify(device_type, logical_name, entity_name, app_name)
    return DetectionResult(detected, f"DeviceType={device_type or 'n/a'}", app_name, fw)
