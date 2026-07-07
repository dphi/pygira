"""Device type resolution and validation."""

from pygira.core.detect import DetectionResult
from pygira.core.types import DeviceType


def resolve_device_type(requested: DeviceType | None, detected: DetectionResult) -> DeviceType:
    """Resolve requested vs detected type with strict mismatch handling."""
    if requested is None:
        if detected.device_type == DeviceType.UNKNOWN:
            msg = "Could not auto-detect device type."
            raise RuntimeError(msg)
        return detected.device_type

    if detected.device_type == DeviceType.UNKNOWN:
        error_msg = (
            f"--device {requested.value!r} was provided, but device type could not be detected "
            f"({detected.evidence})."
        )
        raise RuntimeError(error_msg)

    if requested != detected.device_type:
        error_msg = (
            f"Detected device {detected.device_type.value!r}, but --device "
            f"{requested.value!r} was requested."
        )
        raise RuntimeError(error_msg)

    return requested
