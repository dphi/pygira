import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from pygira.cli import main
from pygira.devices.x1 import PROFILE as X1_PROFILE
from tests import _httpmock as respx
from tests._httpmock import Response

HOST = "192.168.1.100"
USER = "device"
PASS = "secret"


def _x1_login() -> tuple[object, str, str, str]:
    return X1_PROFILE, HOST, USER, PASS


@respx.mock
def test_restart_uses_x1_webservice_reboot() -> None:
    route = respx.post(f"http://{HOST}/webservice").mock(return_value=Response(200, json={}))

    with patch("pygira.commands._target.resolve_login", return_value=_x1_login()):
        result = CliRunner().invoke(main, ["--device", "x1", "restart"])

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.read()) == {"command": "reboot"}


@respx.mock
def test_factory_reset_uses_x1_webservice_factory_reset() -> None:
    route = respx.post(f"http://{HOST}/webservice").mock(return_value=Response(200, json={}))

    with patch("pygira.commands._target.resolve_login", return_value=_x1_login()):
        result = CliRunner().invoke(main, ["--device", "x1", "factory-reset", "--confirm"])

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.read()) == {"command": "factoryReset"}


def test_restart_routes_tks_ip_through_device_facade() -> None:
    device = MagicMock()
    with patch("pygira.commands.maintenance._device_client", return_value=device):
        result = CliRunner().invoke(main, ["--device", "tks-ip", "restart"])

    assert result.exit_code == 0, result.output
    device.reboot.assert_called_once_with()
