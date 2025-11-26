"""GBS Command Line Interface

Main entry point for the gbs command.
"""

import asyncclick as click


@click.group()
@click.version_option()
async def cli():
    """GBS: Gateware Build System

    A build system for FPGA and ASIC gateware projects.
    """
    pass


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
