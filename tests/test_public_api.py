"""Contract tests for the supported package-root API."""

from importlib.resources import files

import click
from click.testing import CliRunner

import pygira
from pygira.cli import PygiraGroup, main


def test_public_api_exports_library_entry_points() -> None:
    assert pygira.G1.__name__ == "G1"
    assert pygira.X1.__name__ == "X1"
    assert pygira.ApiClient.__name__ == "ApiClient"
    assert pygira.GdsClient.__name__ == "GdsClient"
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


def test_cli_boundary_translates_expected_library_errors() -> None:
    @click.group(cls=PygiraGroup)
    def cli() -> None:
        pass

    @cli.command()
    def fail() -> None:
        msg = "device unavailable"
        raise pygira.TransportError(msg)

    result = CliRunner().invoke(cli, ["fail"])

    assert result.exit_code == 1
    assert result.output == "Error: device unavailable\n"
    assert isinstance(result.exception, SystemExit)


def test_cli_boundary_does_not_hide_programming_errors() -> None:
    @click.group(cls=PygiraGroup)
    def cli() -> None:
        pass

    @cli.command()
    def fail() -> None:
        msg = "bug"
        raise RuntimeError(msg)

    result = CliRunner().invoke(cli, ["fail"])

    assert result.exit_code == 1
    assert result.output == ""
    assert isinstance(result.exception, RuntimeError)
