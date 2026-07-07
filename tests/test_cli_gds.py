from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from pygira.cli import main
from pygira.core.types import DeviceType
from pygira.devices.g1 import PROFILE


class DummyG1:
    def __init__(self) -> None:
        self.calls = []

    def process_view(self) -> dict[str, object]:
        self.calls.append(("process_view",))
        return {"response": {"ok": True}}

    def device_config(self) -> dict[str, str]:
        self.calls.append(("device_config",))
        return {"Latitude": "53.000000"}

    def set_device_config(self, values: dict[str, str]) -> None:
        self.calls.append(("set_device_config", values))

    def app_value(self, app_name: str, key: str) -> str:
        self.calls.append(("app_value", app_name, key))
        return "value"

    def set_app_value(self, app_name: str, key: str, value: str) -> None:
        self.calls.append(("set_app_value", app_name, key, value))

    def set_location(self, lat: float, lon: float) -> None:
        self.calls.append(("set_location", lat, lon))

    def tks_status(self) -> dict[str, object]:
        self.calls.append(("tks_status",))
        return {"present": True, "value": "1"}

    def configure_tks(self, tks_ip: str, tks_user: str, tks_pass: str) -> None:
        self.calls.append(("configure_tks", tks_ip, tks_user, tks_pass))

    def restart(self) -> None:
        self.calls.append(("restart",))

    def factory_reset(self) -> None:
        self.calls.append(("factory_reset",))


def _patch_gds_login(
    monkeypatch: pytest.MonkeyPatch,
    profile: object = PROFILE,
) -> tuple[DummyG1, dict[str, object]]:
    dummy = DummyG1()
    created = {}

    def fake_login(
        ip: str | None,
        username: str | None,
        password: str | None,
    ) -> tuple[object, str | None, str | None, str | None]:
        return profile, ip, username, password

    def fake_g1(host: str, username: str, password: str, timeout: float) -> DummyG1:
        created.update(
            {"host": host, "username": username, "password": password, "timeout": timeout},
        )
        return dummy

    monkeypatch.setattr("pygira.commands.gds.resolve_login", fake_login)
    monkeypatch.setattr("pygira.commands.gds.G1", fake_g1)
    return dummy, created


def test_gds_query_commands_use_g1_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy, created = _patch_gds_login(monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "gds",
            "--ip",
            "192.168.1.100",
            "--username",
            "admin",
            "--password",
            "secret",
            "process-view",
        ],
    )

    assert result.exit_code == 0, result.output
    assert created["host"] == "192.168.1.100"
    assert dummy.calls == [("process_view",)]


def test_gds_status_config_location_and_restart_commands_use_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dummy, _ = _patch_gds_login(monkeypatch)
    runner = CliRunner()
    base = [
        "gds",
        "--ip",
        "192.168.1.100",
        "--username",
        "admin",
        "--password",
        "secret",
    ]

    device_config_result = runner.invoke(main, [*base, "device-config"])
    status_result = runner.invoke(main, [*base, "tks-status"])
    location_result = runner.invoke(
        main,
        [*base, "set-location", "--lat", "53.1", "--lon", "8.2"],
    )
    restart_result = runner.invoke(main, [*base, "restart"])

    assert device_config_result.exit_code == 0, device_config_result.output
    assert status_result.exit_code == 0, status_result.output
    assert location_result.exit_code == 0, location_result.output
    assert restart_result.exit_code == 0, restart_result.output
    assert ("device_config",) in dummy.calls
    assert ("tks_status",) in dummy.calls
    assert ("set_location", 53.1, 8.2) in dummy.calls
    assert ("restart",) in dummy.calls


def test_gds_app_value_and_device_config_commands_use_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dummy, _ = _patch_gds_login(monkeypatch)
    runner = CliRunner()

    get_result = runner.invoke(
        main,
        [
            "gds",
            "--ip",
            "192.168.1.100",
            "--username",
            "admin",
            "--password",
            "secret",
            "app-value",
            "get",
            "--app-name",
            "Gira.G1",
            "--key",
            "weather.settings",
        ],
    )
    set_result = runner.invoke(
        main,
        [
            "gds",
            "--ip",
            "192.168.1.100",
            "--username",
            "admin",
            "--password",
            "secret",
            "app-value",
            "set",
            "--app-name",
            "Gira.G1",
            "--key",
            "weather.settings",
            "--value",
            "{}",
        ],
    )
    config_result = runner.invoke(
        main,
        [
            "gds",
            "--ip",
            "192.168.1.100",
            "--username",
            "admin",
            "--password",
            "secret",
            "set-device-config",
            "--set",
            "Latitude=53.123456",
            "--set",
            "Longitude=7.654321",
        ],
    )

    assert get_result.exit_code == 0, get_result.output
    assert set_result.exit_code == 0, set_result.output
    assert config_result.exit_code == 0, config_result.output
    assert ("app_value", "Gira.G1", "weather.settings") in dummy.calls
    assert ("set_app_value", "Gira.G1", "weather.settings", "{}") in dummy.calls
    assert ("set_device_config", {"Latitude": "53.123456", "Longitude": "7.654321"}) in dummy.calls


def test_gds_group_rejects_x1_before_query(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(device_type=DeviceType.X1)
    dummy, created = _patch_gds_login(monkeypatch, profile=profile)
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "gds",
            "--ip",
            "192.168.1.100",
            "--username",
            "admin",
            "--password",
            "secret",
            "process-view",
        ],
    )

    assert result.exit_code != 0
    assert "G1 only" in result.output
    assert created == {}
    assert dummy.calls == []


def test_gds_configure_tks_requires_capability_and_factory_reset_needs_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dummy, _ = _patch_gds_login(monkeypatch)
    seen = {}

    def fake_require_capability(profile: object, **kwargs: bool) -> None:
        seen["profile"] = profile
        seen["kwargs"] = kwargs

    monkeypatch.setattr("pygira.commands.gds.require_capability", fake_require_capability)
    runner = CliRunner()

    tks_result = runner.invoke(
        main,
        [
            "gds",
            "--ip",
            "192.168.1.100",
            "--username",
            "admin",
            "--password",
            "secret",
            "configure-tks",
            "--tks-ip",
            "10.0.0.1",
            "--tks-user",
            "user1",
            "--tks-pass",
            "pass1",
        ],
    )
    reset_result = runner.invoke(
        main,
        [
            "gds",
            "--ip",
            "192.168.1.100",
            "--username",
            "admin",
            "--password",
            "secret",
            "factory-reset",
            "--confirm",
        ],
    )

    assert tks_result.exit_code == 0, tks_result.output
    assert reset_result.exit_code == 0, reset_result.output
    assert seen["kwargs"] == {"tks": True}
    assert ("configure_tks", "10.0.0.1", "user1", "pass1") in dummy.calls
    assert ("factory_reset",) in dummy.calls
