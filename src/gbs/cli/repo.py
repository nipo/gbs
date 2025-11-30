"""GBS Repository Commands

Commands for managing GBS repositories.
"""

import asyncclick as click
from pathlib import Path
import sys

from ..logging import get_logger
from ..loaders import load_repository, LoadError
from .group import ReMatchGroup

@click.group(cls = ReMatchGroup)
async def repo():
    """Repository introspection commands"""
    pass

@repo.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
async def list(path: Path):
    """List libraries, partitions, and files in a repository

    PATH: Path to repository file (e.g., repository.gbs.yaml)
    """
    logger = get_logger()

    try:
        # Load repository
        repository = load_repository(path)

        click.echo(f"Repository: {repository.name}")
        if repository.description:
            click.echo(f"Description: {repository.description}")
        click.echo(f"Root: {repository.root}")
        click.echo()

        if not repository.libraries:
            click.echo("No libraries found")
            return

        click.echo(f"Libraries ({len(repository.libraries)}):")
        for lib_name, library in sorted(repository.libraries.items()):
            click.echo(f"  {lib_name}")
            if library.description:
                click.echo(f"    Description: {library.description}")

            if library.partitions:
                click.echo(f"    Partitions ({len(library.partitions)}):")
                for part_name, partition in sorted(library.partitions.items()):
                    # Count sources by evaluating root group
                    source_count = 0
                    for group in partition.groups:
                        for condition in group.conditions:
                            source_count += len(condition.sources)

                    click.echo(f"      {part_name} ({source_count} source files)")
            else:
                click.echo(f"    Partitions: (none)")
            click.echo()

    except LoadError as e:
        logger.error(f"Failed to load repository: {e}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error")
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


@repo.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
async def validate(path: Path):
    """Validate repository definitions

    PATH: Path to repository file (e.g., repository.gbs.yaml)
    """
    logger = get_logger()

    try:
        # Load repository (this validates YAML structure)
        repository = load_repository(path)

        errors = []
        warnings = []

        # Check libraries
        if not repository.libraries:
            warnings.append("Repository contains no libraries")

        # Validate each library
        for lib_name, library in repository.libraries.items():
            if not library.partitions:
                warnings.append(f"Library '{lib_name}' contains no partitions")

            # Validate each partition
            for part_name, partition in library.partitions.items():
                if not partition.groups:
                    errors.append(f"Partition '{lib_name}.{part_name}' has no groups")
                    continue

                # Check for at least some content
                has_content = False
                for group in partition.groups:
                    for condition in group.conditions:
                        if condition.sources or condition.deps or condition.groups:
                            has_content = True
                            break
                    if has_content:
                        break

                if not has_content:
                    warnings.append(f"Partition '{lib_name}.{part_name}' has no sources or dependencies")

        # Report results
        click.echo(f"Repository: {repository.name}")
        click.echo(f"Libraries: {len(repository.libraries)}")

        total_partitions = sum(len(lib.partitions) for lib in repository.libraries.values())
        click.echo(f"Partitions: {total_partitions}")
        click.echo()

        if errors:
            click.echo(f"Errors ({len(errors)}):", err=True)
            for error in errors:
                click.echo(f"  ✗ {error}", err=True)
            click.echo()

        if warnings:
            click.echo(f"Warnings ({len(warnings)}):")
            for warning in warnings:
                click.echo(f"  ⚠ {warning}")
            click.echo()

        if not errors and not warnings:
            click.echo("✓ Repository is valid")
        elif not errors:
            click.echo("✓ Repository is valid (with warnings)")
        else:
            click.echo("✗ Repository has errors", err=True)
            sys.exit(1)

    except LoadError as e:
        logger.error(f"Failed to load repository: {e}")
        click.echo(f"Error loading repository: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error")
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


@repo.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--partition", "-p", required=True, help="Partition to query (format: library.partition)")
@click.option("--filter", "-f", multiple=True, help="Filter variable (format: var=value)")
async def query(path: Path, partition: str, filter: tuple[str]):
    """Query dependency traversal with filters

    PATH: Path to repository file (e.g., repository.gbs.yaml)

    Example: gbs repo query repo.yaml -p mylib.mypart -f vendor=xilinx -f sim=0
    """
    logger = get_logger()

    try:
        # Parse filter variables
        filter_vars = {}
        for filter_spec in filter:
            if "=" not in filter_spec:
                click.echo(f"Error: Invalid filter '{filter_spec}', expected format: var=value", err=True)
                sys.exit(1)

            var, value = filter_spec.split("=", 1)

            # Try to parse as integer
            try:
                filter_vars[var] = int(value)
            except ValueError:
                filter_vars[var] = value

        # Load repository
        repository = load_repository(path)

        # Create a minimal project to use the resolver
        from ..models import Project, Library, Partition, FilterCondition, ConditionalGroup
        from ..resolver import DependencyResolver, PartitionRef

        # Parse partition reference
        try:
            start_ref = PartitionRef.parse(partition)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        # Create empty project with filter context
        empty_partition = Partition(name="__query__", groups=[])
        project = Project(
            name="__query__",
            root_partition=empty_partition,
            topcell="none",
            filter_vars=filter_vars
        )

        # Create resolver
        resolver = DependencyResolver(project, [repository])

        # Check partition exists
        if resolver.get_partition(start_ref) is None:
            click.echo(f"Error: Partition '{partition}' not found", err=True)
            sys.exit(1)

        # Build dependency graph from this partition
        graph = resolver.build_dependency_graph([start_ref])

        # Topologically sort
        sorted_refs = resolver.topological_sort(graph)

        # Display results
        click.echo(f"Query: {partition}")
        if filter_vars:
            click.echo(f"Filters: {filter_vars}")
        click.echo()

        click.echo(f"Dependency tree ({len(sorted_refs)} partitions):")
        click.echo()

        for ref in sorted_refs:
            resolved = graph[ref]
            indent = "  " if ref != start_ref else "→ "

            click.echo(f"{indent}{ref}")
            click.echo(f"    Sources: {len(resolved.sources)} files")

            if resolved.deps:
                click.echo(f"    Depends on: {', '.join(str(d) for d in resolved.deps)}")

        click.echo()
        click.echo(f"Build order: {' → '.join(str(r) for r in sorted_refs)}")

    except LoadError as e:
        logger.error(f"Failed to load repository: {e}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error during query")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
