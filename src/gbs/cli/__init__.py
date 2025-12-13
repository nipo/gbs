"""GBS Command Line Interface

Main entry point for the gbs command.
"""

import asyncclick as click
import sys
from pathlib import Path

from ..logging import setup_logging, get_logger, get_log_file
from ..config.model import GBSConfig
from ..ui import FeedbackHub, SimpleBackend, set_global_hub
from ..ui.messages import MessageSeverity, LogLevel
from .group import ReMatchGroup

# Try to import Rich backend
try:
    from ..ui import RichBackend, is_rich_available
    _has_rich = is_rich_available()
except ImportError:
    _has_rich = False


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


DEFAULT_MAX_LOG_COUNT = 10


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
    "-P", "--no-progress",
    is_flag=True,
    help="Disable progress bars"
)
@click.option(
    "--log-dir",
    type=click.Path(path_type=Path),
    help="Custom directory for log files (default: .gbs/logs)"
)
@click.pass_context
async def cli(ctx, verbose: bool, debug: bool, no_progress: bool, log_dir: Path | None):
    """GBS: Gateware Build System

    A build system for FPGA and ASIC gateware projects.
    """
    # Set up logging
    gbs_logger = setup_logging(verbose=verbose, debug=debug, log_dir=log_dir)
    logger = get_logger()

    # Load GBS configuration
    logger.debug("Loading GBS configuration...")
    gbs_config = GBSConfig.load()

    # Cleanup old logs based on config (0 = keep all)
    max_log_count = gbs_config.max_log_count if gbs_config.max_log_count is not None else DEFAULT_MAX_LOG_COUNT
    gbs_logger.cleanup_old_logs(max_log_count)

    # Determine minimum severity based on flags
    if debug:
        min_severity = MessageSeverity.DEBUG
        min_log_level = LogLevel.DEBUG
    elif verbose:
        min_severity = MessageSeverity.INFO
        min_log_level = LogLevel.INFO
    else:
        min_severity = MessageSeverity.WARNING
        min_log_level = LogLevel.WARNING

    # Determine if progress bars should be shown
    # Disable if: verbose, debug, or --no-progress flag
    show_progress = not verbose and not debug and not no_progress

    # Create FeedbackHub for unified UI output
    # Use RichBackend if available and stdout is a TTY, otherwise SimpleBackend
    if _has_rich and sys.stdout.isatty() and not verbose and not debug:
        backend = RichBackend(
            show_progress=show_progress,
            min_severity=min_severity,
            min_log_level=min_log_level
        )
        logger.debug("Using RichBackend for fancy terminal output")
    else:
        backend = SimpleBackend(
            show_progress=show_progress,
            min_severity=min_severity,
            min_log_level=min_log_level
        )
        logger.debug("Using SimpleBackend for plain text output")

    hub = FeedbackHub(backend)
    await hub.__aenter__()

    # Set as global hub
    set_global_hub(hub)

    # Store in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj["logger"] = logger
    ctx.obj["log_file"] = get_log_file()
    ctx.obj["gbs_config"] = gbs_config
    ctx.obj["allow_progress_bars"] = not verbose and not debug
    ctx.obj["feedback_hub"] = hub
    ctx.obj["_hub_cleanup"] = lambda: hub.__aexit__(None, None, None)

    logger.debug(f"CLI invoked with verbose={verbose}, debug={debug}")
    logger.debug(f"Loaded {len(gbs_config.tools)} tools")


# Import and register command groups
from .repo import repo
from .project import project
from .suite import suite

cli.add_command(repo)
cli.add_command(project)
cli.add_command(suite)


# Add result callback to cleanup hub
@cli.result_callback()
@click.pass_context
async def cleanup_hub(ctx, result, **kwargs):
    """Clean up FeedbackHub after command completes"""
    if "_hub_cleanup" in ctx.obj:
        await ctx.obj["_hub_cleanup"]()
        set_global_hub(None)


def main():
    """Entry point for the gbs command"""
    cli(_anyio_backend="asyncio")
