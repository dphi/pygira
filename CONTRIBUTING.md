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

All commands follow the same pattern: decorate with `@common_options`, resolve
the target through `commands._target.resolve_device()`, and invoke the public
`G1`/`X1` facade. Commands must not construct transport clients directly.
Let expected `PygiraError` failures reach the top-level CLI boundary. Use
`click.UsageError` or `click.BadParameter` for invalid command input; do not
broadly catch programming errors. Commands are registered via `register(main)`
in each file under `src/pygira/commands/` and wired in `cli.py`.

`resolve_login()` pulls credentials from `devices.toml` when `--name` is
given, or prompts interactively. G1-only features must call
`require_capability()` before using the GDS transport.

## Hardware smoke tests

Hardware tests are read-only and disabled unless explicitly enabled. Never put
device credentials in fixtures, command history, test IDs, or failure messages.
Configure them through local environment variables:

```bash
PYGIRA_HARDWARE_TESTS=1 \
PYGIRA_HARDWARE_DEVICE=g1 \
PYGIRA_HARDWARE_HOST=192.168.1.240 \
PYGIRA_HARDWARE_USERNAME=device \
PYGIRA_HARDWARE_PASSWORD='...' \
uv run pytest --no-cov -m hardware tests/integration
```

The credential-free TKS-IP bootstrap/status probe uses the same opt-in with
`PYGIRA_HARDWARE_DEVICE=tks-ip`; only `PYGIRA_HARDWARE_HOST` is required.

The default test suite collects these tests but skips them before any network
connection. Keep hardware tests non-destructive unless a separate marker and
an additional explicit opt-in safeguard are introduced.

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
