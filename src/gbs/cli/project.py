"""GBS Project Commands

Commands for managing GBS projects.
"""

import asyncclick as click
from pathlib import Path
import sys

from ..logging import get_logger
from ..loaders import load_project, load_project_with_repositories, load_repository, LoadError
from ..resolver import resolve_project
from ..cli import load_project_for_command, get_project_file
from .group import ReMatchGroup

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


@project.command()
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
async def build(ctx, repo: tuple[Path], output_dir: Path, max_iterations: int, show_graph: bool):
    """Build a project"""
    from ..tasks import BuildContext, BuildFileSet

    logger = get_logger()
    show_pb = ctx.obj["allow_progress_bars"]
    project_file = get_project_file(ctx)

    try:
        # Load project configuration
        project, repositories, gbs_config = load_project_for_command(ctx, project_file, repo)

        #Verify project has output groups
        if not project.output_groups:
            click.echo("Error: Project must define at least one output group", err=True)
            click.echo("See documentation for output group configuration", err=True)
            sys.exit(1)

        await _build_with_output_groups(
            project, repositories, gbs_config,
            output_dir, max_iterations, show_graph, show_pb
        )

    except LoadError as e:
        logger.error(f"Failed to load project or repository: {e}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Build failed")
        click.echo(f"Build failed: {e}", err=True)
        sys.exit(1)


async def _build_with_output_groups(
    project, repositories, gbs_config,
    output_dir: Path, max_iterations: int, show_graph: bool, show_pb: bool
):
    """Build using new planner + executor flow"""
    from ..tasks import BuildContext, BuildFileSet
    from ..planner import plan_project
    from ..backend.registry import get_backend_registry
    from ..model.dispatcher import DispatcherRegistry, run_dispatcher_iteration
    from ..resolver import resolve_project

    logger = get_logger()

    # Create build context
    build_ctx = BuildContext(project=project, gbs_config=gbs_config)

    # Discover backends
    click.echo("Discovering backends...")
    backend_registry = get_backend_registry()
    backend_names = backend_registry.list_backends()
    click.echo(f"Discovered {len(backend_names)} backend(s):")
    for backend_module in backend_names:
        click.echo(f"  - {backend_module}")

    # Plan build for all output groups
    click.echo()
    click.echo(f"Planning build for {len(project.output_groups)} output group(s)...")
    backends = backend_registry.get_all_backends()

    # Create synthetic repository from project's root partition for planning
    from ..model.repository import Repository, Library
    from pathlib import Path
    project_repo = Repository(name=project.name, root=Path("."))
    project_library = Library(name=project.root_library_name)
    project_library.add_partition(project.root_partition)
    project_repo.add_library(project_library)

    # Include project repo in repositories for planning
    all_repositories = [project_repo] + repositories

    plans = plan_project(project, all_repositories, backends)

    # Resolve sources for each plan
    for plan in plans:
        # Resolve dependencies for this output group's topcell
        build_set = resolve_project(project, all_repositories)
        plan.source_fileset = build_set

        num_files = len(plan.source_fileset.get_all_files())
        num_libs = len(plan.source_fileset.libraries)
        click.echo(f"  Output group '{plan.output_group.name}':")
        click.echo(f"    Topcell: {plan.output_group.topcell}")
        click.echo(f"    Sources: {num_files} files in {num_libs} libraries")
        click.echo(f"    Passes: {len(plan.passes)}")
        click.echo(f"    Outputs: {len(plan.output_group.outputs)}")

    # Show dependency graph if requested
    if show_graph:
        click.echo()
        click.echo("Build dependency graph:")
        click.echo()

        for plan in plans:
            click.echo(f"Output group: {plan.output_group.name}")
            click.echo(f"  Topcell: {plan.output_group.topcell}")
            click.echo()

            # Show source files by library
            click.echo(f"  Source files ({len(plan.source_fileset.get_all_files())} files):")
            for lib_name in plan.source_fileset.libraries:
                files = plan.source_fileset.files.get((lib_name, None), [])
                if not files:
                    # Try all partitions for this library
                    for part_name in plan.source_fileset.partitions.get(lib_name, []):
                        files.extend(plan.source_fileset.files.get((lib_name, part_name), []))

                if files:
                    click.echo(f"    Library {lib_name}:")
                    for source_file in files:
                        # Handle both enum and string for language
                        lang_str = source_file.file_type.value if hasattr(source_file.file_type, 'value') else str(source_file.file_type)
                        click.echo(f"      - {source_file.path.name} ({lang_str})")

            click.echo()

            # Show passes
            click.echo(f"  Passes ({len(plan.passes)} passes):")
            for pass_class in plan.passes:
                click.echo(f"    - {pass_class.name}")
                click.echo(f"        Input types: {', '.join(pass_class.input_types)}")
                click.echo(f"        Output types: {', '.join(pass_class.output_types)}")

            click.echo()

            # Show expected outputs
            click.echo(f"  Expected outputs ({len(plan.output_group.outputs)} files):")
            for output_spec in plan.output_group.outputs:
                click.echo(f"    - {output_spec.path} (type: {output_spec.type})")

            click.echo()

        return  # Exit without building

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Execute each build plan
    click.echo()
    click.echo("Executing build plans...")

    for plan in plans:
        click.echo(f"\nBuilding output group '{plan.output_group.name}'...")

        # Set topcell and library for this output group
        # This allows dispatchers to access the topcell via context.get_topcell()
        build_ctx.set_output_group_context(
            topcell=plan.output_group.topcell,
            topcell_library=project.root_library_name
        )

        # Create BuildFileSet from plan's source fileset
        fileset = BuildFileSet(build_ctx)
        build_ctx.populate_fileset(plan.source_fileset, fileset)

        # Determine which backends contributed to this plan
        # We need to create dispatchers for these backends
        backend_modules_used = set()
        for pass_metadata in plan.passes:
            # Each pass knows which backend it came from
            # For now, we need to match passes to backends
            # The planner should track this, but we can infer it from pass names
            for backend_module in backend_names:
                backend = backend_registry.get_backend(backend_module)
                # Check if this backend provides this pass
                contributed_passes = backend.contribute_passes(
                    plan.output_group.backend_config.get(backend_module, {}),
                    {output.type for output in plan.output_group.outputs}
                )
                if pass_metadata.pass_class in contributed_passes:
                    backend_modules_used.add(backend_module)
                    break

        # Create dispatcher registry for this plan
        dispatcher_registry = DispatcherRegistry()
        for backend_module in backend_modules_used:
            backend = backend_registry.get_backend(backend_module)
            backend_config = plan.output_group.backend_config.get(backend_module, {})
            backend_config['output_dir'] = str(output_dir)
            dispatcher = backend.create_dispatcher(backend_config)
            dispatcher_registry.register(dispatcher)
            click.echo(f"  Registered dispatcher: {dispatcher.name}")

        # Run dispatcher iteration
        iterations = await run_dispatcher_iteration(
            build_ctx,
            fileset,
            dispatcher_registry,
            max_iterations=max_iterations
        )
        click.echo(f"  Converged after {iterations} iteration(s)")

        # Execute build tasks
        click.echo(f"  Executing build tasks...")
        num_files = await build_ctx.execute_build(
            fileset,
            show_progress=(sys.stdout.isatty() and show_pb)
        )
        click.echo(f"  Processed {num_files} files")

    click.echo()
    click.echo("Build complete!")


@project.command()
@click.option(
    "-o", "--output-dir",
    type=click.Path(path_type=Path),
    default=Path("build"),
    help="Build output directory to clean (default: build)"
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
@click.pass_context
async def clean(ctx, output_dir: Path, dry_run: bool, force: bool):
    """Clean build artifacts

    Removes build directories and generated files specified in backend configurations.
    """
    import shutil

    logger = get_logger()
    project_file = get_project_file(ctx)

    try:
        # Load project configuration
        project, repositories, gbs_config = load_project_for_command(ctx, project_file)

        # Collect directories and files to clean
        dirs_to_clean = set()
        files_to_clean = set()

        # Add default output directory
        if not output_dir.is_absolute():
            output_dir = project_file.parent / output_dir
        dirs_to_clean.add(output_dir)

        # Get backends from raw_config for additional cleanup
        backends = project.raw_config.get("backends", [])

        for backend_config in backends:
            config = backend_config.get("config", {})

            # Check for additional output_dir in backend config
            if "output_dir" in config:
                backend_output_dir = Path(config["output_dir"])
                if not backend_output_dir.is_absolute():
                    backend_output_dir = project_file.parent / backend_output_dir
                dirs_to_clean.add(backend_output_dir)

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


@project.command()
@click.pass_context
async def show(ctx):
    """Show project configuration"""
    logger = get_logger()
    project_file = get_project_file(ctx)

    try:
        # Load project
        project = load_project(project_file)

        click.echo(f"Project: {project.name}")
        if project.description:
            click.echo(f"Description: {project.description}")
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
@click.option("--repo", "-r", multiple=True, type=click.Path(exists=True, path_type=Path),
              help="Additional repository to load")
@click.option("--output-group", "-g", type=str,
              help="Output group to resolve for (default: first output group)")
@click.pass_context
async def fileset(ctx, repo: tuple[Path], output_group: str | None):
    """Show resolved build file set for an output group"""
    from ..resolver import DependencyResolver
    from ..model.repository import Repository, Library

    logger = get_logger()
    project_file = get_project_file(ctx)

    try:
        # Load project and its specified repositories
        gbs_config = ctx.obj.get("gbs_config")
        click.echo(f"Loading project: {project_file}")
        project, repositories = load_project_with_repositories(project_file, gbs_config=gbs_config)

        # Load additional repositories from command line
        for repo_path in repo:
            repositories.append(load_repository(repo_path))

        # Check if project has output groups
        if not project.output_groups:
            click.echo("Error: Project must define at least one output group", err=True)
            sys.exit(1)

        # Select output group
        if output_group:
            selected_group = next((g for g in project.output_groups if g.name == output_group), None)
            if not selected_group:
                click.echo(f"Error: Output group '{output_group}' not found", err=True)
                click.echo(f"Available: {', '.join(g.name for g in project.output_groups)}", err=True)
                sys.exit(1)
        else:
            selected_group = project.output_groups[0]

        click.echo(f"Project: {project.name}")
        click.echo(f"Output group: {selected_group.name}")
        click.echo()

        # Create synthetic repository from project's root partition
        project_repo = Repository(name=project.name, root=project_file.parent)
        project_library = Library(name=project.root_library_name)
        project_library.add_partition(project.root_partition)
        project_repo.add_library(project_library)

        # Include project repo in repositories
        all_repositories = [project_repo] + repositories

        # Resolve dependencies with output group's filter_vars
        resolver = DependencyResolver(project, all_repositories, selected_group.filter_vars)
        build_set = resolver.resolve()

        # Display results
        click.echo(f"Build file set ({len(build_set.get_all_files())} files):")
        click.echo()

        for lib_name in build_set.libraries:
            click.echo(f"Library: {lib_name}")

            for part_name in build_set.partitions.get(lib_name, []):
                files = build_set.files.get((lib_name, part_name), [])
                click.echo(f"  Partition: {part_name} ({len(files)} files)")

                for source_file in files:
                    variant_str = f"-{source_file.variant}" if source_file.variant else ""
                    # Handle both enum and string for file_type
                    file_type_str = source_file.file_type.value if hasattr(source_file.file_type, 'value') else str(source_file.file_type)
                    click.echo(f"    - {source_file.path.name} ({file_type_str}{variant_str})")

            click.echo()

        click.echo(f"Build order: {' → '.join(build_set.libraries)}")

        if selected_group.filter_vars:
            click.echo()
            click.echo("Filter variables:")
            for key, value in sorted(selected_group.filter_vars.items()):
                click.echo(f"  {key}: {value}")

    except LoadError as e:
        logger.error(f"Failed to load project or repository: {e}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error during resolution")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
