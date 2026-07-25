# Changelog

All notable changes to pygira are documented here. The project follows
[Semantic Versioning](https://semver.org/) while its public API evolves toward 1.0.

## Unreleased

## 0.1.0 - 2026-07-25

### Added

- Define the supported package-root library API and ship PEP 561 typing metadata.
- Add normalized `DeviceInfo` and `FirmwareStatus` models backed by firmware contract fixtures.
- Add normalized diagnostic-page and TKS connection-status models.
- Add TKS-IP auto-detection, AES-encrypted diagnostic log downloads, and normalized
  non-web health inspection.
- Add read-only TKS-IP device, date/time, network, SIP-client, and incoming-call inspection.
- Add TKS-IP configuration backup, restore, and firmware-update operations.
- Add keyboard-driven configured-device selection with search, direct IP entry, and automatic
  selection when only one compatible option remains.
- Add a device compatibility matrix to CLI help and `command-support`.
- Add `pygira --version` and installed-wheel smoke checks for CI and publishing.
- Test both lowest-direct and latest dependency resolution outside the lockfile.
- Add caller-configurable GDS TLS verification and async context-manager support.
- Add a concurrent GDS response dispatcher and push-event queue, and expose `GdsClient`
  through the supported package-root API.
- Document confirmed firmware compatibility and protocol evidence levels.
- Add configuration-service private-CA support and SHA-256 certificate pinning.
- Add opt-in, read-only hardware smoke-test infrastructure with environment-only credentials.

### Changed

- Organize the CLI around resource-first command groups such as `device info`, `network get`,
  `network set`, `tks info`, and `tks sip info`, while preserving compatibility aliases.
- Unify G1, X1, and TKS-IP log commands and normalize G1/X1 logging-level presentation.
- Accept both modern named-device configuration and legacy apartment-based configuration.
- Add structured public exceptions for authentication, transport, protocol, timeout,
  unsupported-capability, device-detection, and device API failures.
- Translate expected library failures once at the top-level CLI boundary without hiding
  unexpected programming errors.
- Consolidate G1 and X1 cookie-session authentication and token derivation.
- Remove broad exception wrappers from legacy CLI commands and substantially expand command
  behavior coverage.
- Validate configuration files strictly and write credentials atomically with private permissions.
- Raise the lxml floor to the first verified portable release for supported platforms.

### Fixed

- Persist and safely recover TKS-IP web sessions, including expired-session HTTP 404 responses
  and transient web-assistant startup races.
- Keep validation failures useful without exposing credential-bearing configuration values.
- Preserve default and configured usernames across all CLI commands.
- Redact GDS authentication tokens from connection errors.
- Raise Click exceptions from CLI helpers instead of terminating the Python process.
- Normalize remaining HTTP, TKS-IP, firmware, and configuration-service failures into the
  public exception hierarchy.
