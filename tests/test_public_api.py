"""Contract tests for the supported package-root API."""

from importlib.resources import files

from click.testing import CliRunner

import pygira
from pygira.cli import main


def test_public_api_exports_library_entry_points() -> None:
    assert pygira.G1.__name__ == "G1"
    assert pygira.X1.__name__ == "X1"
    assert pygira.ApiClient.__name__ == "ApiClient"
    assert issubclass(pygira.AuthenticationError, pygira.PygiraError)
    assert issubclass(pygira.DeviceApiError, pygira.PygiraError)
    assert issubclass(pygira.TransportError, pygira.PygiraError)
    assert issubclass(pygira.UnsupportedCapabilityError, pygira.PygiraError)
    assert pygira.__version__


def test_package_ships_pep561_marker() -> None:
    assert files("pygira").joinpath("py.typed").is_file()


def test_cli_exposes_package_version() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0, result.output
    assert pygira.__version__ in result.output
