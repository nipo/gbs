"""Project loading and execution

Provides the Project class for loading, building, and managing GBS projects.
"""

import sys
import asyncio

from pathlib import Path
from typing import Optional, TYPE_CHECKING, AsyncIterable
from dataclasses import dataclass, field

from .model import ProjectModel
from ..logging import get_logger
from ..build import BuildContext
from ..build.task import ResourceTypology
from ..backend.protocol import Backend
from ..backend.registry import get_backend_registry
from ..backend.dispatcher import DispatcherRegistry, run_dispatcher_iteration
from ..repository.model import Repository, Library
from ..repository.model import SourceFileSet, Repository
from ..planner.planner import BuildPlan

# Avoid circular imports by using TYPE_CHECKING
if TYPE_CHECKING:
    from ..repository.model import Repository

logger = get_logger(__name__)


class LoadError(Exception):
    """Error loading project or configuration file"""
    pass


def _lazy_imports():
    """Lazy import to avoid circular dependencies"""
    from ..repository.loader import (
        load_project as load_project_model,
        load_repositories_from_project,
    )
    return load_project_model, load_repositories_from_project

class Project:
    """GBS Project execution context

    Manages a loaded project including its data model, repositories, and
    build configuration. Provides methods for building and managing the project.

    Attributes:
        model: The project data model (ProjectModel)
        repositories: List of loaded repositories
        path: Path to the project file
        gbs_config: Optional GBS configuration
    """
    model: ProjectModel
    repositories: list
    path: Optional[Path]
    gbs_config: Optional[any]

    def __init__(self,
                 model: ProjectModel,
                 repositories: list,
                 path: Optional[Path],
                 gbs_config: Optional[any],
                 max_parallel: Optional[int] = None):
        self.model = model
        self.repositories = repositories
        self.path = path
        self.gbs_config = gbs_config
        self.__realizations = None

        # Determine max_parallel using precedence chain:
        # 1. Explicitly provided parameter (command line will use this)
        # 2. Project config (model.max_parallel)
        # 3. GBS config (gbs_config.max_parallel)
        # 4. Default (4)
        if max_parallel is not None:
            self._max_parallel = max_parallel
        elif model.max_parallel is not None:
            self._max_parallel = model.max_parallel
        elif gbs_config is not None and gbs_config.max_parallel is not None:
            self._max_parallel = gbs_config.max_parallel
        else:
            self._max_parallel = 4  # Default

        # Shared semaphore for parallel execution across all output groups
        self._semaphore: Optional[asyncio.Semaphore] = None
    
    @property
    def semaphore(self) -> asyncio.Semaphore:
        """Get shared semaphore for all build contexts, creating it lazily if needed"""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_parallel)
        return self._semaphore

    def set_max_parallel(self, max_parallel: int):
        """Override max_parallel setting (e.g., from command line)

        Args:
            max_parallel: Maximum number of parallel tasks

        Raises:
            ValueError: If semaphore already initialized
        """
        if self._semaphore is not None:
            raise ValueError("Cannot change max_parallel after semaphore is initialized")
        self._max_parallel = max_parallel

    @classmethod
    def load_from_file(cls, path: Path, gbs_config=None) -> 'Project':
        """Load a project from a YAML file

        This is the primary factory method for creating Project instances.
        Loads the project definition and any referenced repositories.

        Args:
            path: Path to project.gbs.yaml file
            gbs_config: Optional GBSConfig for repository merging

        Returns:
            Project instance

        Raises:
            LoadError: If project or repositories cannot be loaded

        Example:
            >>> proj = Project.load_from_file(Path("project.gbs.yaml"))
            >>> proj.build()
        """
        # Import loader functions lazily to avoid circular imports
        load_project_model, load_repositories_from_project = _lazy_imports()

        logger.info(f"Loading project: {path}")

        # Load the project data model
        try:
            project_model = load_project_model(path, gbs_config=gbs_config)
        except Exception as e:
            raise LoadError(f"Failed to load project from {path}: {e}")

        # Load repositories specified in the project
        try:
            repositories = load_repositories_from_project(
                project_model.raw_config,
                path.parent,
                gbs_config=gbs_config
            )
        except Exception as e:
            logger.warning(f"Failed to load repositories: {e}")
            repositories = []

        return cls(
            model=project_model,
            repositories=repositories,
            path=path,
            gbs_config=gbs_config
        )

    @classmethod
    def find_and_load(cls, start_path: Optional[Path] = None, gbs_config=None) -> 'Project':
        """Find and load a project from the current or parent directories

        Searches for project.gbs.yaml starting from start_path and walking
        up the directory tree.

        Args:
            start_path: Starting directory (defaults to current working directory)
            gbs_config: Optional GBSConfig

        Returns:
            Project instance

        Raises:
            LoadError: If no project file is found
        """
        if start_path is None:
            start_path = Path.cwd()

        current = start_path.resolve()

        # Walk up the directory tree
        while True:
            project_file = current / "project.gbs.yaml"
            if project_file.exists():
                return cls.load_from_file(project_file, gbs_config=gbs_config)

            # Move to parent directory
            parent = current.parent
            if parent == current:
                # Reached filesystem root
                break
            current = parent

        raise LoadError(f"No project.gbs.yaml found in {start_path} or parent directories")

    async def realizations(self) -> AsyncIterable: # [PlanRealization]
        if self.__realizations is not None:
            for p in self.__realizations:
                yield p

        backend_registry = get_backend_registry()

        # Plan build for all output groups
        logger.info("")
        logger.info(f"Planning build for {len(self.model.output_groups)} output group(s)...")
        backends = backend_registry.get_all_backends()
        backend_names = backend_registry.list_backends()

        # Create synthetic repository from project's root partition for planning
        project_repo = Repository(name=self.model.name, root=Path("."))
        project_library = Library(name=self.model.root_library_name)
        project_library.add_partition(self.model.root_partition)
        project_repo.add_library(project_library)

        # Include project repo in repositories for planning
        all_repositories = [project_repo] + self.repositories

        from ..planner.planner import BuildPlanner
        planner = BuildPlanner(all_repositories, backends, self.model.raw_config, self.gbs_config)
        self.__realizations = []

        for output_group in self.model.output_groups:
            plan = planner.plan(output_group)
            realization = PlanRealization(
                project = self,
                plan = plan,
                source_fileset = self.model.resolve(plan.repositories, plan.filter_vars),
            )

            await realization.dispatch()
            
            self.__realizations.append(plan)

            yield realization
            
    async def build(
        self,
        show_progress: bool = True
    ):
        """Build the project

        Executes the build for all output groups.

        Args:
            show_progress: Whether to show progress bars (default: True)

        Raises:
            Exception: If build fails

        Example:
            >>> proj = Project.load_from_file(Path("project.gbs.yaml"))
            >>> await proj.build()
        """
        async for realization in self.realizations():
            # Execute build tasks
            logger.info(f"  Realizing build plan {realization.plan}...")
            await realization.execute(
                show_progress=(sys.stdout.isatty() and show_progress)
            )

    async def clean(
        self,
        dry_run: bool = False
    ):
        """Clean the project
        """
        async for realization in self.realizations():
            await realization.clean(dry_run)
            
    async def show_graph(self):
        """Show build dependency graph

        Displays detailed information about the build plan including source files,
        passes, outputs, library dependencies, and build task graph.

        Example:
            >>> proj = Project.load_from_file(Path("project.gbs.yaml"))
            >>> await proj.show_graph()
        """
        async for realization in self.realizations():
            # Execute build tasks
            realization.task_graph_show()

    async def get_source_files(
        self,
        output_group_names: Optional[list[str]] = None
    ) -> dict[str, set[Path]]:
        """Get source files for output groups without building

        Runs planning phase only to extract source file lists.
        Useful for determining if a project needs rebuilding.

        Args:
            output_group_names: Which output groups to plan for (None = all)

        Returns:
            Dict mapping output_group_name -> set of source file paths

        Example:
            >>> sources = await project.get_source_files(["simulation"])
            >>> sources
            {'simulation': {Path('src/top.vhd'), Path('src/uart.vhd')}}
        """
        result = {}

        # Filter output groups if names provided
        output_groups_to_plan = self.model.output_groups
        if output_group_names is not None:
            output_groups_to_plan = [
                og for og in self.model.output_groups
                if og.name in output_group_names
            ]

        # Run planning for each output group
        async for realization in self.realizations():
            # Check if this output group should be included
            if output_group_names is None or realization.plan.output_group.name in output_group_names:
                # Extract all source files from the source fileset
                source_files = set(realization.source_fileset.get_all_files())
                result[realization.plan.output_group.name] = source_files

        return result

    async def needs_rebuild(
        self,
        changed_files: set[Path],
        output_group_names: Optional[list[str]] = None,
        always_rebuild_patterns: Optional[list[str]] = None
    ) -> tuple[bool, str]:
        """Check if project needs rebuild based on changed files

        Args:
            changed_files: Set of changed file paths (absolute)
            output_group_names: Which output groups to check
            always_rebuild_patterns: Glob patterns that always trigger rebuild

        Returns:
            (needs_rebuild, reason) tuple

        Example:
            >>> needs, reason = await project.needs_rebuild(
            ...     changed_files={Path("src/uart.vhd").resolve()},
            ...     always_rebuild_patterns=["**/*.gbs.yaml"]
            ... )
            >>> needs, reason
            (True, "Source file changed in simulation: uart.vhd")
        """
        from pathlib import PurePath

        # Check always_rebuild_patterns first
        if always_rebuild_patterns:
            for changed_file in changed_files:
                for pattern in always_rebuild_patterns:
                    if PurePath(changed_file).match(pattern):
                        return (True, f"Always-rebuild pattern matched: {pattern} ({changed_file.name})")

        # Get source files for relevant output groups
        sources_by_group = await self.get_source_files(output_group_names)

        # Check if any changed file is in the source files
        for group_name, source_files in sources_by_group.items():
            # Resolve all source files to absolute paths for comparison
            resolved_sources = {f.resolve() for f in source_files}

            # Check for overlap with changed files
            overlapping_files = changed_files & resolved_sources

            if overlapping_files:
                # Get first overlapping file for message
                first_file = next(iter(overlapping_files))
                return (True, f"Source file changed in {group_name}: {first_file.name}")

        # No overlap found
        return (False, "No source files changed")

    def __str__(self) -> str:
        return f"Project({self.model.name}, {len(self.model.output_groups)} output groups, {len(self.repositories)} repositories)"

class PlanRealization:
    def __init__(self,
                 project: Project,
                 plan: BuildPlan,
                 source_fileset: SourceFileSet):
        backend_registry = get_backend_registry()
        backends = backend_registry.get_all_backends()
        backend_names = backend_registry.list_backends()


        self.project = project
        self.plan = plan
        self.source_fileset = source_fileset

        # Use the project's shared semaphore so all output groups share parallelism limit
        self.build_ctx = BuildContext(
            project = self.project.model,
            gbs_config = self.project.gbs_config,
            semaphore = self.project.semaphore)

        num_files = len(self.source_fileset.get_all_files())
        num_libs = len(self.source_fileset.libraries)
        logger.info(f"  Output group '{self.plan.output_group.name}':")
        logger.info(f"    Topcell: {self.plan.output_group.topcell}")
        logger.info(f"    Sources: {num_files} files in {num_libs} libraries")
        logger.info(f"    Passes: {self.plan.passes}")
        logger.info(f"    Outputs: {len(self.plan.output_group.outputs)}")
        logger.info(f"Dispatching output group '{self.plan.output_group.name}'...")
        
        # Set topcell and library for this output group
        self.build_ctx.set_output_group_context(
            topcell = self.plan.output_group.topcell,
            topcell_library = self.project.model.root_library_name,
            output_group = self.plan.output_group
        )

        # Populate pending work queue from plan's source fileset
        self.build_ctx.populate_pending(self.source_fileset)

        # Add output goals to pending queue
        # These are the desired outputs that dispatchers will work backwards from
        for output in self.plan.output_group.outputs:
            output_path = output.path.resolve()
            output_resource = self.build_ctx.get_resource(
                output_path,
                file_type=output.type,
                typology=ResourceTypology.OUTPUT,
                generated_by=None,  # No producer yet
            )
            self.build_ctx.add_pending(output_resource)
            logger.debug(f"  Added output goal: {output.type} -> {output_path}")

        # Determine which backends to use:
        # 1. Backends that contributed passes (main backend doing the work)
        # 2. Backends configured in backend_config (may be post-processors like NSL CDC)
        backend_modules_used = set()

        # Create dispatcher registry for this plan
        self.dispatcher_registry = DispatcherRegistry()
        for pass_metadata in self.plan.passes:
            contributed_dispatchers = pass_metadata.pass_obj.dispatchers()
            for dispatcher in contributed_dispatchers:
                self.dispatcher_registry.register(dispatcher)
                logger.info(f"  Registered dispatcher: {dispatcher.name}")

        from ..plugins import get_plugin_registry
        plugin_registry = get_plugin_registry()

        for plugin in plugin_registry.get_all_plugins():
            for dispatcher in plugin.generic_dispatchers():
                self.dispatcher_registry.register(dispatcher)
                logger.info(f"  Registered generic dispatcher: {dispatcher.name}")

            
    async def dispatch(self, max_iterations = 10) -> None:
        # Run dispatcher iteration
        iterations = await run_dispatcher_iteration(
            self.build_ctx,
            self.dispatcher_registry,
            max_iterations=max_iterations
        )
        logger.info(f"  Converged after {iterations} iteration(s)")

    async def execute(self, show_progress: bool = False):
        # Launch all steps and await them
        if self.build_ctx.steps:
            import asyncio
            async with self.build_ctx.build():
                # build() calls _launch() which launches all steps
                # Now await all running tasks
                if show_progress:
                    try:
                        from ..ui.progress import run_with_progress_tasks
                        await run_with_progress_tasks(self.build_ctx)
                    except ImportError:
                        await asyncio.gather(*self.build_ctx.running)
                else:
                    await asyncio.gather(*self.build_ctx.running)

    def task_graph_show(self, print_func = None):
        from ..build.task import Resource, VirtualResource, Task

        if print_func is None:
            import click
            print_func = click.echo
        
        # Organize steps by type
        resources = []
        virtual_resources = []
        tasks = []

        for step in self.build_ctx.steps:
            if isinstance(step, Resource):
                resources.append(step)
            elif isinstance(step, VirtualResource):
                virtual_resources.append(step)
            elif isinstance(step, Task):
                tasks.append(step)

        print_func(f"    Resources ({len(resources)} files):")
        for resource in sorted(resources, key=lambda r: r.name):
            deps = [d.name for d in resource.depends_on if d in tasks]
            if deps:
                print_func(f"      {resource.name}")
                for dep_name in sorted(deps):
                    print_func(f"        ← produced by: {dep_name}")
            else:
                print_func(f"      {resource.name} ({resource.metadata.get('file_type')} source in {resource.metadata.get('library')})")

        print_func("")
        print_func(f"    Tasks ({len(tasks)} tasks):")
        for task in sorted(tasks, key=lambda t: t.name):
            print_func(f"      {task.name}")

            # Show what this task depends on (inputs)
            input_resources = [d for d in task.depends_on if isinstance(d, (Resource, VirtualResource))]
            if input_resources:
                for dep in sorted(input_resources, key=lambda r: r.name):
                    print_func(f"        → reads: {dep.name}")

            # Show what depends on this task (outputs)
            output_resources = [e for e in task.expected_by if isinstance(e, (Resource, VirtualResource))]
            if output_resources:
                for exp in sorted(output_resources, key=lambda r: r.name):
                    print_func(f"        ← produces: {exp.name}")

        if virtual_resources:
            print_func("")
            print_func(f"    Virtual resources ({len(virtual_resources)}):")
            for vr in sorted(virtual_resources, key=lambda r: r.name):
                print_func(f"      {vr.name}")

    async def clean(self, dry_run: bool = False):
        import click
        to_clean = set()
        to_clean |= self.build_ctx.to_clean()

        cur = Path(".").resolve()
        for f in to_clean:
            assert cur in f.parents

        if dry_run:
            for f in sorted(to_clean):
                click.echo(f"- {f}")
        else:
            for f in to_clean:
                if f.is_dir():
                    for root, dirs, files in f.walk(top_down=False):
                        for name in files:
                            (root / name).unlink()
                        for name in dirs:
                            (root / name).rmdir()
                else:
                    f.unlink(missing_ok = True)
                    
__all__ = [
    'LoadError',
    'Project',
    'PlanRealization',
]
