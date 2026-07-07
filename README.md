# pygira

[![PyPI](https://img.shields.io/pypi/v/pygira)](https://pypi.org/project/pygira/)
[![CI](https://github.com/dphi/pygira/actions/workflows/ci.yml/badge.svg)](https://github.com/dphi/pygira/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](LICENSE)

`pygira` is a provisioning CLI for Gira G1 and X1 devices.

## Installation

```bash
pip install pygira
# or as a standalone tool
uv tool install pygira
```

## Supported device families

- `g1`
- `x1`

## Device selection

- By default, `pygira` auto-detects the device type.
- You can enforce a type with `--device {g1,x1}`.
- If `--device` does not match the detected device type, the command fails immediately.

## Command support

- Shared: info, network configuration, logs, firmware update, restart, factory reset, SSH control.
- G1-only: weather and TKS configuration.

## Device configuration

`devices.toml` (not committed) holds per-apartment credentials. See `devices.toml.example` for shape.
Create and manage it with the config commands instead of hand-editing when possible:

```bash
pygira config init
pygira config add-device living_room_g1 --type g1 --host 192.168.1.240
pygira config add-device controller --type x1 --host 192.168.1.241 --location home
pygira config validate
pygira config list
```

Use named devices directly:

```bash
pygira --name living_room_g1 info
pygira --location home --name controller info
```

Locations are optional grouping only; devices can live directly under `[devices.<name>]`.

## Development

```bash
uv run pygira [options] <command>    # run the CLI
uv run pytest                        # run all tests
uv run pytest tests/test_api.py      # run a single test file
uv run pytest -k test_name           # run a single test by name
uv run ruff check src/ tests/        # lint
uv run mypy src/                     # type-check
```

All source files must be ruff-clean. Run `uv run ruff check src/ tests/` before finishing any edit and fix all reported errors. Pre-existing violations in unchanged lines are acceptable to leave, but any line you touch or add must be clean.

Supported Python versions are 3.10 through 3.14. CI runs the full quality gate on each supported version. See [CONTRIBUTING.md](CONTRIBUTING.md) for the pull-request checklist.

## Architecture

### Two protocols, not one

Every command resolves a `DeviceProfile` (via `context.resolve_profile`) that determines which transport to use:

- **G1** → `api_prefix="/api"`, port 80 (iscwebservice JSON)
- **X1** → `api_prefix="/webservice"`, port 80 (same JSON protocol, different path)

On top of those, two further protocols exist:

| Protocol | Port | File | Used for |
| -------- | ---- | ---- | -------- |
| iscwebservice | 80 HTTP | `api.py` | All standard commands (firmware, logs, NTP, IP, reboot) |
| GDS WebSocket | 4432 WSS | `gds.py` | G1 only: weather + TKS-IP config, factory reset |
| configurationservice | 4433 HTTPS | `config_service.py` | X1 log download, X1 syslog severity; G1 detection fallback only |
| GDS-REST-API | 443 HTTPS `/api` | — | X1 only: Gira IoT/Home App KNX control (not used for provisioning) |
| TKS-IP web app | 8080 HTTP | `tks_web.py` | TKS-IP gateway only: backup save/restore, firmware update — see below |

**TKS-IP gateway web app** (separate physical device, not G1/X1): the on-demand
port-8080 app (`activate-tks-web` starts it) speaks a stateful JSON
command-loop protocol, not a REST API. Key gotcha: login has no dedicated
button/endpoint — it submits when the password field's commit event carries
an extra flag (`["value", id, password, true, true, false]`). Widget ids are
session-random; `tks_web._find_widget_id()` locates controls by their stable
CSS class instead (e.g. `aBSaveButton`, `aUSUpdateButton`).

**X1 GDS WebSocket**: Port 4432 is open on the X1 and accepts connections with the same URL/auth format as the G1 (`wss://<host>:4432/gds/api?ui<base64>`). `RegisterApplication` succeeds. However, the X1 GDS is **event-push only** — it sends live value change events (e.g. clock ticks) after registration but does not respond to any query commands (`GetProcessView`, `GetDeviceConfig`, `GetCurrentUser` all time out). All X1 provisioning commands go through iscwebservice at `/webservice`.

**X1 GDS-REST-API** (port 5522, proxied via port 443 at `/api`): A separate REST server (`/opt/gira/bin/restserver`) exposes the Gira IoT / Home App API. Two versions co-exist: `GET /api/` → `{"info":"GDS-REST-API","version":"2"}`, `GET /api/v1/` → version 1. Client registration via `POST /api/clients {"client": {"name": "...", "type": "..."}}` returns a token; subsequent calls use `?token=<token>` as URL param. `/api/uiconfig?token=<token>` provides the KNX UI configuration for the mobile app. This API is for KNX datapoint control by third-party apps — **not relevant for provisioning**.

### Auth quirks

- `api.py` uses `Basic` (capital B) in the Authorization header.
- `config_service.py` uses `basic` (lowercase) — different binary, different implementation.
- Many G1 commands require **session auth** (getPasswordSalt → doAuthenticateSession → retry). `ApiClient._post` handles this automatically via `_post_with_session_fallback` for errors 220/235. If session auth also fails, it raises `RuntimeError` rather than returning the error dict silently.
- GDS WebSocket uses WSS (TLS, port 4432). Auth is a query-string token: `ui` + base64(`user:password`) appended to the path `/gds/api`. URL form: `wss://<host>:4432/gds/api?ui<base64>`. Gira CA cert is private/not distributed; TLS verification is disabled in `gds.py` (local-network devices only).
- G1 `getDeviceInfo` without `forceLong` is publicly accessible (no auth). With `forceLong: True`, session auth is required.

### Device detection flow (`core/detect.py`)

1. JSON probe → `/webservice` (finds X1)
2. JSON probe → `/api` (finds G1)
3. XML fallback → port 4433 configurationservice

`resolve_device_type` enforces that `--device` matches detected type; mismatches raise immediately.

### Adding a command

All commands follow the same pattern: decorated with `@common_options`, call `resolve_login()` first, construct `ApiClient` with `profile.api_prefix`, wrap body in `try/except Exception as e: die(e)`. Commands are registered via `register(main)` in each file under `commands/` and wired in `cli.py`.

`resolve_login()` pulls credentials from `devices.toml` when `--apartment` is given, or prompts interactively.

### Test infrastructure

Tests use a custom HTTP mock in `tests/_httpmock.py` (not respx/responses — those only intercept httpx; this project uses a stdlib `_http.py` shim). Use `@mock` decorator or `with mock:` context, then register routes with `respx.get(url)`, `respx.post(url)`, etc. Shared fixture data is in `tests/fixtures.py` (built from real firmware data).

## Firmware findings

The following was learned from inspecting the G1, X1, and TKS-IP firmware images. The firmware packages themselves are not distributed with this project.

### Documented findings from firmware inspection

**Both devices run the same `iscwebservice` binary** with different plugin shared objects:
- G1: `libsipwebservice.so` — served at `/api` (nginx proxies `/api` → port 1080)
- X1: `libgds_x1_webservice.so` — nginx rewrites `/webservice` → `/api` before handing to port 1080

This is why both APIs are nearly identical protocol-wise, just at different URL prefixes.

**iscwebservice auth** (`iscwebservice.conf`): `mode="gds"`, `singleUser="true"` (G1) / `changePassword allowed="false"` (X1). Auth is delegated to the GDS layer. `<FWUpdate enabled="false" />` on X1 explains why X1 firmware update uses a different code path.

**GDS_1 password encoding** (`encode-pw.sh`):
```
salt = 32 random alphanumeric chars (static per user, returned by getPasswordSalt)
password_hash = base64(sha256_bytes(password + salt))[:43]
session_token = sha256_upper(password_hash + "+" + sessionSalt)
stored_credential = password_hash + salt   # 75 chars total
```
This is exactly what `ApiClient._compute_auth_token` implements for version `"GDS_1"`. Auth failures (error 220) mean the password in `devices.toml` does not match the device — `getDeviceInfo` without `forceLong` is publicly accessible so the mismatch is only exposed when `forceLong` is required.

**Factory reset**: `{request: {command: "Restart", type: "FactoryReset"}}` — this is a GDS WebSocket command, not an iscwebservice command. There is no confirmed `/api` equivalent.

**TKS channel URNs**: `DcsVHsGUI.Connection` channel at StartId=500001, FieldHandlerName=`tks_ip_gw_proxy`. Channel instance name is "Connect". Datapoints:
- index 0: `Connect` (Binary, write-only) — trigger to connect/disconnect
- index 1: `ConnectionState` (Byte, read+event) — 0=initialising, 1=unregistered, 2=registering, 3=registered, 4=unregistering, 5=connection_lost
- index 2: `DisconnectReason` (Byte, read) — 0=none, 3=wrong_credentials, 4=timeout, 5=license_exceeded, 6=internal_error

URN paths: `<base>:Connect:ConnectionState`, `<base>:Connect:DisconnectReason`, `<base>:Connect:Connect`.

**Fixed GDS IDs (StartId=500001, confirmed live)**:
- 500001: Connect channel — `urn:gds:chn:Gira-G1.GIG1LXKXIP:Connect` (also reachable as `urn:gds:chn:GIG1LXKXIP:Connect`)
- 500002: Connect trigger (write-only) — `urn:gds:dp:Gira-G1.GIG1LXKXIP:Connect:Connect`
- 500003: ConnectionState (read) — live value confirmed (3=registered)
- 500004: DisconnectReason (read)

`gds.py:get_tks_status` uses `GetValue` with IDs 500003/500004 directly — no ETS project or `GetProcessView` needed; works even when process view is empty. `gds.py:configure_tks` uses fixed channel URN `urn:gds:chn:GIG1LXKXIP:Connect` for `SetConfiguration` and ID 500002 for the reconnect pulse. `GetConfiguration` on the channel URN returns `IpAddress`, `Username`, `Password`, `ResetOnGpaCommisioning`. `SetAppValue("dcs.settings", ...)` is dead — the device never reads it back.

**G1 configurationservice (port 4433)**: auth is `Basic` (capital B) with `device` username — opposite of X1 which uses `basic` (lowercase). Known working paths: `GET /discovery/download/logfiles` (returns log ZIP, 80+ KB), `GET /discovery/presentation/` (diagnostic HTML), `GET /discovery/presentation/completely` (full diagnostic), `GET /discovery/systemlog/` (syslog HTML). Port 4433 also runs a UPnP server at port 8080: `GET http://<host>:8080/GiraDeviceDescription.xml` returns device health state (IP, MAC, serial, monitored apps).

**Known iscwebservice commands — G1** (from web UI JS + live probing):
- Public (no auth): `getDeviceInfo` without `forceLong`
- Session auth required: `getDeviceInfo` with `forceLong: true`, `reboot`, `setNtpConfig`, `setIpConfig`, `getLogfile`, `getDiagnosticPage`, `startonlineupdate`, `initlocalupload`, `progress`, `controlService`, `getFirmwareStatus`
- Not implemented on iscwebservice (error 228): `getAppValue`, `getDcsConfig` — these are GDS-only on G1

**X1 iscwebservice commands** (from X1 web UI JS enum + live probing):

Basic auth only (no session required):
- `getDeviceInfo` → 3 fields: `CurrentFirmwareVersion`, `AppName`, `UserManagement`
- `getFirmwareStatus` → `currentVersion`, `isCloning`, `isRebootPending`, `isUpdating`, `offlineVersion`, `progress`, `isDownloading`
- `getDiagnosticPage` → blob with running processes, system/NTP/IP info
- `getSonosChannels` → list of configured Sonos channels
- `getAppValue` (params: `appName`, `key`) → key/value store
- `getLogicEnginePage` → logic engine status (pages, nodes, started/config times)
- `getOpenVpnCertificateValidity` → `enabled`, `notBefore`, `notAfter`
- `resetUploadSlot` → clears pending firmware upload slot
- `setKeepAlive` / `setKeepAliveDuration` (param: `duration`)

Session auth required (getPasswordSalt → doAuthenticateSession first; works on both HTTP port 80 and HTTPS port 443 at `/webservice`):
- `getDeviceInfo` → full 39-field response: `User`, `DebugMode`, `Time`, `StartupTime`, `SdCardPresent/Usage/Size`, `Hostname`, `CurrentFirmwareVersion`, `MacAddress`, `MacAddress1/2`, `Dhcp`, `IpAddress`, `SubnetMask`, `DefaultGateway`, `NameServer`, `Ntp`, `NtpServerAddress`, `NtpInterval`, `AppName`, `EtsDownload`, `DeviceName`, `DeviceId`, `CurrentSystem`, `SerialNumber`, `TimeZoneID`, `SyslogSeverity`, `KIM-LicenceInfo`, `KnxBusVoltage`, `ProgrammingMode`, `ProgrammingMode2`, `MaintenanceRequired`, `UserManagement`, `Knx`
- `getLogfile` → `data.content` base64-encoded log bundle
- `setIpConfig`, `setNtpConfig`, `setSyslogSeverity` (param: `syslogSeverity` 0..4), `setTimeZone`
- `setProgrammingMode`, `reboot`, `factoryReset`
- `doFirmwareUpdate`, `getFirmwareUpdate`, `resetUploadSlot`
- `setAppValue` (params: `appName`, `key`, `value`)
- `getSonosSlots`, `setSonosSlots` — Sonos slot config (getSonosSlots returns ERR_SYSTEM without config)
- `restartLogicEngine`
- Auth flow: `getPasswordSalt`, `doAuthenticateSession`, `doCloseSession`, `getNewPasswordSalt`, `writeNewPasswordHash`, `gdsSetNewPasswordAsHash`

Not implemented on this device (error 228 ERR_COMMUNICATION):
- All SIP: `getSipConfiguration`, `setSipConfiguration`, `getContacts`, `createContact`, `updateContact`, `deleteContact`, `playRingtone`, `getRingtones`, `getButtons`, `setButtons`, `getSipUsers`, `setSipUser`, `deleteSipUser`, `changeSipUserPassword`, `getPortSettings`, `setPortSettings`, `exportSipData`, `importSipData`
- `getDcsConfig`, `getDualNet`, `setDualNet`
- `GetApplicationsInfo`, `GetAppStatusInformation`, `GetTokenConfig`, `SetTokenConfig`
- `getArchives`, `GetRecordingData`, `getDataLoggerArchiveEntries`, `getDataLoggerArchiveFile`, `getDataLoggerLogfileDownloadName`
- `DiagnosticRefreshDeviceConfigurations`, `GetDiagnosticRefreshDeviceConfigurationsState`

`getDownloadLink` — session auth required; returns `{"error": "ERR_CONFIGURATION", "id": "228"}` (not ERR_COMMUNICATION) when no ETS project is stored on the device (`EtsDownload: false` in `getDeviceInfo`). The command IS real and recognized; it just has nothing to serve.

**`getDownloadLink` internals (confirmed from iscwebservice binary):**
1. Handler calls `GetDcsConfig` via GDS IPC and checks `hasDcsProject`
2. `hasDcsProject == false` → returns ERR_CONFIGURATION
3. `hasDcsProject == true` → `DownloadLinkHandler::CreateLink()` creates a symlink at `/webservice/download/<token>` pointing to the stored project file, returns that URL
4. The URL is then GET-able via nginx (which proxies `/webservice/...` to port 1080)

**What sets `EtsDownload: true` / `hasDcsProject: true`:** The Gira ETS plug-in does a "download" operation to the X1 over the KNX bus. `libdscknxappprg::ExecuteDownloadHandler` processes the incoming KNX application program and `libluaknxdownloadhandler::CreateFileWithKnxDataE` stores the project file on-device. Only after that does `getDownloadLink` return a URL.

**Implication for import:** There is no HTTP API path to import/re-program an ETS project. Programming happens KNX-bus-in via the ETS plug-in — `doFirmwareUpdate` is firmware-only. The `x1-import-program` command (upload + `doFirmwareUpdate`) will not perform ETS programming.

X1 running processes (from getDiagnosticPage):
`knxstack.sh`, `mono` (logic engine), `iscwebservice`, `presencesimulation`, `restserver` (port 5522 GDS-REST-API), `usage-statistics-client`, `sonosapp`, `hueapp`

**GDS WebSocket commands**: `RegisterApplication`, `GetProcessView`, `GetAppValue`, `SetAppValue`, `DeleteAppValue`, `GetConfiguration`, `SetConfiguration`, `SetDeviceConfig`, `GetDeviceConfig`, `SetValue`, `GetValue`, `GetUIConfiguration`, `SetUIConfiguration`, `Restart` (+ `type: FactoryReset`), `GetCurrentUser`, `GetUsers`, `EmitEvent`, `WriteLogEntry`

**`GetDeviceConfig`/`SetDeviceConfig` format**: these commands do NOT take a channel URN. The `ipc` flag lives at the request level, and `deviceConfig` is a flat string→string dict:
```json
{"command": "GetDeviceConfig", "ipc": true}
{"command": "SetDeviceConfig", "ipc": "true", "deviceConfig": {"Key": "value", ...}}
```
`GetDeviceConfig` returns all device-level config (~100 keys) under `response.deviceConfig.ipc`. Only some keys are writable via `SetDeviceConfig`; others return error 103. Confirmed writable: `Latitude`, `Longitude` (format `"53.150000"` — 6 decimal places). `gds.py:set_device_config` and `gds.py:set_location` implement this.

### TKS-IP HTTP protocol

Port 8080, activated by `config_service.activate_tks_webinterface`:
- `GET /state?callback=setState` — JSONP polling; response contains `"system.state": "0"` when ready, `"2"` on error
- `GET /json?sid=...&rid=...&data=[...]` — main JSON-RPC bus; `["documentReady"]` is the boot trigger that starts the web interface
- `GET /cam` / `/cam.jpg` / `/camera/cam.jpg` — door camera JPEG stream
- `GET /state` and `POST /json` are served by the embedded `httpd` binary (not nginx)

## Key constraints

- `--device g1|x1` is enforced against auto-detection; a mismatch is a hard error, not a warning.
- G1-only features (weather, TKS-IP) are gated by `DeviceCapabilities.weather` / `.tks` flags on the profile; use `require_capability()` before calling GDS.
- `config_service.py` functions `push_device_xml`/`set_ip_config` (XML-based IP config) are superseded by `ApiClient.set_ip_config` (JSON) — the XML path is not called from any current command.

## License

`pygira` is licensed under the GNU General Public License v3.0 or later (`GPL-3.0-or-later`).
