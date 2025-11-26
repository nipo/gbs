"""GBS Command Line Interface

Main entry point for the gbs command.
"""

import asyncclick as click
from pathlib import Path

from gbs.logging import setup_logging, get_logger, get_log_file


@click.group()
@click.version_option()
@click.option(
    "-v", "--verbose",
    is_flag=True,
    help="Enable verbose output (INFO level)"
)
@click.option(
    "-d", "--debug",
    is_flag=True,
    help="Enable debug output (DEBUG level)"
)
@click.option(
    "--log-dir",
    type=click.Path(path_type=Path),
    help="Custom directory for log files (default: .gbs/logs)"
)
@click.pass_context
async def cli(ctx, verbose: bool, debug: bool, log_dir: Path | None):
    """GBS: Gateware Build System

    A build system for FPGA and ASIC gateware projects.
    """
    # Set up logging
    setup_logging(verbose=verbose, debug=debug, log_dir=log_dir)
    logger = get_logger()

    # Store in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj["logger"] = logger
    ctx.obj["log_file"] = get_log_file()

    logger.debug(f"CLI invoked with verbose={verbose}, debug={debug}")


@cli.group()
async def repo():
    """Repository introspection commands"""
    pass


@cli.group()
async def project():
    """Project management commands"""
    pass


@cli.command()
async def build():
    """Build a project"""
    click.echo("Build command - not yet implemented")


@cli.command()
async def status():
    """Query build status"""
    click.echo("Status command - not yet implemented")


@cli.command()
async def clean():
    """Clean build artifacts"""
    click.echo("Clean command - not yet implemented")


@repo.command()
@click.argument("path")
async def list(path: str):
    """List libraries, partitions, and files in a repository"""
    click.echo(f"Listing repository at: {path}")
    click.echo("Not yet implemented")


@repo.command()
@click.argument("path")
async def validate(path: str):
    """Validate repository definitions"""
    click.echo(f"Validating repository at: {path}")
    click.echo("Not yet implemented")


@repo.command()
@click.argument("path")
async def query(path: str):
    """Query dependency traversal with filters"""
    click.echo(f"Querying repository at: {path}")
    click.echo("Not yet implemented")


@project.command()
@click.argument("project_file")
async def show(project_file: str):
    """Show project configuration"""
    click.echo(f"Showing project: {project_file}")
    click.echo("Not yet implemented")


@project.command()
@click.argument("project_file")
async def fileset(project_file: str):
    """Show resolved build file set"""
    click.echo(f"Showing fileset for project: {project_file}")
    click.echo("Not yet implemented")


def main():
    """Entry point for the gbs command"""
    cli(_anyio_backend="asyncio")


if __name__ == "__main__":
    main()
