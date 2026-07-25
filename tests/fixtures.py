"""
Shared test fixtures built from real firmware data.
"""

# ── configurationservice XML ──────────────────────────────────────────────────
# Shape matches the GPA device template in:
# opt/fwu/system_offline/gira/opt/gira/etc/devicestack/etsconfig/templates/gpa/device.template

DEVICE_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<conf:Device xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
             xmlns:conf="http://service.schema.gira.de/configuration">
  <conf:EntityId>abc123</conf:EntityId>
  <conf:EntityName>G1-Test</conf:EntityName>
  <conf:LogicalName>Living Room Panel</conf:LogicalName>
  <conf:FirmwareVersion GpaOnly="true">3.5.62.0</conf:FirmwareVersion>
  <conf:MacAddress GpaOnly="true">AA:BB:CC:DD:EE:FF</conf:MacAddress>
  <conf:DHCP GpaOnly="true">false</conf:DHCP>
  <conf:IpAddress GpaOnly="true">192.168.1.100</conf:IpAddress>
  <conf:SubnetMask GpaOnly="true">255.255.255.0</conf:SubnetMask>
  <conf:DefaultGateway GpaOnly="true">192.168.1.1</conf:DefaultGateway>
  <conf:PrimaryDNS GpaOnly="true">8.8.8.8</conf:PrimaryDNS>
  <conf:SecondaryDNS GpaOnly="true">8.8.4.4</conf:SecondaryDNS>
  <conf:IpConfigWasSet GpaOnly="true">true</conf:IpConfigWasSet>
  <conf:DeviceType>GIG1LXKXIP</conf:DeviceType>
  <conf:DeviceId>device-001</conf:DeviceId>
</conf:Device>
"""

# ── MeteoGroup weather API response ───────────────────────────────────────────
# Shape from opt/fwu/system_offline/gira/opt/gira/share/devicestack/web/demo/data/demoData.json
# and the ProxyDataService in layout.js

WEATHER_COUNTRIES_RESPONSE = {
    "land": [
        {"landname": "Deutschland", "land_id": "mg-49", "iso2lc": "DE"},
        {"landname": "Denmark", "land_id": "mg-45", "iso2lc": "DK"},
    ],
}

WEATHER_STATIONS_RESPONSE = {
    "ort": [
        {
            "ort_id": "mg-18220678",
            "ortsname": "Radevormwald",
            "region": "Nordrhein-Westfalen",
            "land": "Deutschland",
        },
        {
            "ort_id": "mg-18220679",
            "ortsname": "Radevormwald-Süd",
            "region": "Nordrhein-Westfalen",
            "land": "Deutschland",
        },
    ],
}

WEATHER_STATIONS_EMPTY = {"ort": []}

# ── GDS WebSocket messages ────────────────────────────────────────────────────
# Format from layout.js: {"request": {...}} / {"response": {"request": {...}, ...}}


def gds_register_response() -> dict:
    """Registration acknowledgement from devicestack."""
    return {
        "response": {
            "request": {"command": "RegisterApplication"},
            "result": "ok",
        },
    }


def gds_set_app_value_response(app_name: str, key: str) -> dict:
    return {
        "response": {
            "request": {"command": "SetAppValue", "appName": app_name, "key": key},
            "result": "ok",
        },
    }


def gds_get_app_value_response(app_name: str, key: str, value: str) -> dict:
    return {
        "response": {
            "request": {"command": "GetAppValue", "appName": app_name, "key": key},
            "value": value,
        },
    }


def gds_process_view_response(device_id: str = "device-001") -> dict:
    """
    Process view response containing DCS channel URNs.
    URN patterns from g1_device.xml:
      DcsVHsGUI.Connection channel at StartId=501010 region,
      handled by tks_ip_gw_proxy field handler.
    """
    return {
        "response": {
            "request": {"command": "GetProcessView"},
            "channels": [
                {
                    "urn": f"urn:gds:chn:{device_id}:DcsVHsGUI.Connection",
                    "datapoints": [
                        {"urn": f"urn:gds:dp:{device_id}:DcsVHsGUI.Connection:Connect"},
                        {"urn": f"urn:gds:dp:{device_id}:DcsVHsGUI.Connection:State"},
                    ],
                },
                {
                    "urn": f"urn:gds:chn:{device_id}:DisplayG1",
                    "datapoints": [
                        {"urn": f"urn:gds:dp:{device_id}:DisplayG1:Brightness"},
                    ],
                },
            ],
        },
    }


def gds_set_configuration_response(urn: str) -> dict:
    return {
        "response": {
            "request": {"command": "SetConfiguration", "object": {"urn": urn}},
            "result": "ok",
        },
    }


def gds_set_value_response(urn: str) -> dict:
    return {
        "response": {
            "request": {"command": "SetValue", "id": urn},
            "result": "ok",
        },
    }


def gds_restart_response() -> dict:
    return {
        "response": {
            "request": {"command": "Restart"},
            "result": "ok",
        },
    }


# ── iscwebservice API responses ───────────────────────────────────────────────
# Command responses from /api endpoint (iscwebservice port 1080)

FIRMWARE_INFO_ONLINE_RESPONSE = {
    "state": "available",
    "version": "3.5.63.0",
    "downloadUrl": "https://download.gira.de/software/G1/linux/Gira-G1-3.5.63.zip",
}

FIRMWARE_INFO_ONLINE_NONE = {
    "state": "upToDate",
    "version": "3.5.62.0",
}

FIRMWARE_PROGRESS_RESPONSE = {
    "state": "done",
    "progress": 100,
}

CONTROL_SERVICE_OK = {"result": "ok"}
COMMISSIONING_TEST_RESPONSE = {"state": "ok", "commissioningMode": True}

# ── TKS-IP gateway web app (port 8080) ────────────────────────────────────────
# Trimmed from a real HAR capture of the login + System page (2026-07-04).
# Widget ids (cNN) are session-assigned and NOT stable; the CSS classes are.

TKS_ROOT_HTML = """\
<html><head><script>$(document).ready(function() {\
decodeCommand(0,6,"287aca0a-9de4-4cc1-9028-8471048eb545",0);});</script></head><body></body></html>
"""

TKS_LOGIN_HTML = """\
<div class="lLDCName"><div id="c61">
  <div class="ui-textbox-center"><input type="text" /></div>
</div></div>
<div class="lLDCPassword"><div id="c62">
  <div class="ui-textbox-center"><input type="password" /></div>
</div></div>
<div class="lLLoginButton"><div id="c66"><button><span>Anmelden</span></button></div></div>
"""

TKS_SYSTEM_HTML = """\
<div class="aBSaveButton"><div id="c104"><button>Sichern</button></div></div>
<div class="aBRestoreButton"><div id="c107"><button>Wiederherstellen</button></div></div>
<div class="aUSUpdateButton"><div id="c114"><button>Aktualisieren</button></div></div>
"""

# Trimmed from the Übersicht (Overview) menu's Administration group row —
# these <a> links share no distinguishing CSS class, only label text, and all
# converge on the combined Administration assistant page (2026-07-04 HAR).
TKS_OVERVIEW_HTML = """\
<div id="i112" class="mainMenu"><div id="i113" class="mmTable"><table id="c67">
<tr id="e13" class="ui-single"><td class="mmTableEntry cell0"><div class="ui-label">
<a id="l5"><span class="ui-text-style-default">Sicherung / Wiederherstellung</span></a>
<a id="l6"><span class="ui-text-style-default">Update</span></a>
<a id="l7"><span class="ui-text-style-default">Zugangsdaten</span></a>
<a id="l8"><span class="ui-text-style-default">Geräteinfos</span></a>
<a id="l9"><span class="ui-text-style-default">Nutzungsbedingungen</span></a>
<a id="l10"><span class="ui-text-style-default">Datum und Uhrzeit</span></a>
</div></td></tr>
</table></div></div>
"""

# Trimmed from the Administration assistant's Geräteinfos panel; values are
# placeholders, not the captured device's real MAC/bus address.
TKS_DEVICE_INFO_HTML = """\
<div id="e15"><span>Informationen zur Software</span></div>\
<div class="aDICEntry"><div class="aDICEContent"><table><tr>\
<td class="aDICECName"><div class="ui-label"><span>Software-Version:</span></div></td>\
<td class="aDICECValue"><div class="ui-label"><span>05.04.00.08</span></div></td>\
</tr></table></div>\
<div class="aDICEContent"><table><tr>\
<td class="aDICECName"><div class="ui-label"><span>WebGUI-Version:</span></div></td>\
<td class="aDICECValue"><div class="ui-label"><span>3.03 - RC104</span></div></td>\
</tr></table></div></div>\
<div id="e16"><span>Informationen zur Hardware</span></div>\
<div class="aDICEntry"><div class="aDICEContent"><table><tr>\
<td class="aDICECName"><div class="ui-label"><span>MAC-Adresse:</span></div></td>\
<td class="aDICECValue"><div class="ui-label"><span>AA:BB:CC:DD:EE:FF</span></div></td>\
</tr></table></div>\
<div class="aDICEContent"><table><tr>\
<td class="aDICECName"><div class="ui-label"><span>Busadresse:</span></div></td>\
<td class="aDICECValue"><div class="ui-label"><span>0xEA81DF</span></div></td>\
</tr></table></div></div>\
"""
