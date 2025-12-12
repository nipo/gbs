"""GBS Suite Commands

Commands for managing GBS test suites (multi-project orchestration).
"""

import asyncclick as click
from pathlib import Path
import sys

from ..logging import get_logger
from ..ui import get_global_hub, LogMessage, BuildStatus
from .group import ReMatchGroup


@click.group(invoke_without_command=False, cls=ReMatchGroup)
@click.option(
    "-f", "--file",
    "suite_file",
    type=click.Path(exists=True, path_type=Path),
    help="Suite file (defaults to suite.gbs.yaml)"
)
@click.pass_context
async def suite(ctx, suite_file: Path | None):
    """Suite management commands"""
    # Store suite_file option in context (may be None)
    # Auto-discovery will happen in subcommands if needed
    ctx.obj["suite_file_option"] = suite_file


def get_suite_file(ctx) -> Path:
    """Get suite file from context, with auto-discovery if needed

    Args:
        ctx: Click context

    Returns:
        Path to the suite file

    Raises:
        click.ClickException: If no suite file found
    """
    suite_file = ctx.obj.get("suite_file_option")
    if suite_file is None:
        # Default to suite.gbs.yaml in current directory
        suite_file = Path("suite.gbs.yaml")
        if not suite_file.exists():
            raise click.ClickException(
                "No suite file found. Please specify with -f/--file or create suite.gbs.yaml"
            )
    return suite_file


@suite.command()
@click.option(
    "-o", "--output",
    "junit_output",
    type=click.Path(path_type=Path),
    help="JUnit XML output file"
)
@click.option(
    "--summary",
    "summary_output",
    type=click.Path(path_type=Path),
    help="Summary JSON output file"
)
@click.option(
    "--log-dir",
    "suite_log_dir",
    type=click.Path(path_type=Path),
    help="Directory for project logs"
)
@click.option(
    "-j", "--jobs",
    type=int,
    metavar="N",
    help="Maximum number of parallel projects (overrides suite config)"
)
@click.option(
    "--filter",
    "filter_file",
    type=click.Path(exists=True, path_type=Path),
    help="File containing list of changed files (one per line)"
)
@click.option(
    "--stop-on-failure",
    is_flag=True,
    help="Stop suite execution on first project failure"
)
@click.pass_context
async def build(
    ctx,
    junit_output,
    summary_output,
    suite_log_dir,
    jobs,
    filter_file,
    stop_on_failure
):
    """Build all projects in a suite"""
    from ..suite import load_suite, SuiteExecutor
    from ..suite.formatters import write_junit_xml, write_summary_json

    logger = get_logger()
    suite_file = get_suite_file(ctx)
    gbs_config = ctx.obj.get("gbs_config")

    # Validate jobs parameter
    if jobs is not None and jobs < 1:
        click.echo("Error: --jobs must be >= 1", err=True)
        sys.exit(1)

    try:
        # Load suite
        logger.info(f"Loading suite from {suite_file}")
        suite_def = load_suite(suite_file)

        # Override settings from command line
        if jobs is not None:
            suite_def.settings.max_parallel_projects = jobs

        if suite_log_dir is not None:
            suite_def.settings.output.log_dir = suite_log_dir

        if junit_output is not None:
            suite_def.settings.output.junit_xml = junit_output

        if summary_output is not None:
            suite_def.settings.output.summary_json = summary_output

        if stop_on_failure:
            suite_def.settings.stop_on_failure = True

        # Load changed files if filtering
        changed_files = set()
        if filter_file:
            logger.info(f"Loading changed files from {filter_file}")
            with open(filter_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        changed_files.add(Path(line).resolve())
            logger.info(f"Loaded {len(changed_files)} changed files")
            suite_def.settings.filter.enabled = True

        # Create executor and build
        executor = SuiteExecutor(suite_def, gbs_config=gbs_config, changed_files=changed_files)

        hub = get_global_hub()

        # Build suite with progress tracking
        async with hub.progress(
            f"Building suite '{suite_def.name}' ({len(suite_def.projects)} projects)",
            total=len(suite_def.projects)
        ) as prog:
            result = await executor.build_suite()

        # Generate outputs
        if suite_def.settings.output.junit_xml:
            logger.info(f"Writing JUnit XML to {suite_def.settings.output.junit_xml}")
            write_junit_xml(result, suite_def.settings.output.junit_xml)

        if suite_def.settings.output.summary_json:
            logger.info(f"Writing summary JSON to {suite_def.settings.output.summary_json}")
            write_summary_json(result, suite_def.settings.output.summary_json)

        # Emit summary via hub
        click.echo()
        click.echo("Suite Results:")
        click.echo(f"  Status: {result.status.value}")
        click.echo(f"  Duration: {result.duration:.1f}s")
        click.echo(f"  Total: {result.total_projects}")
        click.echo(f"  Successful: {result.successful}")
        click.echo(f"  Failed: {result.failed}")
        click.echo(f"  Errors: {result.errors}")
        click.echo(f"  Skipped: {result.skipped}")

        # Print project details
        if result.project_results:
            click.echo()
            click.echo("Projects:")
            for proj_result in result.project_results:
                status_icon = {
                    "success": "✓",
                    "failure": "✗",
                    "error": "✗",
                    "skipped": "-"
                }.get(proj_result.status.value, "?")

                click.echo(f"  {status_icon} {proj_result.project.name} ({proj_result.duration:.1f}s)")

                if proj_result.error_message:
                    click.echo(f"      Error: {proj_result.error_message}")

        # Exit with error if suite failed
        if result.status.value in ("failure", "error"):
            sys.exit(1)

    except Exception as e:
        logger.exception("Suite build failed")
        click.echo(f"Suite build failed: {e}", err=True)
        sys.exit(1)


@suite.command()
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Show detailed information"
)
@click.pass_context
async def list(ctx, verbose):
    """List all projects in a suite"""
    from ..suite import load_suite, SuiteExecutor

    logger = get_logger()
    suite_file = get_suite_file(ctx)
    gbs_config = ctx.obj.get("gbs_config")

    try:
        # Load suite
        suite_def = load_suite(suite_file)

        click.echo(f"Suite: {suite_def.name}")
        if suite_def.description:
            click.echo(f"Description: {suite_def.description}")
        click.echo()

        # Use executor to list projects (ensures consistent project file discovery)
        executor = SuiteExecutor(suite_def, gbs_config=gbs_config)
        projects_with_files = executor.list_projects()

        click.echo(f"Projects ({len(projects_with_files)}):")
        for proj, project_file in projects_with_files:
            status = "(skipped)" if proj.skip else ""
            status_mark = "" if project_file else " [ERROR: project file not found]"
            click.echo(f"  • {proj.name} {status}{status_mark}")

            if verbose:
                if project_file:
                    click.echo(f"      Path: {project_file}")
                else:
                    click.echo(f"      Path: {proj.path} (not found)")
                if proj.output_groups:
                    click.echo(f"      Output groups: {', '.join(proj.output_groups)}")
                if proj.depends_on:
                    click.echo(f"      Depends on: {', '.join(proj.depends_on)}")
                if proj.tags:
                    click.echo(f"      Tags: {', '.join(proj.tags)}")
                click.echo()

    except Exception as e:
        logger.exception("Failed to list suite")
        click.echo(f"Failed to list suite: {e}", err=True)
        sys.exit(1)


@suite.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be cleaned without actually cleaning"
)
@click.pass_context
async def clean(ctx, dry_run):
    """Clean all projects in a suite

    Removes build directories and generated files for all projects.
    Uses the same base output path logic as build.
    """
    from ..suite import load_suite, SuiteExecutor

    logger = get_logger()
    suite_file = get_suite_file(ctx)
    gbs_config = ctx.obj.get("gbs_config")

    try:
        # Load suite
        suite_def = load_suite(suite_file)

        click.echo(f"Cleaning suite '{suite_def.name}' ({len(suite_def.projects)} projects)...")

        # Use executor to clean (ensures consistent base_output_path as build)
        executor = SuiteExecutor(suite_def, gbs_config=gbs_config)
        results = await executor.clean_suite(dry_run=dry_run)

        # Report results
        for proj_name, error in results.items():
            if error == "skipped":
                click.echo(f"  - {proj_name}: skipped")
            elif error:
                click.echo(f"  - {proj_name}: ERROR - {error}", err=True)

        click.echo()
        if dry_run:
            click.echo("Dry run complete (no files were deleted)")
        else:
            click.echo("Clean complete")

    except Exception as e:
        logger.exception("Suite clean failed")
        click.echo(f"Suite clean failed: {e}", err=True)
        sys.exit(1)


__all__ = ['suite']
