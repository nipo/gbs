"""GBS Command Line Interface

Main entry point for the gbs command.
"""

import asyncclick as click
from pathlib import Path
import sys

from gbs.logging import setup_logging, get_logger, get_log_file
from gbs.loaders import load_repository, load_project, load_project_with_repositories, LoadError
from gbs.resolver import resolve_project
from gbs.plugins import get_plugin_registry
from gbs.config import GBSConfig


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
    logger.debug(f"Loaded {len(gbs_config.tools)} tools, {len(gbs_config.profiles)} profiles")


@cli.group()
async def repo():
    """Repository introspection commands"""
    pass


@cli.group()
async def project():
    """Project management commands"""
    pass


@cli.command()
@click.argument("project_file", type=click.Path(exists=True, path_type=Path), default="project.gbs.yaml")
@click.option(
    "-r", "--repo",
    type=click.Path(exists=True, path_type=Path),
    multiple=True,
    help="Additional repository to load"
)
@click.option(
    "-o", "--output-dir",
    type=click.Path(path_type=Path),
    default=Path("build"),
    help="Build output directory (default: build)"
)
@click.option(
    "--max-iterations",
    type=int,
    default=100,
    help="Maximum backend iterations (default: 100)"
)
@click.option(
    "--show-graph",
    is_flag=True,
    help="Show build dependency graph and exit (do not build)"
)
@click.pass_context
async def build(ctx, project_file: Path, repo: tuple[Path], output_dir: Path, max_iterations: int, show_graph: bool):
    """Build a project

    PROJECT_FILE: Path to project file (default: project.gbs.yaml)
    """
    import asyncio
    from gbs.tasks import BuildContext, BuildFileSet, BuildResource
    from gbs.backend import run_backend_iteration
    from gbs.backend_loader import load_backends_from_project

    logger = get_logger()
    show_pb = ctx.obj["allow_progress_bars"]

    try:
        # Get GBS config from context
        gbs_config = ctx.obj.get("gbs_config")

        # Load project and its specified repositories
        click.echo(f"Loading project: {project_file}")
        project, repositories = load_project_with_repositories(project_file, gbs_config=gbs_config)

        # Load additional repositories from command line
        for repo_path in repo:
            click.echo(f"Loading additional repository: {repo_path}")
            repositories.append(load_repository(repo_path))

        # Resolve dependencies
        click.echo("Resolving dependencies...")
        build_set = resolve_project(project, repositories)

        click.echo(f"Resolved {len(build_set.get_all_files())} files in {len(build_set.libraries)} libraries")

        # Create build context
        build_ctx = BuildContext(project=project, gbs_config=gbs_config)

        # Create build fileset and populate it
        fileset = BuildFileSet(build_ctx)

        click.echo("Creating build fileset...")
        # First pass: create all BuildResources
        partition_to_resources: dict[tuple[str, str], list[BuildResource]] = {}
        for lib_name in build_set.libraries:
            if lib_name == "nsl_simulation":
                click.echo(f"DEBUG: nsl_simulation partitions order: {build_set.partitions.get(lib_name, [])}")
            for part_name in build_set.partitions.get(lib_name, []):
                files = build_set.files.get((lib_name, part_name), [])
                partition_key = (lib_name, part_name)
                partition_to_resources[partition_key] = []

                for source_file in files:
                    # Map language to file type (language is now a plain string)
                    file_type = source_file.language
                    if source_file.variant:
                        file_type = f"{file_type}_{source_file.variant}"

                    # Create BuildResource
                    br = BuildResource(
                        resource=build_ctx.get_resource(source_file.path),
                        file_type=file_type,
                        library=lib_name,
                    )
                    partition_to_resources[partition_key].append(br)
                    fileset.add(br)

        # Second pass: populate BuildResource.depends_on based on partition dependencies
        for partition_key, resources in partition_to_resources.items():
            lib_name, part_name = partition_key
            partition_deps = build_set.partition_deps.get(partition_key, set())

            # For each resource in this partition, add dependencies on all resources from dependent partitions
            for br in resources:
                for dep_lib, dep_part in partition_deps:
                    dep_partition_key = (dep_lib, dep_part)
                    dep_resources = partition_to_resources.get(dep_partition_key, [])
                    br.depends_on.update(dep_resources)

        # Load backends from project configuration
        click.echo("Loading backends...")
        registry = load_backends_from_project(project.raw_config)

        if len(registry) == 0:
            click.echo("Warning: No backends configured", err=True)
            click.echo("Add 'backends' section to project configuration")
            sys.exit(1)

        click.echo(f"Loaded {len(registry)} backend(s):")
        for backend in registry:
            click.echo(f"  - {backend.name} (priority={backend.priority})")

        # Create output directory
        output_dir.mkdir(parents=True, exist_ok=True)

        # Run backend iteration
        click.echo()
        click.echo("Running backend iteration...")
        iterations = await run_backend_iteration(
            build_ctx,
            fileset,
            registry,
            max_iterations=max_iterations
        )
        click.echo(f"Converged after {iterations} iteration(s)")

        # Show dependency graph if requested
        if show_graph:
            click.echo()
            click.echo("Build dependency graph:")
            click.echo()

            # Collect all tasks
            tasks = set()
            for br in fileset:
                # Check if resource has any tasks that expect it (producers)
                if hasattr(br.resource, 'expected_by'):
                    for dep in br.resource.expected_by:
                        if hasattr(dep, 'executor'):  # It's a task
                            tasks.add(dep)

            # Display tasks with their dependencies
            if not tasks:
                click.echo("  No build tasks generated")
            else:
                for task in sorted(tasks, key=lambda t: t.name):
                    click.echo(f"  Task: {task.name}")
                    if task.description:
                        click.echo(f"    Description: {task.description}")

                    # Show inputs
                    if hasattr(task, 'inputs') and task.inputs:
                        click.echo(f"    Inputs ({len(task.inputs)}):")
                        for inp in task.inputs:
                            if hasattr(inp, 'path'):
                                click.echo(f"      - {inp.path}")
                            else:
                                click.echo(f"      - {inp}")

                    # Show outputs
                    if hasattr(task, 'outputs') and task.outputs:
                        click.echo(f"    Outputs ({len(task.outputs)}):")
                        for out in task.outputs:
                            if hasattr(out, 'path'):
                                click.echo(f"      - {out.path}")
                            else:
                                click.echo(f"      - {out}")

                    click.echo()

            click.echo(f"Total tasks: {len(tasks)}")
            return  # Exit without building

        # Execute build
        click.echo()
        click.echo("Executing build tasks...")
        async with build_ctx.build():
            # Gather all resources
            all_resources = [br.resource for br in fileset]
            if all_resources:
                # Use progress monitoring if available and stdout is TTY
                from gbs.progress import run_with_progress, HAS_TQDM
                import sys

                if HAS_TQDM and sys.stdout.isatty() and show_pb:
                    # Run with progress bars
                    await run_with_progress(build_ctx, all_resources)
                else:
                    # Run without progress bars
                    await asyncio.gather(*all_resources)

        click.echo()
        click.echo(f"Build complete: {len(fileset)} files processed")

        # Show summary
        click.echo()
        click.echo("Build summary:")
        for lib_name in fileset.libraries_in_dependency_order():
            lib_files = fileset.filter(library=lib_name)
            click.echo(f"  Library {lib_name}: {len(lib_files)} files")

    except LoadError as e:
        logger.error(f"Failed to load project or repository: {e}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Build failed")
        click.echo(f"Build failed: {e}", err=True)
        sys.exit(1)


@cli.command()
async def status():
    """Query build status"""
    click.echo("Status command - not yet implemented")


@cli.command()
@click.argument(
    "project_file",
    type=click.Path(exists=True, path_type=Path),
    default="project.gbs.yaml",
    required=False
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be deleted without actually deleting"
)
@click.option(
    "-f", "--force",
    is_flag=True,
    help="Skip confirmation prompt and delete immediately"
)
async def clean(project_file: Path, dry_run: bool, force: bool):
    """Clean build artifacts

    Removes build directories and generated files specified in backend configurations.
    By default, looks for project.gbs.yaml in the current directory.

    PROJECT_FILE: Path to project file (default: project.gbs.yaml)
    """
    import shutil

    logger = get_logger()

    try:
        # Load project configuration
        click.echo(f"Loading project: {project_file}")
        project = load_project(project_file)

        # Get backends from raw_config
        backends = project.raw_config.get("backends", [])
        if not backends:
            click.echo("No backends configured - nothing to clean")
            return

        # Collect directories and files to clean
        dirs_to_clean = set()
        files_to_clean = set()

        for backend_config in backends:
            config = backend_config.get("config", {})

            # Check for output_dir in backend config
            if "output_dir" in config:
                output_dir = Path(config["output_dir"])
                if not output_dir.is_absolute():
                    output_dir = project_file.parent / output_dir
                dirs_to_clean.add(output_dir)

            # Check for topcell (GHDL generates executables)
            if "topcell" in config:
                topcell = config["topcell"]
                # Executable is in current directory
                exe_path = project_file.parent / topcell
                if exe_path.exists():
                    files_to_clean.add(exe_path)

                # GHDL also creates e~<topcell>.o files
                elab_file = project_file.parent / f"e~{topcell}.o"
                if elab_file.exists():
                    files_to_clean.add(elab_file)

        # Show what will be cleaned
        if not dirs_to_clean and not files_to_clean:
            click.echo("No build artifacts found")
            return

        click.echo("\nBuild artifacts to clean:")
        for dir_path in sorted(dirs_to_clean):
            if dir_path.exists():
                # Count files in directory
                file_count = sum(1 for _ in dir_path.rglob("*") if _.is_file())
                click.echo(f"  📁 {dir_path}/ ({file_count} files)")
            else:
                click.echo(f"  📁 {dir_path}/ (not found)")

        for file_path in sorted(files_to_clean):
            size = file_path.stat().st_size if file_path.exists() else 0
            size_str = f"{size:,} bytes" if size < 1024*1024 else f"{size/(1024*1024):.1f} MB"
            click.echo(f"  📄 {file_path} ({size_str})")

        if dry_run:
            click.echo("\n--dry-run: No files were deleted")
            return

        # Confirm deletion (unless --force is used)
        if not force:
            if not click.confirm("\nProceed with deletion?"):
                click.echo("Cancelled")
                return

        # Delete directories
        deleted_dirs = 0
        deleted_files = 0

        for dir_path in dirs_to_clean:
            if dir_path.exists():
                try:
                    shutil.rmtree(dir_path)
                    click.echo(f"✓ Deleted {dir_path}/")
                    deleted_dirs += 1
                except Exception as e:
                    click.echo(f"✗ Failed to delete {dir_path}/: {e}", err=True)

        # Delete files
        for file_path in files_to_clean:
            if file_path.exists():
                try:
                    file_path.unlink()
                    click.echo(f"✓ Deleted {file_path}")
                    deleted_files += 1
                except Exception as e:
                    click.echo(f"✗ Failed to delete {file_path}: {e}", err=True)

        click.echo(f"\nCleaned {deleted_dirs} directories and {deleted_files} files")

    except LoadError as e:
        click.echo(f"Error loading project: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Clean failed")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


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
        from gbs.models import Project, Library, Partition, FilterCondition, ConditionalGroup
        from gbs.resolver import DependencyResolver, PartitionRef

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


@project.command()
@click.argument("project_file", type=click.Path(exists=True, path_type=Path))
async def show(project_file: Path):
    """Show project configuration

    PROJECT_FILE: Path to project file (e.g., project.gbs.yaml)
    """
    logger = get_logger()

    try:
        # Load project
        project = load_project(project_file)

        click.echo(f"Project: {project.name}")
        if project.description:
            click.echo(f"Description: {project.description}")
        click.echo()

        if project.filter_vars:
            click.echo("Filter variables:")
            for var, value in sorted(project.filter_vars.items()):
                click.echo(f"  {var} = {value}")
            click.echo()

        click.echo("Root partition:")
        click.echo(f"  Library: {project.root_library_name}")
        click.echo(f"  Partition: {project.root_partition.name}")

    except LoadError as e:
        logger.error(f"Failed to load project: {e}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error")
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


@project.command()
@click.argument("project_file", type=click.Path(exists=True, path_type=Path))
@click.option("--repo", "-r", multiple=True, type=click.Path(exists=True, path_type=Path),
              help="Additional repository to load")
async def fileset(project_file: Path, repo: tuple[Path]):
    """Show resolved build file set

    PROJECT_FILE: Path to project file (e.g., project.gbs.yaml)
    """
    logger = get_logger()

    try:
        # Load project and its specified repositories
        project, repositories = load_project_with_repositories(project_file)

        # Load additional repositories from command line
        for repo_path in repo:
            repositories.append(load_repository(repo_path))

        # Resolve dependencies
        build_set = resolve_project(project, repositories)

        # Display results
        click.echo(f"Project: {project.name}")
        click.echo()

        click.echo(f"Build file set ({len(build_set.get_all_files())} files):")
        click.echo()

        for lib_name in build_set.libraries:
            click.echo(f"Library: {lib_name}")

            for part_name in build_set.partitions.get(lib_name, []):
                files = build_set.files.get((lib_name, part_name), [])
                click.echo(f"  Partition: {part_name} ({len(files)} files)")

                for source_file in files:
                    variant_str = f"-{source_file.variant}" if source_file.variant else ""
                    click.echo(f"    - {source_file.path.name} ({source_file.language.value}{variant_str})")

            click.echo()

        click.echo(f"Build order: {' → '.join(build_set.libraries)}")

    except LoadError as e:
        logger.error(f"Failed to load project or repository: {e}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error during resolution")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def main():
    """Entry point for the gbs command"""
    cli(_anyio_backend="asyncio")


if __name__ == "__main__":
    main()
