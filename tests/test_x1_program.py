"""Tests for x1-export-program and x1-import-program CLI commands."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from pygira.cli import main
from pygira.devices.g1 import PROFILE as G1_PROFILE
from pygira.devices.x1 import PROFILE as X1_PROFILE
from pygira.gds import GdsClient

HOST = "192.168.1.50"
USER = "device"
PASS = "secret"

_CREDS = ["--ip", HOST, "--username", USER, "--password", PASS]

FAKE_CONFIG = [
    {"guid": "00000000-0000-0000-0000-000000000001"},
    {
        "name": "Deckenlampe",
        "channelType": "de.gira.schema.channels.Switch",
        "functionType": "de.gira.schema.functions.Switch",
        "channelViewID": 1,
        "dataPoints": [{"dataPoint": "OnOff", "id": 150001}],
        "parameters": [],
        "iconID": 1,
        "switchTimeChannelID": 30000,
        "switchTimes": [],
    },
]


def _fake_login_x1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "pygira.commands.maintenance.resolve_login",
        lambda ip, u, p: (X1_PROFILE, ip, u, p),
    )


def _fake_login_g1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "pygira.commands.maintenance.resolve_login",
        lambda ip, u, p: (G1_PROFILE, ip, u, p),
    )


# ── x1-export-program ─────────────────────────────────────────────────────────


def test_export_rejects_non_x1(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_login_g1(monkeypatch)
    result = CliRunner().invoke(main, ["x1-export-program", *_CREDS])
    assert result.exit_code != 0 or "X1 only" in result.output


def test_export_saves_json_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_login_x1(monkeypatch)
    out = tmp_path / "program.json"

    monkeypatch.setattr(
        "pygira.commands.maintenance.run_gds",
        lambda host, user, pw, coro, timeout: FAKE_CONFIG,
    )

    result = CliRunner().invoke(main, ["x1-export-program", *_CREDS, "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text()) == FAKE_CONFIG
    assert "saved" in result.output


def test_export_default_filename_is_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_login_x1(monkeypatch)
    monkeypatch.setattr(
        "pygira.commands.maintenance.run_gds",
        lambda host, user, pw, coro, timeout: FAKE_CONFIG,
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["x1-export-program", *_CREDS])
    assert result.exit_code == 0, result.output
    assert ".json" in result.output


# ── x1-import-program ─────────────────────────────────────────────────────────


def test_import_rejects_non_x1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_login_g1(monkeypatch)
    f = tmp_path / "prog.json"
    f.write_text(json.dumps(FAKE_CONFIG))
    result = CliRunner().invoke(main, ["x1-import-program", *_CREDS, "--confirm", str(f)])
    assert result.exit_code != 0 or "X1 only" in result.output


def test_import_aborts_without_confirm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_login_x1(monkeypatch)
    f = tmp_path / "prog.json"
    f.write_text(json.dumps(FAKE_CONFIG))
    result = CliRunner().invoke(main, ["x1-import-program", *_CREDS, str(f)], input="n\n")
    assert result.exit_code != 0


def test_import_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_login_x1(monkeypatch)
    program = tmp_path / "invalid.json"
    program.write_text("{not-json")

    result = CliRunner().invoke(
        main,
        ["x1-import-program", *_CREDS, "--confirm", str(program)],
    )

    assert result.exit_code != 0
    assert "invalid program JSON" in result.output


def test_import_calls_set_ui_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_login_x1(monkeypatch)
    f = tmp_path / "prog.json"
    f.write_text(json.dumps(FAKE_CONFIG))
    calls = []

    def fake_run_gds(
        host: str,
        user: str,
        pw: str,
        coro: Callable[[GdsClient], Awaitable[None]],
        timeout: float,
    ) -> None:
        calls.append(("run_gds", host))

    monkeypatch.setattr("pygira.commands.maintenance.run_gds", fake_run_gds)

    result = CliRunner().invoke(main, ["x1-import-program", *_CREDS, "--confirm", str(f)])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert "applied" in result.output
