"""Core device typing and capability models."""

import sys
from enum import Enum

from pydantic import BaseModel

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:

    class StrEnum(str, Enum):
        """Compatibility fallback for Python 3.10."""


class DeviceType(StrEnum):
    """Supported device families."""

    G1 = "g1"
    X1 = "x1"
    TKS_IP = "tks-ip"
    UNKNOWN = "unknown"


class DeviceCapabilities(BaseModel):
    """Feature support flags for a device family."""

    weather: bool = False
    tks: bool = False
