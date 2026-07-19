"""GDS WebSocket client — port 4432 (WSS).

URL: wss://<host>:4432/gds/api?ui<base64(user:pass)>
Auth: query-string token "ui" + base64(user:password)
All messages: {"request": {"command": "...", ...}}
"""

import asyncio
import base64
import json
import ssl
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar, cast

import websockets

from pygira.exceptions import OperationTimeoutError, ProtocolError, TransportError

T = TypeVar("T")

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection


def _make_url(host: str, username: str, password: str) -> str:
    token = "ui" + base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"wss://{host}:4432/gds/api?{token}"


def _make_ssl(*, verify_tls: bool = False) -> ssl.SSLContext:
    """Build a TLS context, retaining the device-compatible insecure default."""
    ctx = ssl.create_default_context()
    if not verify_tls:
        # Gira CA cert is private and unavailable through public CA stores.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _response_data(resp: dict[str, object]) -> dict[str, object]:
    value = resp.get("response", {})
    return value if isinstance(value, dict) else {}


def _parse_message(raw: str | bytes) -> dict[str, object]:
    value = json.loads(raw)
    if isinstance(value, dict):
        return value
    protocol = "GDS"
    code = "invalid-message"
    msg = "GDS returned a non-object WebSocket message"
    raise ProtocolError(protocol, "receive", code, msg)


@dataclass(slots=True)
class _PendingRequest:
    payload: dict[str, object]
    future: asyncio.Future[dict[str, object]]


def _is_echo_subset(expected: object, echoed: object) -> bool:
    if isinstance(expected, Mapping) and isinstance(echoed, Mapping):
        return all(
            key in expected and _is_echo_subset(expected[key], value)
            for key, value in echoed.items()
        )
    return expected == echoed


def _requests_match(expected: dict[str, object], echoed: dict[str, object]) -> bool:
    """Match a response using every request field echoed by the device."""
    if expected.get("command") != echoed.get("command"):
        return False
    comparable = expected
    if "id" in echoed and "id" not in expected and "urn" in expected:
        # Some firmware echoes a URN-addressed SetValue request under `id`.
        comparable = {**expected, "id": expected["urn"]}
    shared_echo = {key: value for key, value in echoed.items() if key in comparable}
    return _is_echo_subset(comparable, shared_echo)


# From tksip-definitions.xml + layout.js dcs.messages.js
_CONNECTION_STATE = {
    "0": "initialising",
    "1": "unregistered",
    "2": "registering",
    "3": "registered",
    "4": "unregistering",
    "5": "connection_lost",
}
_DISCONNECT_REASON = {
    "0": None,
    "3": "wrong_credentials",
    "4": "timeout",
    "5": "license_exceeded",
    "6": "internal_error",
}


class GdsClient:
    """Concurrent GDS WebSocket session for a single host (port 4432, WSS).

    A background reader correlates responses and retains push messages for
    :meth:`next_event` and :meth:`listen`.
    """

    def __init__(  # noqa: PLR0913 - transport security options are explicit public API
        self,
        host: str,
        username: str,
        password: str,
        timeout: float = 15.0,
        *,
        verify_tls: bool = False,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        """Initialize without connecting; call connect() to open the session."""
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout
        self.ssl_context = ssl_context or _make_ssl(verify_tls=verify_tls)
        self._ws: ClientConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: list[_PendingRequest] = []
        self._events: asyncio.Queue[dict[str, object] | BaseException | None] = asyncio.Queue()
        self._send_lock = asyncio.Lock()

    async def __aenter__(self) -> "GdsClient":
        """Connect and return this client as an async context manager."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        """Close the WebSocket connection."""
        await self.close()

    async def connect(self) -> None:
        """Open WebSocket connection and register application."""
        if self._ws is not None:
            msg = "GDS client is already connected"
            raise TransportError(msg)
        self._events = asyncio.Queue()
        url = _make_url(self.host, self.username, self.password)
        try:
            self._ws = await websockets.connect(
                url,
                ssl=self.ssl_context,
                open_timeout=self.timeout,
            )
            await self._register()
            self._reader_task = asyncio.create_task(
                self._reader_loop(),
                name=f"pygira-gds-reader-{self.host}",
            )
        except Exception as exc:
            await self.close()
            # Strip the credential token from the URL in any exception message.
            safe = f"wss://{self.host}:4432/gds/api?<token>"
            detail = str(exc).replace(url, safe)
            msg = f"Could not connect or register GDS at {safe}: {detail}"
            raise TransportError(msg) from exc

    async def _register(self) -> None:
        assert self._ws is not None
        msg = {
            "request": {
                "command": "RegisterApplication",
                "applicationId": "Gira.UniversalApp",
                "applicationType": "ui",
                "pretty": "true",
                "instanceId": str(uuid.uuid4()),
            },
        }
        await self._ws.send(json.dumps(msg))
        # consume the registration response
        await asyncio.wait_for(self._ws.recv(), timeout=self.timeout)

    async def close(self) -> None:
        """Close the WebSocket connection."""
        self._fail_pending(TransportError("GDS connection closed"))
        reader = self._reader_task
        self._reader_task = None
        if reader is not None:
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader
        if self._ws:
            await self._ws.close()
            self._ws = None
        await self._events.put(None)

    def _fail_pending(self, exc: BaseException) -> None:
        pending, self._pending = self._pending, []
        for request in pending:
            if not request.future.done():
                request.future.set_exception(exc)

    def _route_response(self, message: dict[str, object]) -> bool:
        echoed = _response_data(message).get("request")
        if not isinstance(echoed, dict):
            return False
        for index, request in enumerate(self._pending):
            if _requests_match(request.payload, echoed):
                self._pending.pop(index)
                if not request.future.done():
                    request.future.set_result(message)
                return True
        return False

    async def _reader_loop(self) -> None:
        """Own all WebSocket reads and route responses or push events."""
        assert self._ws is not None
        try:
            while True:
                raw = await self._ws.recv()
                message = _parse_message(raw)
                if self._route_response(message):
                    continue
                await self._events.put(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = exc if isinstance(exc, ProtocolError) else TransportError(str(exc))
            self._fail_pending(failure)
            await self._events.put(failure)

    async def _send_message(self, payload: dict[str, object]) -> None:
        assert self._ws is not None
        async with self._send_lock:
            try:
                await self._ws.send(json.dumps({"request": payload}))
            except Exception as exc:
                msg = f"Could not send GDS command {payload.get('command', '')!r}"
                raise TransportError(msg) from exc

    async def _send_request(self, payload: dict[str, object]) -> dict[str, object]:
        if self._ws is None or self._reader_task is None:
            msg = "GDS client is not connected"
            raise TransportError(msg)
        future = asyncio.get_running_loop().create_future()
        pending = _PendingRequest(payload, future)
        self._pending.append(pending)
        try:
            await self._send_message(payload)
        except Exception:
            with suppress(ValueError):
                self._pending.remove(pending)
            raise

        command = payload.get("command", "")
        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        except TimeoutError as exc:
            msg = f"No response for GDS command {command!r}"
            raise OperationTimeoutError(msg) from exc
        finally:
            with suppress(ValueError):
                self._pending.remove(pending)

    def _raise_for_error(self, resp: dict[str, object], command: str) -> None:
        error = _response_data(resp).get("error")
        if not isinstance(error, dict):
            return
        code = error.get("code")
        if code in (None, 0, "0"):
            return
        text = error.get("text") or "device reported an error"
        hint = error.get("hint")
        detail = f" ({hint})" if hint else ""
        protocol = "GDS"
        error_detail = f"{text}{detail}"
        raise ProtocolError(protocol, command, code, error_detail)

    async def get_process_view(self) -> dict:
        """Return the full GDS process view (all devices, channels, datapoints with live values)."""
        # ui:"true" and cached:"false" match what the G1 web UI sends on G1 (not X1).
        return await self._send_request(
            {"command": "GetProcessView", "ui": "true", "cached": "false"},
        )

    def _find_datapoint(self, obj: object, name_fragment: str) -> dict[str, object] | None:
        if isinstance(obj, dict):
            urn = obj.get("urn")
            if isinstance(urn, str) and name_fragment in urn:
                return obj
            for value in obj.values():
                match = self._find_datapoint(value, name_fragment)
                if match is not None:
                    return match
        elif isinstance(obj, list):
            for item in obj:
                match = self._find_datapoint(item, name_fragment)
                if match is not None:
                    return match
        return None

    async def get_tks_status(self) -> dict[str, object]:
        """Return TKS-IP connection status via fixed G1 datapoint IDs.

        Keys: present (bool), state (str or None), disconnect_reason (str or None).
        state values: registered, unregistered, registering, unregistering,
        initialising, connection_lost. present=False when TKS channel absent.

        Uses fixed IDs from g1_device.xml (Connect channel StartId=500001):
        500003=ConnectionState, 500004=DisconnectReason. Works without ETS project.
        """
        # To re-derive these IDs if firmware changes:
        #   GET https://<g1>:4433/discovery/download/logfiles  (Basic auth, user=device)
        #   Unzip → opt/gira/etc/devicestack/devicedefinition/g1_device.xml
        #   Find: <channel Name="Connect" TypeURN="...DcsVHsGUI.Connection" StartId="N" ...>
        #   Then: channel=N, Connect trigger=N+1, ConnectionState=N+2, DisconnectReason=N+3
        state_resp = await self._send_request({"command": "GetValue", "id": "500003"})
        r = _response_data(state_resp)
        error = r.get("error", {})
        error_code = error.get("code") if isinstance(error, dict) else None
        if error_code not in (None, "0", 0):
            return {"present": False, "state": None, "disconnect_reason": None}
        state_raw = str(r.get("value", ""))
        reason_resp = await self._send_request({"command": "GetValue", "id": "500004"})
        reason_raw = str(_response_data(reason_resp).get("value", ""))
        return {
            "present": True,
            "state": _CONNECTION_STATE.get(state_raw, state_raw),
            "disconnect_reason": _DISCONNECT_REASON.get(reason_raw),
        }

    async def get_app_value(self, app_name: str, key: str) -> object:
        """Read a persistent app value from GDS."""
        resp = await self._send_request({"command": "GetAppValue", "appName": app_name, "key": key})
        return _response_data(resp).get("value")

    async def set_app_value(self, app_name: str, key: str, value: str) -> None:
        """Write a persistent app value via GDS."""
        resp = await self._send_request(
            {"command": "SetAppValue", "appName": app_name, "key": key, "value": value},
        )
        self._raise_for_error(resp, "SetAppValue")

    async def set_configuration(self, urn: str, metadata: dict[str, str]) -> None:
        """Write channel configuration metadata (key/value pairs) to a URN."""
        items = [{"key": k, "value": v} for k, v in metadata.items()]
        resp = await self._send_request(
            {
                "command": "SetConfiguration",
                "object": {"urn": urn, "metadata": items, "pretty": "true"},
            },
        )
        self._raise_for_error(resp, "SetConfiguration")

    async def set_value(self, urn: str, value: str) -> None:
        """Write a datapoint value by URN."""
        # Web UI uses "urn" key for URN-addressed datapoints, "id" for numeric IDs.
        # All our callers use URNs.
        resp = await self._send_request({"command": "SetValue", "urn": urn, "value": value})
        self._raise_for_error(resp, "SetValue")

    async def get_device_config(self) -> dict[str, str]:
        """Return the flat ipc device-config dict (all keys as strings)."""
        resp = await self._send_request({"command": "GetDeviceConfig", "ipc": True})
        device_config = _response_data(resp).get("deviceConfig", {})
        if not isinstance(device_config, dict):
            return {}
        ipc = device_config.get("ipc", {})
        return cast("dict[str, str]", ipc if isinstance(ipc, dict) else {})

    async def set_device_config(self, values: dict[str, str]) -> None:
        """Write one or more flat ipc device-config keys.

        Format confirmed from layout.js: ipc flag at request level, flat deviceConfig.
        Raises RuntimeError on device error.
        """
        resp = await self._send_request(
            {"command": "SetDeviceConfig", "ipc": "true", "deviceConfig": values},
        )
        self._raise_for_error(resp, "SetDeviceConfig")

    async def set_location(self, lat: float, lon: float) -> None:
        """Update the stored device location."""
        await self.set_device_config({"Latitude": f"{lat:.6f}", "Longitude": f"{lon:.6f}"})

    async def get_ui_configuration(self) -> list:
        """Return the GPA UI configuration (list of channel/function objects)."""
        resp = await self._send_request({"command": "GetUIConfiguration"})
        self._raise_for_error(resp, "GetUIConfiguration")
        config = _response_data(resp).get("config", [])
        return config if isinstance(config, list) else []

    async def set_ui_configuration(self, config: list) -> None:
        """Write GPA UI configuration to the device."""
        resp = await self._send_request({"command": "SetUIConfiguration", "config": config})
        self._raise_for_error(resp, "SetUIConfiguration")

    async def restart(self) -> None:
        """Restart the device via GDS."""
        await self._send_message({"command": "Restart", "type": "Device"})

    async def factory_reset(self) -> None:
        """Reset the device via GDS."""
        await self._send_message({"command": "Restart", "type": "FactoryReset"})

    async def next_event(self, *, timeout: float | None = None) -> dict[str, object]:
        """Return the next unmatched GDS push message."""
        try:
            item = await asyncio.wait_for(self._events.get(), timeout=timeout)
        except TimeoutError as exc:
            msg = "No GDS event received before the deadline"
            raise OperationTimeoutError(msg) from exc
        if item is None:
            msg = "GDS connection closed"
            raise TransportError(msg)
        if isinstance(item, BaseException):
            raise item
        return item

    async def listen(self) -> AsyncIterator[dict[str, object]]:
        """Async generator: yield parsed messages from the GDS push stream.

        Call GetProcessView first to subscribe to all datapoint change events.
        Runs until the connection closes or the caller cancels.
        """
        # Subscribes and returns current state; the device then pushes future changes.
        await self._send_request({"command": "GetProcessView", "ui": "true", "cached": "false"})
        while True:
            yield await self.next_event()

    def _find_urn(
        self,
        process_view: dict,
        name_fragment: str,
        *,
        prefix: str | None = None,
    ) -> str | None:
        """Walk process view response to find a URN by fragment and optional prefix."""

        def walk(obj: object) -> str | None:
            if isinstance(obj, dict):
                urn = obj.get("urn", "")
                if name_fragment in urn and (prefix is None or urn.startswith(prefix)):
                    return urn
                for v in obj.values():
                    result = walk(v)
                    if result:
                        return result
            elif isinstance(obj, list):
                for item in obj:
                    result = walk(item)
                    if result:
                        return result
            return None

        return walk(process_view)

    async def configure_tks(self, tks_ip: str, tks_user: str, tks_pass: str) -> None:
        """Configure TKS credentials and trigger a reconnect.

        Uses fixed G1 channel URN and datapoint ID — no ETS project required.
        """
        # Channel URN and trigger ID from g1_device.xml (see get_tks_status for derivation).
        await self.set_configuration(
            "urn:gds:chn:GIG1LXKXIP:Connect",
            {"IpAddress": tks_ip, "Username": tks_user, "Password": tks_pass},
        )
        # Trigger reconnect: pulse Connect datapoint 0→1 (write-only, ID 500002)
        await self._send_request({"command": "SetValue", "id": "500002", "value": "0"})
        await asyncio.sleep(2.0)
        await self._send_request({"command": "SetValue", "id": "500002", "value": "1"})


def run_gds(  # noqa: PLR0913 - mirrors explicit GdsClient connection options
    host: str,
    username: str,
    password: str,
    coro: Callable[[GdsClient], Awaitable[T]],
    timeout: float = 15.0,
    *,
    verify_tls: bool = False,
    ssl_context: ssl.SSLContext | None = None,
) -> T:
    """Run an async coroutine that receives a connected GdsClient."""

    async def _inner() -> T:
        client = GdsClient(
            host,
            username,
            password,
            timeout=timeout,
            verify_tls=verify_tls,
            ssl_context=ssl_context,
        )
        async with client:
            return await coro(client)

    return asyncio.run(_inner())
