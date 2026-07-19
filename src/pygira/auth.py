"""Shared iscwebservice session authentication."""

import base64
import hashlib
from typing import Any, Protocol, cast

from pygira.exceptions import AuthenticationError, DeviceApiError

AUTH_ERROR_CODES = {"220", "235"}


class _Response(Protocol):
    content: bytes

    def json(self) -> object: ...

    def raise_for_status(self) -> None: ...


class SessionClient(Protocol):
    """HTTP client operations required by the session handshake."""

    def post(self, path: str, *, json: dict[str, object]) -> _Response:
        """Send a JSON request while preserving session cookies."""
        ...


def webservice_payload(command: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a cookie-session webservice request payload."""
    payload: dict[str, Any] = {"command": command, "keepAlive": True}
    if data is not None:
        payload["data"] = data
    return payload


def compute_session_token(password: str, salt: str, session_salt: str, version: str) -> str:
    """Compute the token used by legacy and GDS_1 session authentication."""
    if version == "GDS_1":
        digest = hashlib.sha256((password + salt).encode()).digest()
        password_hash = base64.b64encode(digest).decode()[:43]
    else:
        first = hashlib.sha256(password.encode()).hexdigest()
        password_hash = hashlib.sha256(f"{first}+{salt}".encode()).hexdigest()
    return hashlib.sha256(f"{password_hash}+{session_salt}".encode()).hexdigest().upper()


def _response_json(response: _Response) -> dict[str, Any]:
    return cast("dict[str, Any]", response.json() if response.content else {})


def authenticated_request(  # noqa: PLR0913 - explicit session credentials and command data
    client: SessionClient,
    path: str,
    username: str,
    password: str,
    command: str,
    data: dict[str, Any] | None = None,
    *,
    first_error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Establish a cookie session and execute one validated command."""
    salt_response = client.post(
        path,
        json=webservice_payload("getPasswordSalt", {"username": username}),
    )
    salt_response.raise_for_status()
    salt_result = _response_json(salt_response)
    session_data = salt_result.get("data") or {}
    salt = session_data.get("salt")
    session_salt = session_data.get("sessionSalt")
    version = session_data.get("version", "1")
    if not salt or not session_salt:
        raise AuthenticationError(command, salt_result or first_error or {})

    token = compute_session_token(password, str(salt), str(session_salt), str(version))
    auth_response = client.post(
        path,
        json=webservice_payload(
            "doAuthenticateSession",
            {"username": username, "token": token},
        ),
    )
    auth_response.raise_for_status()
    auth_result = _response_json(auth_response)
    if auth_result.get("error"):
        raise AuthenticationError(command, auth_result)

    response = client.post(path, json=webservice_payload(command, data))
    response.raise_for_status()
    result = _response_json(response)
    if result.get("error"):
        error_type = (
            AuthenticationError if str(result.get("id", "")) in AUTH_ERROR_CODES else DeviceApiError
        )
        raise error_type(command, result)
    return result
