"""Project building functionality

Core build execution logic for GBS projects.
"""

import sys
from pathlib import Path
from typing import Optional

import click

from ..logging import get_logger
from ..build import BuildContext, BuildFileSet
from ..backend.registry import get_backend_registry
from ..backend.dispatcher import DispatcherRegistry, run_dispatcher_iteration
from ..repository.model import Repository, Library
from ..repository.resolver import resolve_project
from ..planner.planner import plan_project

logger = get_logger(__name__)


async def build_project(
    project_model,
    repositories: list,
    output_dir: Path = Path("build"),
    max_iterations: int = 10,
    show_progress: bool = True,
    gbs_config=None
):
    """Build a project

    Core build logic that plans and executes builds for all output groups.

    Args:
        project_model: ProjectModel to build
        repositories: List of Repository objects
        output_dir: Output directory for build artifacts
        max_iterations: Maximum dispatcher iterations
        show_progress: Whether to show progress bars
        gbs_config: Optional GBS configuration

    Returns:
        None

    Raises:
        Exception: If build fails
    """
    # Create build context
    build_ctx = BuildContext(project=project_model, gbs_config=gbs_config)

    # Discover backends
    logger.info("Discovering backends...")
    backend_registry = get_backend_registry()
    backend_names = backend_registry.list_backends()
    logger.info(f"Discovered {len(backend_names)} backend(s):")
    for backend_module in backend_names:
        logger.info(f"  - {backend_module}")

    # Plan build for all output groups
    logger.info("")
    logger.info(f"Planning build for {len(project_model.output_groups)} output group(s)...")
    backends = backend_registry.get_all_backends()

    # Create synthetic repository from project's root partition for planning
    project_repo = Repository(name=project_model.name, root=Path("."))
    project_library = Library(name=project_model.root_library_name)
    project_library.add_partition(project_model.root_partition)
    project_repo.add_library(project_library)

    # Include project repo in repositories for planning
    all_repositories = [project_repo] + repositories

    plans = plan_project(project_model, all_repositories, backends)

    # Resolve sources for each plan
    for plan in plans:
        # Resolve dependencies for this output group's topcell
        build_set = resolve_project(project_model, all_repositories)
        plan.source_fileset = build_set

        num_files = len(plan.source_fileset.get_all_files())
        num_libs = len(plan.source_fileset.libraries)
        logger.info(f"  Output group '{plan.output_group.name}':")
        logger.info(f"    Topcell: {plan.output_group.topcell}")
        logger.info(f"    Sources: {num_files} files in {num_libs} libraries")
        logger.info(f"    Passes: {len(plan.passes)}")
        logger.info(f"    Outputs: {len(plan.output_group.outputs)}")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Execute each build plan
    logger.info("")
    logger.info("Executing build plans...")

    for plan in plans:
        logger.info(f"\nBuilding output group '{plan.output_group.name}'...")

        # Set topcell and library for this output group
        build_ctx.set_output_group_context(
            topcell=plan.output_group.topcell,
            topcell_library=project_model.root_library_name
        )

        # Create BuildFileSet from plan's source fileset
        fileset = BuildFileSet(build_ctx)
        build_ctx.populate_fileset(plan.source_fileset, fileset)

        # Determine which backends to use:
        # 1. Backends that contributed passes (main backend doing the work)
        # 2. Backends configured in backend_config (may be post-processors like NSL CDC)
        backend_modules_used = set()

        # Add backends that contributed passes
        for pass_metadata in plan.passes:
            for backend_module in backend_names:
                backend = backend_registry.get_backend(backend_module)
                contributed_passes = backend.contribute_passes(
                    plan.output_group.backend_config.get(backend_module, {}),
                    {output.type for output in plan.output_group.outputs}
                )
                if pass_metadata.pass_class in contributed_passes:
                    backend_modules_used.add(backend_module)
                    break

        # Add backends explicitly configured in backend_config (e.g., post-processors)
        for backend_module in plan.output_group.backend_config.keys():
            if backend_module in backend_names:
                backend_modules_used.add(backend_module)

        # Create dispatcher registry for this plan
        dispatcher_registry = DispatcherRegistry()
        for backend_module in backend_modules_used:
            backend = backend_registry.get_backend(backend_module)
            backend_config = plan.output_group.backend_config.get(backend_module, {})
            backend_config['output_dir'] = str(output_dir)
            dispatcher = backend.create_dispatcher(backend_config)
            dispatcher_registry.register(dispatcher)
            logger.info(f"  Registered dispatcher: {dispatcher.name}")

        # Run dispatcher iteration
        iterations = await run_dispatcher_iteration(
            build_ctx,
            fileset,
            dispatcher_registry,
            max_iterations=max_iterations
        )
        logger.info(f"  Converged after {iterations} iteration(s)")

        # Execute build tasks
        logger.info(f"  Executing build tasks...")
        num_files = await build_ctx.execute_build(
            fileset,
            show_progress=(sys.stdout.isatty() and show_progress)
        )
        logger.info(f"  Processed {num_files} files")

    logger.info("")
    logger.info("Build complete!")


def _show_task_graph(build_ctx):
    """Display the build task graph

    Args:
        build_ctx: BuildContext with registered steps
    """
    from ..build.task import Resource, VirtualResource, Task

    # Organize steps by type
    resources = []
    virtual_resources = []
    tasks = []

    for step in build_ctx.steps:
        if isinstance(step, Resource):
            resources.append(step)
        elif isinstance(step, VirtualResource):
            virtual_resources.append(step)
        elif isinstance(step, Task):
            tasks.append(step)

    click.echo(f"    Resources ({len(resources)} files):")
    for resource in sorted(resources, key=lambda r: r.name):
        deps = [d.name for d in resource.depends_on if d in tasks]
        if deps:
            click.echo(f"      {resource.name}")
            for dep_name in sorted(deps):
                click.echo(f"        ← produced by: {dep_name}")
        else:
            click.echo(f"      {resource.name} (source)")

    click.echo("")
    click.echo(f"    Tasks ({len(tasks)} tasks):")
    for task in sorted(tasks, key=lambda t: t.name):
        click.echo(f"      {task.name}")

        # Show what this task depends on (inputs)
        input_resources = [d for d in task.depends_on if isinstance(d, (Resource, VirtualResource))]
        if input_resources:
            for dep in sorted(input_resources, key=lambda r: r.name):
                click.echo(f"        → reads: {dep.name}")

        # Show what depends on this task (outputs)
        output_resources = [e for e in task.expected_by if isinstance(e, (Resource, VirtualResource))]
        if output_resources:
            for exp in sorted(output_resources, key=lambda r: r.name):
                click.echo(f"        ← produces: {exp.name}")

    if virtual_resources:
        click.echo("")
        click.echo(f"    Virtual resources ({len(virtual_resources)}):")
        for vr in sorted(virtual_resources, key=lambda r: r.name):
            click.echo(f"      {vr.name}")


async def show_graph_for_project(
    project_model,
    repositories: list,
    output_dir: Path = Path("build"),
    max_iterations: int = 10,
    gbs_config=None
):
    """Show build dependency graph for a project

    Displays detailed information about the build plan including source files,
    passes, outputs, library dependencies, and build task graph.

    Args:
        project_model: ProjectModel to analyze
        repositories: List of Repository objects
        output_dir: Output directory for build artifacts
        max_iterations: Maximum dispatcher iterations
        gbs_config: Optional GBS configuration

    Returns:
        None
    """
    # Create build context
    build_ctx = BuildContext(project=project_model, gbs_config=gbs_config)

    # Discover backends
    click.echo("Discovering backends...")
    backend_registry = get_backend_registry()
    backend_names = backend_registry.list_backends()
    click.echo(f"Discovered {len(backend_names)} backend(s):")
    for backend_module in backend_names:
        click.echo(f"  - {backend_module}")

    # Plan build for all output groups
    click.echo("")
    click.echo(f"Planning build for {len(project_model.output_groups)} output group(s)...")
    backends = backend_registry.get_all_backends()

    # Create synthetic repository from project's root partition for planning
    project_repo = Repository(name=project_model.name, root=Path("."))
    project_library = Library(name=project_model.root_library_name)
    project_library.add_partition(project_model.root_partition)
    project_repo.add_library(project_library)

    # Include project repo in repositories for planning
    all_repositories = [project_repo] + repositories

    plans = plan_project(project_model, all_repositories, backends)

    # Resolve sources for each plan
    for plan in plans:
        # Resolve dependencies for this output group's topcell
        build_set = resolve_project(project_model, all_repositories)
        plan.source_fileset = build_set

        num_files = len(plan.source_fileset.get_all_files())
        num_libs = len(plan.source_fileset.libraries)
        click.echo(f"  Output group '{plan.output_group.name}':")
        click.echo(f"    Topcell: {plan.output_group.topcell}")
        click.echo(f"    Sources: {num_files} files in {num_libs} libraries")
        click.echo(f"    Passes: {len(plan.passes)}")
        click.echo(f"    Outputs: {len(plan.output_group.outputs)}")

    # Show detailed dependency graph
    click.echo("")
    click.echo("Build dependency graph:")
    click.echo("")

    for plan in plans:
        click.echo(f"Output group: {plan.output_group.name}")
        click.echo(f"  Topcell: {plan.output_group.topcell}")
        click.echo("")

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

        click.echo("")

        # Show passes
        click.echo(f"  Passes ({len(plan.passes)} passes):")
        for pass_class in plan.passes:
            click.echo(f"    - {pass_class.name}")
            click.echo(f"        Input types: {', '.join(pass_class.input_types)}")
            click.echo(f"        Output types: {', '.join(pass_class.output_types)}")

        click.echo("")

        # Show expected outputs
        click.echo(f"  Expected outputs ({len(plan.output_group.outputs)} files):")
        for output_spec in plan.output_group.outputs:
            click.echo(f"    - {output_spec.path} (type: {output_spec.type})")

        click.echo("")

        # Build and show library dependency graph
        fileset = BuildFileSet(build_ctx)
        build_ctx.populate_fileset(plan.source_fileset, fileset)

        lib_graph = fileset.library_dependency_graph()
        if lib_graph:
            click.echo(f"  Library dependencies:")
            for lib_name in sorted(lib_graph.keys()):
                deps = lib_graph[lib_name]
                if deps:
                    click.echo(f"    {lib_name} depends on:")
                    for dep in sorted(deps):
                        click.echo(f"      → {dep}")
                else:
                    click.echo(f"    {lib_name} (no dependencies)")
            click.echo("")

        # Show the actual build task graph by running dispatchers
        click.echo(f"  Build task graph:")
        click.echo("")

        # Set output group context
        build_ctx.set_output_group_context(
            topcell=plan.output_group.topcell,
            topcell_library=project_model.root_library_name
        )

        # Determine which backends to use (same logic as build)
        backend_modules_used = set()

        # Add backends that contributed passes
        for pass_metadata in plan.passes:
            for backend_module in backend_names:
                backend = backend_registry.get_backend(backend_module)
                contributed_passes = backend.contribute_passes(
                    plan.output_group.backend_config.get(backend_module, {}),
                    {output.type for output in plan.output_group.outputs}
                )
                if pass_metadata.pass_class in contributed_passes:
                    backend_modules_used.add(backend_module)
                    break

        # Add backends explicitly configured in backend_config
        for backend_module in plan.output_group.backend_config.keys():
            if backend_module in backend_names:
                backend_modules_used.add(backend_module)

        # Create dispatchers
        dispatcher_registry = DispatcherRegistry()
        for backend_module in backend_modules_used:
            backend = backend_registry.get_backend(backend_module)
            backend_config = plan.output_group.backend_config.get(backend_module, {})
            backend_config['output_dir'] = str(output_dir)
            dispatcher = backend.create_dispatcher(backend_config)
            dispatcher_registry.register(dispatcher)

        # Run dispatcher iteration to create tasks
        await run_dispatcher_iteration(
            build_ctx,
            fileset,
            dispatcher_registry,
            max_iterations=max_iterations
        )

        # Show the task graph
        _show_task_graph(build_ctx)


__all__ = ['build_project', 'show_graph_for_project']
