# Project TODO

## X1 Programming Workflows

- [ ] Add `x1-export-program` command to export X1 programming/project data.
- [ ] Implement programming mode handling for export (`setProgrammingMode` with mode support).
- [ ] Implement `getDownloadLink` flow and artifact download handling for X1 export.
- [ ] Add robust output naming/path handling for exported X1 project artifacts.

- [ ] Add `x1-import-program` (re-program) command.
- [ ] Implement upload/apply pipeline for X1 project import (experimental first).
- [ ] Add strict safety checks and explicit warnings around re-program operations.
- [ ] Add response diagnostics for unsupported/unknown X1 re-program API behavior.

## Validation and Quality

- [ ] Add integration-style tests (mocked transport) for X1 export workflow.
- [ ] Add guarded tests for experimental X1 re-program behavior and failure paths.
- [ ] Document supported/experimental status of X1 programming features in `README.md`.
