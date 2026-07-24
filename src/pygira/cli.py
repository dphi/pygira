"""pygira - Gira provisioning CLI."""

import click

from pygira import __version__, command_support
from pygira.commands import bootstrap, config, device, firmware, gds, maintenance
from pygira.core.types import DeviceType
from pygira.exceptions import PygiraError


class PygiraGroup(click.Group):
    """Translate expected library failures once at the CLI boundary."""

    def invoke(self, ctx: click.Context) -> object:
        """Invoke a command and render expected library failures for CLI users."""
        try:
            return super().invoke(ctx)
        except (PygiraError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(cls=PygiraGroup)
@click.version_option(version=__version__, prog_name="pygira")
@click.option(
    "--device",
    type=click.Choice(["g1", "x1"]),
    default=None,
    help="Expected device type",
)
@click.option("--name", "device_name", default=None, help="Named device from devices.toml")
@click.option("--location", default=None, help="Optional location name from devices.toml")
@click.option(
    "--config",
    "config_path",
    default="devices.toml",
    show_default=True,
    help="Path to devices.toml",
)
@click.pass_context
def main(
    ctx: click.Context,
    device: str | None,
    device_name: str | None,
    location: str | None,
    config_path: str,
) -> None:
    """Provision and manage Gira devices."""
    ctx.ensure_object(dict)
    ctx.obj["requested_device"] = DeviceType(device) if device else None
    ctx.obj["device_name"] = device_name
    ctx.obj["location"] = location
    ctx.obj["config_path"] = config_path


config.register(main)
device.register(main)
gds.register(main)
maintenance.register(main)
firmware.register(main)
bootstrap.register(main)
command_support.register(main)
command_support.annotate_help(main)

if __name__ == "__main__":
    main()
