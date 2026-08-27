"""Machine-readable command output.

Commands whose result is consumed by a script rather than read by a
human share the ``--format`` spelling and the serialization defined
here, so their documents stay interchangeable.
"""

import asyncclick as click


class MachineOutput:
    """YAML/JSON rendering of a command result."""

    FORMATS = ["yaml", "json"]
    DEFAULT_FORMAT = "yaml"

    @classmethod
    def format_option(cls, command):
        """Decorator adding the ``--format`` option to a command."""
        return click.option(
            "--format", "fmt",
            type=click.Choice(cls.FORMATS),
            default=cls.DEFAULT_FORMAT,
            help=f"Output format (default: {cls.DEFAULT_FORMAT})",
        )(command)

    @classmethod
    def echo(cls, data, fmt: str):
        """Write `data` to stdout in `fmt`."""
        click.echo(cls.render(data, fmt), nl=False)

    @classmethod
    def render(cls, data, fmt: str) -> str:
        """Serialize `data`, keeping the key order the caller built."""
        if fmt == "json":
            import json
            return json.dumps(data, indent=2) + "\n"

        if fmt == "yaml":
            import yaml
            return yaml.dump(data, sort_keys=False, default_flow_style=False)

        raise ValueError(f"Unknown output format: {fmt}")


__all__ = ["MachineOutput"]
