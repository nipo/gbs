"""GBS Project Commands

Commands for managing GBS projects.
"""

import asyncclick as click
from pathlib import Path
import sys

from ..logging import get_logger
from ..repository.loader import load_repository
from ..cli import get_project_file
from .group import ReMatchGroup
from .machine_output import MachineOutput

@click.group(invoke_without_command=False, cls = ReMatchGroup)
@click.option(
    "-f", "--file",
    "project_file",
    type=click.Path(exists=True, path_type=Path),
    help="Project file (auto-discovered if not specified)"
)
@click.pass_context
async def project(ctx, project_file: Path | None):
    """Project management commands"""
    # Store project_file option in context (may be None)
    # Auto-discovery will happen in subcommands if needed
    ctx.obj["project_file_option"] = project_file


async def _project_load(project_file, gbs_config):
    """Load a project.

    Tool overrides given on the root ``gbs`` group ride along on
    ``gbs_config``; the planner applies them per backend, so no
    per-project mutation is needed here.
    """
    from ..project import Project, LoadError
    logger = get_logger()

    try:
        # Load project using new API
        proj = Project.load_from_file(project_file, gbs_config=gbs_config)

    except LoadError as e:
        logger.error(f"Failed to load project or repository: {e}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Loading failed")
        click.echo(f"Loading failed: {e}", err=True)
        raise

    # Verify project has output groups
    if not proj.model.output_groups:
        click.echo("Error: Project must define at least one output group", err=True)
        click.echo("See documentation for output group configuration", err=True)
        sys.exit(1)

    return proj


@project.command()
@click.option(
    "-j", "--jobs",
    type=int,
    metavar="N",
    help="Maximum number of parallel tasks (overrides config files)"
)
@click.argument("output_groups", nargs=-1, metavar="[OUTPUT_GROUP...]")
@click.pass_context
async def build(ctx, jobs, output_groups):
    """Build a project.

    With no OUTPUT_GROUP arguments, every output group declared in the
    project is built. Pass one or more output group names to restrict
    the build to that subset — useful when different groups target
    tools that are not all available locally.
    """
    logger = get_logger()
    project_file = get_project_file(ctx)
    gbs_config = ctx.obj.get("gbs_config")

    # Validate jobs parameter
    if jobs is not None and jobs < 1:
        click.echo("Error: --jobs must be >= 1", err=True)
        sys.exit(1)

    proj = await _project_load(project_file, gbs_config)

    # Apply command-line overrides
    if jobs is not None:
        proj.set_max_parallel(jobs)

    selected = list(output_groups) if output_groups else None

    try:
        await proj.build(selected)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        from ..build.task import BuildError, MissingToolError, ConfigurationError
        if isinstance(e, (MissingToolError, ConfigurationError)):
            # Configuration error — print the message with config hint.
            # Raised before task execution starts (e.g. during build graph
            # construction), so nothing has printed a failure summary yet.
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        elif isinstance(e, BuildError):
            # BuildError means _cleanup() already printed failure summary
            # Just exit with error code
            sys.exit(1)
        else:
            # Other exceptions - log and print
            logger.exception("Build failed")
            click.echo(f"Build failed: {e}", err=True)
            sys.exit(1)

@project.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be deleted without actually deleting"
)
@click.pass_context
async def clean(ctx, dry_run: bool):
    """Clean build artifacts

    Removes build directories and generated files specified in backend configurations.
    """
    logger = get_logger()
    show_pb = ctx.obj["allow_progress_bars"]
    project_file = get_project_file(ctx)
    gbs_config = ctx.obj.get("gbs_config")

    proj = await _project_load(project_file, gbs_config)

    try:
        await proj.clean(dry_run)
    except Exception as e:
        logger.exception("Clean failed")
        click.echo(f"Clean failed: {e}", err=True)
        sys.exit(1)

@project.command()
@click.option(
    "--diagram",
    type=click.Path(path_type=Path),
    help="Generate a graphviz diagram and save to specified path (SVG format)"
)
@click.pass_context
async def show(ctx, diagram: Path | None):
    """Show project configuration"""
    logger = get_logger()
    show_pb = ctx.obj["allow_progress_bars"]
    project_file = get_project_file(ctx)
    gbs_config = ctx.obj.get("gbs_config")

    proj = await _project_load(project_file, gbs_config)

    try:
        if diagram:
            await proj.show_graph(diagram_path=diagram)
        else:
            await proj.show_graph()
    except Exception as e:
        logger.exception("Build failed")
        click.echo(f"Build failed: {e}", err=True)
        sys.exit(1)


@project.command()
@MachineOutput.format_option
@click.pass_context
async def outputs(ctx, fmt: str):
    """List output files, types, and required backends

    Emits the same record schema as `gbs suite outputs`, so documents
    from both commands can be consumed by one reader.
    """
    from ..project.output_inventory import OutputInventory

    project_file = get_project_file(ctx)
    gbs_config = ctx.obj.get("gbs_config")
    hub = ctx.obj.get("feedback_hub")

    # The report is the only thing on stdout; everything the load and
    # the planner have to say goes to stderr.
    if hub is not None:
        hub.divert_output(sys.stderr)

    proj = await _project_load(project_file, gbs_config)
    records = OutputInventory(proj, name=proj.model.name).records()

    if hub is not None:
        # Drain queued messages before the report so they cannot land in
        # the middle of it.
        await hub.flush()

    MachineOutput.echo(records, fmt)
