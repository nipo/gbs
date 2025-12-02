"""GBS Command Line Interface

Main entry point for the gbs command.
"""

import asyncclick as click
from pathlib import Path

from ..logging import setup_logging, get_logger, get_log_file
from ..loaders import load_repository, load_project, load_project_with_repositories, LoadError
from ..plugins import get_plugin_registry
from ..config import GBSConfig
from .group import ReMatchGroup

def load_project_for_command(ctx, project_file: Path, additional_repos: tuple[Path] = ()):
    """Load project with GBS config for CLI commands

    Args:
        ctx: Click context with gbs_config
        project_file: Path to project file
        additional_repos: Optional additional repositories to load

    Returns:
        (project, repositories, gbs_config) tuple
    """
    gbs_config = ctx.obj.get("gbs_config")

    click.echo(f"Loading project: {project_file}")
    project, repositories = load_project_with_repositories(project_file, gbs_config=gbs_config)

    # Load additional repositories from command line
    for repo_path in additional_repos:
        click.echo(f"Loading additional repository: {repo_path}")
        repositories.append(load_repository(repo_path))

    return project, repositories, gbs_config


def find_project_file() -> Path:
    """Find project file in current directory

    Looks for *.gbs.yaml files. If exactly one is found, returns it.
    Otherwise raises an error.

    Returns:
        Path to the project file

    Raises:
        click.ClickException: If no project file or multiple project files found
    """
    import glob
    import os

    # Use glob.glob to avoid asyncio issues with Path.glob
    project_files = [Path(f) for f in glob.glob("*.gbs.yaml", root_dir=os.getcwd())]

    if len(project_files) == 0:
        raise click.ClickException(
            "No project file found. Please specify with -f/--file or create a *.gbs.yaml file."
        )
    elif len(project_files) > 1:
        files_str = ", ".join(str(f.name) for f in project_files)
        raise click.ClickException(
            f"Multiple project files found: {files_str}. Please specify which one with -f/--file."
        )

    return project_files[0]


def get_project_file(ctx) -> Path:
    """Get project file from context, with auto-discovery if needed

    Args:
        ctx: Click context

    Returns:
        Path to the project file

    Raises:
        click.ClickException: If no project file found or multiple found
    """
    project_file = ctx.obj.get("project_file_option")
    if project_file is None:
        project_file = find_project_file()
    return project_file


@click.group(cls = ReMatchGroup)
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

    # Load GBS configuration
    logger.debug("Loading GBS configuration...")
    plugin_registry = get_plugin_registry()
    plugin_registry.discover_plugins()
    default_tools = plugin_registry.get_default_tools()
    gbs_config = GBSConfig.load(plugin_defaults=default_tools)

    # Store in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj["logger"] = logger
    ctx.obj["log_file"] = get_log_file()
    ctx.obj["gbs_config"] = gbs_config
    ctx.obj["allow_progress_bars"] = not verbose and not debug

    logger.debug(f"CLI invoked with verbose={verbose}, debug={debug}")
    logger.debug(f"Loaded {len(gbs_config.tools)} tools")


# Import and register command groups
from .repo import repo
from .project import project

cli.add_command(repo)
cli.add_command(project)


def main():
    """Entry point for the gbs command"""
    cli(_anyio_backend="asyncio")
