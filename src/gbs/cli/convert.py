"""GBS Conversion Commands

Stand-alone file format converters, usable outside any project.
"""

import sys
from pathlib import Path

import asyncclick as click

from .group import ReMatchGroup


@click.group(cls = ReMatchGroup)
async def convert():
    """Stand-alone file format converters"""
    pass


@convert.command("vivado-bus")
@click.argument(
    "yaml_files",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--output-dir", "-o",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory receiving the generated XML files",
)
@click.option(
    "--check",
    is_flag=True,
    help="Verify generated files are up to date instead of writing them",
)
async def vivado_bus(yaml_files: tuple[Path, ...], output_dir: Path,
                     check: bool):
    """Generate Vivado IP-XACT bus definitions from YAML descriptions.

    Each YAML file produces a busDefinition (<name>.xml) and an
    abstractionDefinition (<name>_rtl.xml) in the output directory.
    See the vivado-bus backend documentation for the YAML format.
    """
    from ..builtin.vivado_bus import generator

    stale = 0
    try:
        for yaml_path in yaml_files:
            for file_name, text in generator.Bus.load(yaml_path).outputs().items():
                target = output_dir / file_name
                if not check:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    target.write_text(text)
                elif not target.exists():
                    click.echo(f"{target}: missing")
                    stale += 1
                elif target.read_text() != text:
                    click.echo(f"{target}: differs from {yaml_path}")
                    stale += 1
    except generator.BusDefError as error:
        raise click.ClickException(str(error))

    if check:
        if stale:
            sys.exit(1)
        click.echo(f"{len(yaml_files)} bus descriptions up to date")
