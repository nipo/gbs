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

@click.group(invoke_without_command=False)
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

        # Resolve dependencies
        click.echo("Resolving dependencies...")
        build_set = resolve_project(project, repositories)
        click.echo(f"Resolved {len(build_set.get_all_files())} files in {len(build_set.libraries)} libraries")

        # Create build context
        build_ctx = BuildContext(project=project, gbs_config=gbs_config)

        # Create and populate fileset
        click.echo("Creating build fileset...")
        fileset = BuildFileSet(build_ctx)
        build_ctx.populate_fileset(build_set, fileset)

        # Create output directory
        output_dir.mkdir(parents=True, exist_ok=True)

        # Load and run backends
        click.echo("Loading backends...")
        try:
            registry = build_ctx.load_backends()
            click.echo(f"Loaded {len(registry)} backend(s):")
            for backend in registry:
                click.echo(f"  - {backend.name} (priority={backend.priority})")
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            click.echo("Add 'backends' section to project configuration")
            sys.exit(1)

        # Run backend iteration
        click.echo()
        click.echo("Running backend iteration...")
        iterations, registry = await build_ctx.run_backends(fileset, max_iterations)
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
        num_files = await build_ctx.execute_build(
            fileset,
            show_progress=(sys.stdout.isatty() and show_pb)
        )

        click.echo()
        click.echo(f"Build complete: {num_files} files processed")

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


@project.command()
@click.pass_context
async def status(ctx):
    """Query build status"""
    project_file = get_project_file(ctx)
    click.echo(f"Status for project: {project_file}")
    click.echo("Status command - not yet implemented")


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
@click.option("--repo", "-r", multiple=True, type=click.Path(exists=True, path_type=Path),
              help="Additional repository to load")
@click.pass_context
async def fileset(ctx, repo: tuple[Path]):
    """Show resolved build file set"""
    logger = get_logger()
    project_file = get_project_file(ctx)

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
