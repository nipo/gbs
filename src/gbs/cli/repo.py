"""GBS Repository Commands

Commands for managing GBS repositories.
"""

import asyncclick as click
from pathlib import Path
import sys

from ..logging import get_logger
from ..repository.loader import load_repository, LoadError
from ..repository.resolver import DependencyResolver, ResolutionError
from .group import ReMatchGroup


def _parse_filter_vars(filter_specs: tuple[str, ...]) -> dict[str, str | int]:
    """Parse ``var=value`` filter options into a filter-vars dict.

    Values that look like integers are coerced to ``int`` so that numeric
    filter expressions (e.g. ``sim=0``) evaluate correctly.
    """
    filter_vars: dict[str, str | int] = {}
    for spec in filter_specs:
        if "=" not in spec:
            raise click.ClickException(
                f"Invalid filter '{spec}', expected format: var=value"
            )
        var, value = spec.split("=", 1)
        try:
            filter_vars[var] = int(value)
        except ValueError:
            filter_vars[var] = value
    return filter_vars


@click.group(cls = ReMatchGroup)
async def repo():
    """Repository introspection commands"""
    pass

@repo.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--partition", "-p", required=True, help="Root partition to traverse (format: library.partition)")
@click.option("--filter", "-f", multiple=True, help="Filter variable (format: var=value)")
async def list(path: Path, partition: str, filter: tuple[str, ...]):
    """List the dependency tree of a partition in a repository

    Cold enumeration of a repository is no longer possible: repositories are
    resolved lazily by partition lookup, so a starting partition is required.
    Traversing its dependency tree surfaces every partition (and its files)
    reachable from that root.

    PATH: Path to repository file (e.g., repository.gbs.yaml)

    Example: gbs repo list repo.gbs.yaml -p mylib.mypart -f vendor=xilinx -f sim=0
    """
    logger = get_logger()

    try:
        filter_vars = _parse_filter_vars(filter)

        # Load repository
        repository = load_repository(path)

        # Look up the starting partition; this expands its conditionals with
        # the given filter variables into a resolved Partition.
        root = repository.partition_lookup(partition, filter_vars)
        if root is None:
            click.echo(
                f"Error: Partition '{partition}' not found in repository "
                f"'{repository.name}'",
                err=True,
            )
            sys.exit(1)

        # Resolve the full dependency tree from this root, within this repo.
        resolver = DependencyResolver([repository], filter_vars)
        source_set = resolver.resolve([root])

        click.echo(f"Repository: {repository.name}")
        click.echo(f"Root partition: {partition}")
        if filter_vars:
            click.echo(f"Filters: {filter_vars}")
        click.echo()

        click.echo(f"Dependency tree ({len(source_set.partitions)} partitions, build order):")
        click.echo()

        for name in source_set.partitions:
            sources = source_set.sources.get(name, [])
            deps = source_set.partition_deps.get(name, set())

            marker = "→ " if name == root.name else "  "
            click.echo(f"{marker}{name} ({len(sources)} source files)")
            if deps:
                click.echo(f"    Depends on: {', '.join(sorted(deps))}")

        click.echo()
        click.echo(f"Build order: {' → '.join(source_set.partitions)}")

    except ResolutionError as e:
        logger.error(f"Failed to resolve dependency tree: {e}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except LoadError as e:
        logger.error(f"Failed to load repository: {e}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error")
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)
