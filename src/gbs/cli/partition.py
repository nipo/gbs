"""GBS Partition Commands

Commands operating on a single repository partition, outside of any
project build.
"""

import asyncclick as click
import sys
from pathlib import Path

from ..logging import get_logger
from ..cli import discover_project_files, parse_filter_vars
from ._tool_overrides import parse_backend_kv
from .group import ReMatchGroup


@click.group(cls = ReMatchGroup)
@click.option(
    "--file",
    "project_file",
    type=click.Path(exists=True, path_type=Path),
    help="Project file to take repositories from (auto-discovered if not specified)"
)
@click.pass_context
async def partition(ctx, project_file: Path | None):
    """Partition introspection commands"""
    ctx.obj["project_file_option"] = project_file


class ValidateCommand:
    """Option handling for `gbs partition validate`."""

    @staticmethod
    def project_data_load(project_file: Path | None) -> tuple[dict, Path]:
        """Read the project file the repositories are declared in.

        A partition is validated against the repositories reachable from
        the current directory, which is a superset of what any single
        project uses. A project file is therefore optional: without one
        the config-level repositories stand alone. An ambiguous choice
        is not resolved silently.

        Returns:
            (project data, base path for relative repository paths)
        """
        import yaml

        if project_file is None:
            candidates = discover_project_files()
            if len(candidates) > 1:
                names = ", ".join(sorted(str(c.name) for c in candidates))
                raise click.ClickException(
                    f"Multiple project files found: {names}. "
                    f"Please specify which one with --file."
                )
            if not candidates:
                return {}, Path.cwd()
            project_file = candidates[0]

        data = yaml.safe_load(project_file.read_text())
        if not isinstance(data, dict):
            raise click.ClickException(
                f"Project file {project_file} does not contain a mapping"
            )
        return data, project_file.parent

    @staticmethod
    def backend_resolve(name: str, backend_names: list[str]) -> str:
        """Resolve a user-typed backend name to a registered one.

        Accepts the full module name or any unambiguous substring of it,
        matching how tool overrides are keyed.
        """
        if name in backend_names:
            return name
        matches = [b for b in backend_names if name in b]
        if not matches:
            known = ", ".join(sorted(backend_names))
            raise click.ClickException(
                f"Unknown backend '{name}'. Known backends: {known}"
            )
        if len(matches) > 1:
            raise click.ClickException(
                f"Backend '{name}' is ambiguous, matches: {', '.join(sorted(matches))}"
            )
        return matches[0]

    @staticmethod
    def parse_config(specs: tuple[str, ...]) -> dict[str, str]:
        """Parse ``key=value`` backend config options.

        Values stay strings: backend config keys are consumed as written
        in project files, where ``vhdl_standard: "2008"`` is a string.
        """
        return dict(parse_backend_kv(specs, "-c/--config"))


@partition.command()
@click.argument("partition_name", metavar="LIBRARY.PARTITION")
@click.option(
    "-o", "--output",
    default="-",
    metavar="PATH",
    help="Write the report to PATH ('-' for stdout, the default)"
)
@click.option(
    "-b", "--backend",
    default=None,
    metavar="NAME",
    help="Restrict validation to this backend (full name or unambiguous substring)"
)
@click.option(
    "-f", "--filter",
    "filters",
    multiple=True,
    metavar="VAR=VALUE",
    help="Filter variable for partition expansion. Takes precedence over "
         "variables contributed by the validating pass. May be given multiple times."
)
@click.option(
    "-c", "--config",
    "config_overrides",
    multiple=True,
    metavar="KEY=VALUE",
    help="Backend configuration override, e.g. vhdl_standard=2008. Unlike -f "
         "this reaches the tool invocation itself. May be given multiple times."
)
@click.pass_context
async def validate(ctx, partition_name: str, output: str, backend: str | None,
                   filters: tuple[str, ...], config_overrides: tuple[str, ...]):
    """Check dependency tracking and syntax of one partition.

    Resolves LIBRARY.PARTITION against the repositories declared in the
    GBS configuration and, if there is one, the project file, then hands
    every source a validator can read to that validator. Analysis only:
    nothing is elaborated, simulated or synthesized.

    Sources whose type no validator consumes are listed in the report as
    skipped, so a clean report never implies more coverage than it has.

    Example: gbs partition validate mylib.mypart -f vendor=xilinx -c vhdl_standard=2008
    """
    from ..plugins import get_plugin_registry
    from ..project.partition_validation import (
        PartitionValidation,
        PartitionValidationError,
    )
    from ..repository.loader import load_repositories_from_project, LoadError

    logger = get_logger()
    gbs_config = ctx.obj.get("gbs_config")
    hub = ctx.obj.get("feedback_hub")

    to_stdout = output == "-"
    if to_stdout:
        # Everything else the command would print goes to stderr so the
        # report is the only thing on stdout.
        if hub is not None:
            hub.divert_output(sys.stderr)
        report_path = PartitionValidation.default_report_path()
    else:
        report_path = Path(output)

    filter_vars = parse_filter_vars(filters)
    config = ValidateCommand.parse_config(config_overrides)
    project_data, base_path = ValidateCommand.project_data_load(
        ctx.obj.get("project_file_option")
    )

    backend_names = [b.name for b in get_plugin_registry().get_all_backends()]
    if backend is not None:
        require_backends = [ValidateCommand.backend_resolve(backend, backend_names)]
    else:
        require_backends = []

    # Config overrides target the backend that ends up validating. It is
    # not known before planning, so they are offered to every candidate;
    # each backend only reads the keys it declares.
    configured_backends = require_backends or backend_names
    backend_config = {name: dict(config) for name in configured_backends}

    try:
        repositories = load_repositories_from_project(
            project_data, base_path, gbs_config=gbs_config
        )
    except LoadError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not repositories:
        click.echo(
            "Error: No repositories configured. Declare them in ~/.config/gbs.yaml, "
            ".gbs.yaml or a project file.",
            err=True,
        )
        sys.exit(1)

    validation = PartitionValidation(
        partition_name=partition_name,
        repositories=repositories,
        gbs_config=gbs_config,
        report_path=report_path,
        project_data=project_data,
        filter_vars=filter_vars,
        backend_config=backend_config,
        require_backends=require_backends,
    )

    try:
        report = await validation.run()
    except PartitionValidationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Validation failed")
        click.echo(f"Validation failed: {e}", err=True)
        sys.exit(1)

    if hub is not None:
        # Drain queued messages before the report so they cannot land in
        # the middle of it.
        await hub.flush()

    if to_stdout:
        click.echo(report_path.read_text(), nl=False)
    else:
        click.echo(f"Validation report written to {report_path}", err=True)

    sys.exit(0 if report.status == "ok" else 1)
