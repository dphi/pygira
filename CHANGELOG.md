# Changelog

All notable changes to pygira are documented here. The project follows
[Semantic Versioning](https://semver.org/) while its public API evolves toward 1.0.

## Unreleased

### Added

- Define the supported package-root library API and ship PEP 561 typing metadata.
- Add normalized `DeviceInfo` and `FirmwareStatus` models backed by firmware contract fixtures.
- Add `pygira --version` and installed-wheel smoke checks for CI and publishing.
- Test both lowest-direct and latest dependency resolution outside the lockfile.
- Add caller-configurable GDS TLS verification and async context-manager support.
- Add a concurrent GDS response dispatcher and push-event queue, and expose `GdsClient`
  through the supported package-root API.
- Document confirmed firmware compatibility and protocol evidence levels.

### Changed

- Add structured public exceptions for authentication, transport, protocol, timeout,
  unsupported-capability, device-detection, and device API failures.
- Translate expected library failures once at the top-level CLI boundary without hiding
  unexpected programming errors.
- Validate configuration files strictly and write credentials atomically with private permissions.
- Raise the lxml floor to the first verified portable release for supported platforms.

### Fixed

- Preserve default and configured usernames across all CLI commands.
- Redact GDS authentication tokens from connection errors.
- Raise Click exceptions from CLI helpers instead of terminating the Python process.
