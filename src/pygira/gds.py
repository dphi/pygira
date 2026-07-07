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
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar, cast

import websockets

T = TypeVar("T")

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection


def _make_url(host: str, username: str, password: str) -> str:
    token = "ui" + base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"wss://{host}:4432/gds/api?{token}"


def _make_ssl() -> ssl.SSLContext:
    # ponytail: Gira CA cert is not public; device certs are self-signed by a private CA.
    # Accept any cert — connections are local-network only.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _response_data(resp: dict[str, object]) -> dict[str, object]:
    value = resp.get("response", {})
    return value if isinstance(value, dict) else {}


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
    """GDS WebSocket session for a single host (port 4432, WSS)."""

    def __init__(self, host: str, username: str, password: str, timeout: float = 15.0) -> None:
        """Initialize without connecting; call connect() to open the session."""
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout
        self._ws: ClientConnection | None = None

    async def connect(self) -> None:
        """Open WebSocket connection and register application."""
        url = _make_url(self.host, self.username, self.password)
        try:
            self._ws = await websockets.connect(
                url,
                ssl=_make_ssl(),
                open_timeout=self.timeout,
            )
        except Exception as exc:
            # Strip the credential token from the URL in any exception message.
            safe = f"wss://{self.host}:4432/gds/api?<token>"
            raise type(exc)(str(exc).replace(url, safe)) from None
        await self._register()

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
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _send_request(self, payload: dict[str, object]) -> dict[str, object]:
        assert self._ws is not None
        raw = json.dumps({"request": payload})
        await self._ws.send(raw)
        # read until we get a response that matches our command
        command = payload.get("command", "")
        deadline = asyncio.get_event_loop().time() + self.timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                msg = f"No response for GDS command {command!r}"
                raise TimeoutError(msg)
            raw_resp = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            resp = json.loads(raw_resp)
            req_echo = cast("dict[str, object]", _response_data(resp).get("request", {}))
            if req_echo.get("command") == command:
                return resp
            # not our response — discard and keep reading

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
        msg = f"{command} failed: {text}{detail}"
        raise RuntimeError(msg)

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
        assert self._ws is not None
        await self._ws.send(json.dumps({"request": {"command": "Restart", "type": "Device"}}))

    async def factory_reset(self) -> None:
        """Reset the device via GDS."""
        assert self._ws is not None
        raw = json.dumps({"request": {"command": "Restart", "type": "FactoryReset"}})
        await self._ws.send(raw)

    async def listen(self) -> AsyncIterator[dict[str, object]]:
        """Async generator: yield parsed messages from the GDS push stream.

        Call GetProcessView first to subscribe to all datapoint change events.
        Runs until the connection closes or the caller cancels.
        """
        assert self._ws is not None
        # Subscribes and returns current state; the device then pushes future changes.
        await self._send_request({"command": "GetProcessView", "ui": "true", "cached": "false"})
        async for raw in self._ws:
            yield json.loads(raw)

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


def run_gds(
    host: str,
    username: str,
    password: str,
    coro: Callable[[GdsClient], Awaitable[T]],
    timeout: float = 15.0,
) -> T:
    """Run an async coroutine that receives a connected GdsClient."""

    async def _inner() -> T:
        client = GdsClient(host, username, password, timeout=timeout)
        await client.connect()
        try:
            return await coro(client)
        finally:
            await client.close()

    return asyncio.run(_inner())
