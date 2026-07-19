# Firmware protocol contracts

Contract fixtures contain sanitized responses observed on specific device firmware. Keep the
original field names and response envelope intact, replace all credentials and identifiers,
and use documentation-only address ranges such as `192.0.2.0/24`.

Each fixture records whether it came from hardware, firmware analysis, or an inferred response.
Never add raw logs, backups, tokens, serial numbers, MAC addresses, or private network details.
