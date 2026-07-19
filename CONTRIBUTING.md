# Contributing

## Development setup

This project uses `uv` for dependency management and command execution. Supported Python versions are 3.10 through 3.14, and CI runs the full quality gate on each supported version.

```bash
uv sync --group dev
uv run pre-commit install
```

Device credentials live in `devices.toml`, which is intentionally ignored by Git. Use `uv run pygira config init`, `uv run pygira config add-device ...`, and `uv run pygira config validate` to manage local device entries. `devices.toml.example` shows the direct-device and optional-location TOML shape.

## Required checks

Every commit should be clean for linting, formatting, type checking, tests, and diff coverage.
The local pre-commit hooks run the same gates that CI runs:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
uv run diff-cover coverage.xml --compare-branch HEAD --fail-under=80
```

`pytest` writes `coverage.xml`; run it before `diff-cover` if you invoke the commands manually.
Overall coverage must stay at or above the configured floor. At least 80% of new or changed
executable lines must be covered. Prefer higher coverage for authentication, configuration,
and destructive device operations.

## Adding a command

All commands follow the same pattern: decorate with `@common_options`, call
`resolve_login()` first, construct `ApiClient` with `profile.api_prefix`, and
wrap the body in `try/except Exception as e: die(e)`. Commands are registered
via `register(main)` in each file under `src/pygira/commands/` and wired in
`cli.py`.

`resolve_login()` pulls credentials from `devices.toml` when `--name` is
given, or prompts interactively. G1-only features must call
`require_capability()` before using the GDS transport.

## Test infrastructure

Tests use the custom HTTP mock in `tests/_httpmock.py` (not respx/responses —
those only intercept httpx; this project uses a stdlib-based `_http.py` shim).
Use the `@mock` decorator or `with mock:` context, then register routes with
`respx.get(url)`, `respx.post(url)`, etc. Shared fixture data lives in
`tests/fixtures.py`.

Sanitized firmware-specific response contracts live under
`tests/contracts/<device>/<firmware>/`. Preserve the original protocol field names and envelope,
record the evidence source, and replace all identifiers with documentation-only values. Never
commit credentials, tokens, serial numbers, backups, raw logs, or private network details.

## Pull requests

- Keep pull requests focused and small enough to review.
- Add or update tests for changed behavior.
- Do not commit credentials, firmware dumps, local device IPs, or generated cache files.
- Wait for the GitHub Actions CI workflow to pass before merging.
