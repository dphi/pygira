# Project TODO

## Protocol stabilization

- [ ] Confirm X1 program export and import behavior against each supported firmware family.
- [ ] Add explicit schema validation and compatibility metadata to exported X1 programs.

## TKS-IP research

- [ ] Reverse-engineer the outbound HomeServer API with a passive-first local mock: preserve a
  configuration backup, avoid replacing an active integration, document framing,
  authentication/AES, keepalives, retries, and event semantics, then evaluate a read-only camera
  relay. Do not send protocol commands until their effects are understood.

## Library evolution

- [ ] Add typed response models for remaining stable payloads.
- [ ] Add an async device facade and keep synchronous wrappers separate.

## Validation and quality

- [ ] Continue increasing branch coverage for diagnostic rendering and timeout edge cases.
- [ ] Validate distribution metadata and source archives with a dedicated packaging checker.
