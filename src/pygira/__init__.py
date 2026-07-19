"""Public library interface for pygira."""

from importlib.metadata import PackageNotFoundError, version

from pygira.api import ApiClient
from pygira.config_service import TlsConfig
from pygira.core.types import DeviceType
from pygira.devices.g1 import G1
from pygira.devices.x1 import X1
from pygira.exceptions import (
    AuthenticationError,
    DeviceApiError,
    DeviceDetectionError,
    InvalidInputError,
    OperationTimeoutError,
    ProtocolError,
    PygiraError,
    TransportError,
    UnsupportedCapabilityError,
)
from pygira.gds import GdsClient
from pygira.models import (
    DeviceInfo,
    DiagnosticPage,
    DiagnosticSection,
    FirmwareStatus,
    NetworkConfig,
    TksConnectionStatus,
    WeatherStation,
)

try:
    __version__ = version("pygira")
except PackageNotFoundError:  # pragma: no cover - only possible outside an installed checkout
    __version__ = "0+unknown"

__all__ = [
    "ApiClient",
    "AuthenticationError",
    "DeviceApiError",
    "DeviceDetectionError",
    "DeviceInfo",
    "DeviceType",
    "DiagnosticPage",
    "DiagnosticSection",
    "FirmwareStatus",
    "G1",
    "GdsClient",
    "InvalidInputError",
    "NetworkConfig",
    "OperationTimeoutError",
    "ProtocolError",
    "PygiraError",
    "TransportError",
    "TlsConfig",
    "TksConnectionStatus",
    "UnsupportedCapabilityError",
    "WeatherStation",
    "X1",
    "__version__",
]
