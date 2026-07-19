"""Public exception hierarchy for pygira."""

from typing import Any


class PygiraError(RuntimeError):
    """Base class for runtime failures raised by pygira."""


class TransportError(PygiraError):
    """A network connection or transport operation failed."""


class DeviceDetectionError(PygiraError):
    """The target device family could not be determined or did not match."""


class UnsupportedCapabilityError(PygiraError):
    """The selected device does not implement the requested capability."""


class OperationTimeoutError(PygiraError):
    """A device operation did not complete within its configured timeout."""


class ProtocolError(PygiraError):
    """A non-HTTP device protocol reported an error."""

    def __init__(self, protocol: str, command: str, code: object, detail: object) -> None:
        """Build an error retaining its protocol command and device error code."""
        self.protocol = protocol
        self.command = command
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(f"{protocol} {command} failed ({self.code}): {self.detail}")


class DeviceApiError(PygiraError):
    """A device accepted a request but reported a protocol-level error."""

    def __init__(self, command: str, response: dict[str, Any]) -> None:
        """Build an error containing the command, device error code, and response."""
        self.command = command
        self.response = response
        self.code = str(response.get("id", "?"))
        self.detail = str(response.get("error", "unknown device error"))
        super().__init__(f"API command {command!r} failed ({self.code}): {self.detail}")


class AuthenticationError(DeviceApiError):
    """Authentication or session establishment failed."""
