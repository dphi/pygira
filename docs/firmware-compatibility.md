# Firmware compatibility

pygira is developed from sanitized hardware observations and firmware analysis. A feature is
only considered **confirmed** when it has been exercised on hardware. Firmware outside the
listed versions may work but should be treated as unverified until reported by a user or added
as a protocol contract.

| Capability | Gira G1 | Gira X1 | Gira TKS-IP | Status |
| --- | --- | --- | --- | --- |
| Device detection and information | 3.5.63 | 2.8.874.0 | 05.04.00.08 | Confirmed |
| Network and NTP inspection | 3.5.63 | 2.8.874.0 | 05.04.00.08 | Confirmed |
| Network and NTP configuration | 3.5.63 | 2.8.874.0 | Not supported | Confirmed on G1/X1 |
| Log download | 3.5.63 | 2.8.874.0 | 05.04.00.08 | Confirmed |
| Passive diagnostics/status | 3.5.63 | 2.8.874.0 | 05.04.00.08 | Confirmed |
| Local firmware update | 3.5.63 | 2.8.874.0 | 05.04.00.08 | Beta |
| Online firmware check/update | 3.5.63 | 2.8.874.0 | Not supported | Beta on G1/X1 |
| SIP-client inspection | Not applicable | Not applicable | 05.04.00.08 | Confirmed |
| Configuration backup/restore | Not exposed | Not exposed | 05.04.00.08 | Beta |
| GDS weather and TKS configuration | 3.5.63 | Not supported | Not applicable | Confirmed on G1 |
| X1 program export and import | Not supported | 2.8.874.0 | Not applicable | Experimental |

## Evidence levels

- **Confirmed**: exercised against hardware and represented by tests or sanitized fixtures.
- **Beta**: protocol is understood, but failure and firmware compatibility coverage is limited.
- **Experimental**: behavior may be incomplete, destructive, or firmware-specific.
- **Inferred**: derived from firmware or web UI analysis without a successful hardware operation.

Contract fixtures live under `tests/contracts/<device>/<firmware>/`. Bug reports should include
the device family, firmware version, command, and a redacted response. Never attach credentials,
tokens, backups, full diagnostic archives, serial numbers, or private addresses.
