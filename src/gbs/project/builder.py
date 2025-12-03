"""Project building functionality

Core build execution logic for GBS projects.
"""

import sys
from pathlib import Path
from typing import Optional

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


__all__ = ['build_project']
