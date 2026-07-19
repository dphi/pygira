# Project TODO

## Protocol stabilization

- [ ] Confirm X1 program export and import behavior against each supported firmware family.
- [ ] Add explicit schema validation and compatibility metadata to exported X1 programs.
- [ ] Extend caller-provided CA support to configurationservice and add fingerprint pinning.

## Library evolution

- [ ] Add typed response models for diagnostics, TKS status, and remaining stable payloads.
- [ ] Add an async device facade and keep synchronous wrappers separate.
- [ ] Route GDS responses and push events through a single reader task and event queue.
- [ ] Consolidate duplicate session-authentication implementations.

## Validation and quality

- [ ] Add opt-in hardware integration tests with credential-safe fixtures.
- [ ] Increase overall branch coverage, prioritizing CLI failure and timeout paths.
- [ ] Validate distribution metadata and source archives with a dedicated packaging checker.
