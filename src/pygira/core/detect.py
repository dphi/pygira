"""Device type detection."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from lxml import etree

from pygira import _http as httpx
from pygira import config_service as cs
from pygira.core.types import DeviceType


@dataclass(frozen=True)
class ProbeAttempt:
    """Sanitized outcome of one device-detection probe."""

    endpoint: str
    outcome: str
    detail: str


@dataclass(frozen=True)
class DetectionResult:
    """Detected device type plus evidence string."""

    device_type: DeviceType
    evidence: str
    app_name: str = ""
    firmware_version: str = ""
    attempts: tuple[ProbeAttempt, ...] = ()


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


def _failure_detail(exc: Exception) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _try_json_probe(
    host: str,
    username: str,
    password: str,
    path: str,
) -> tuple[DetectionResult | None, ProbeAttempt]:
    payload: dict[str, object] = {"command": "getDeviceInfo"}
    auth = (username, password) if password else None
    try:
        with httpx.Client(base_url=f"http://{host}", timeout=8.0, auth=auth) as client:
            resp = client.post(path, json=payload)
            resp.raise_for_status()
            response_data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return None, ProbeAttempt(path, "failed", _failure_detail(exc))

    if not isinstance(response_data, Mapping):
        return None, ProbeAttempt(path, "invalid", "response was not a JSON object")
    nested = response_data.get("data", {})
    if not isinstance(nested, Mapping):
        return None, ProbeAttempt(path, "invalid", "response data was not a JSON object")
    data = nested

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
        return None, ProbeAttempt(path, "inconclusive", "identity fields were empty")
    result = DetectionResult(detected, f"{path} AppName={marker or 'n/a'}", marker, fw)
    return result, ProbeAttempt(path, "matched", result.evidence)


def detect_device_type(host: str, username: str, password: str) -> DetectionResult:
    """Detect Gira device family — JSON probes first, configurationservice XML as fallback."""
    attempts: list[ProbeAttempt] = []
    probe, attempt = _try_json_probe(host, username, password, "/webservice")
    attempts.append(attempt)
    if probe is None:
        probe, attempt = _try_json_probe(host, username, password, "/api")
        attempts.append(attempt)
    if probe is not None:
        return replace(probe, attempts=tuple(attempts))

    try:
        root = cs.get_device_xml(host, username, password)
    except (httpx.HTTPError, etree.XMLSyntaxError, ValueError) as exc:
        attempts.append(
            ProbeAttempt("configurationservice", "failed", _failure_detail(exc)),
        )
        evidence = "; ".join(f"{item.endpoint}: {item.detail}" for item in attempts)
        return DetectionResult(DeviceType.UNKNOWN, evidence, attempts=tuple(attempts))

    device_type = _text(root, "DeviceType").upper()
    logical_name = _text(root, "LogicalName")
    entity_name = _text(root, "EntityName")
    app_name = _text(root, "AppDeviceName")
    fw = _text(root, "FirmwareVersion")
    detected = _classify(device_type, logical_name, entity_name, app_name)
    evidence = f"DeviceType={device_type or 'n/a'}"
    attempts.append(ProbeAttempt("configurationservice", "matched", evidence))
    return DetectionResult(detected, evidence, app_name, fw, tuple(attempts))
