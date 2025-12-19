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
from ..protocol import Backend
from ..backend.registry import get_backend_registry
from ..backend import DispatcherRegistry, run_dispatcher_iteration
from ..repository.model import SourceFileSet, Repository

# Avoid circular imports by using TYPE_CHECKING
if TYPE_CHECKING:
    from ..planner.planner import BuildPlan

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

from ..ui.reporter import UIReporter

class Project(UIReporter):
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
                 max_parallel: Optional[int] = None,
                 parent_reporter: Optional[UIReporter] = None):
        # Initialize UIReporter (parent is Suite if building within a suite)
        UIReporter.__init__(self,
            reporter_name=f"Project({model.name})" if model.name else "Project",
            parent_reporter=parent_reporter
        )

        self.model = model
        self.repositories = repositories
        self.path = path
        self.gbs_config = gbs_config
        self.base_output_path = None  # Can be set for suite builds to scope output directories
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

    def set_base_output_path(self, base_output_path: Path):
        """Set base output path for this project

        Used by suite executor to scope project builds to separate directories.

        Args:
            base_output_path: Base directory for build outputs
        """
        self.base_output_path = base_output_path

    @classmethod
    def load_from_file(cls, path: Path, gbs_config=None, parent_reporter: Optional[UIReporter] = None) -> 'Project':
        """Load a project from a YAML file

        This is the primary factory method for creating Project instances.
        Loads the project definition and any referenced repositories.

        Args:
            path: Path to project.gbs.yaml file
            gbs_config: Optional GBSConfig for repository merging
            parent_reporter: Optional parent UIReporter (e.g., SuiteExecutor)

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
            project_model = load_project_model(path)
        except Exception as e:
            raise LoadError(f"Failed to load project from {path}: {e}")

        # Load repositories specified in the project
        repositories = load_repositories_from_project(
            project_model.raw_config,
            path.parent,
            gbs_config=gbs_config
        )

        return cls(
            model=project_model,
            repositories=repositories,
            path=path,
            gbs_config=gbs_config,
            parent_reporter=parent_reporter
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

        # Include all repositories for planning
        all_repositories = self.repositories

        from ..planner.planner import BuildPlanner
        planner = BuildPlanner(
            all_repositories,
            backends,
            self.model.raw_config,
            self.gbs_config,
            root_partition_template=self.model.root_partition_template,
            parent_reporter=self
        )
        self.__realizations = []

        for output_group in self.model.output_groups:
            plan = planner.plan(output_group)

            # Evaluate root partition template with this output group's filter vars
            root_partition = self.model.root_partition_template.evaluate(
                plan.filter_vars,
                self.model.root_library_name
            )

            # Resolve dependencies
            from ..repository.resolver import DependencyResolver
            resolver = DependencyResolver(plan.repositories, plan.filter_vars)
            source_fileset = resolver.resolve([root_partition])

            realization = PlanRealization(
                project = self,
                plan = plan,
                source_fileset = source_fileset,
            )

            await realization.dispatch()

            self.__realizations.append(realization)

            yield realization
            
    async def build(self):
        """Build the project

        Executes the build for all output groups.

        Raises:
            Exception: If build fails

        Example:
            >>> proj = Project.load_from_file(Path("project.gbs.yaml"))
            >>> await proj.build()
        """
        # Build each realization (output group)
        async for realization in self.realizations():
            logger.info(f"  Realizing build plan {realization.plan}...")
            await realization.execute()

    async def clean(
        self,
        dry_run: bool = False
    ):
        """Clean the project

        Delegates to each output group's realization to clean their build artifacts.
        Dispatchers decide what paths to clean based on what they created.
        If base output path becomes empty, it is also removed.

        Args:
            dry_run: If True, show what would be deleted without actually deleting
        """
        import click

        # Track all cleaned paths to check if base output path becomes empty
        all_cleaned_paths = set()

        # Clean each realization by asking its dispatchers what to clean
        async for realization in self.realizations():
            cleaned_paths = realization.clean(dry_run)
            all_cleaned_paths |= cleaned_paths
            
    async def show_graph(self, diagram_path: Optional[Path] = None):
        """Show build dependency graph

        Displays detailed information about the build plan including source files,
        passes, outputs, library dependencies, and build task graph.

        Args:
            diagram_path: Optional path to save graphviz diagram (SVG format)

        Example:
            >>> proj = Project.load_from_file(Path("project.gbs.yaml"))
            >>> await proj.show_graph()
            >>> await proj.show_graph(diagram_path=Path("build_graph.svg"))
        """
        async for realization in self.realizations():
            # Execute build tasks
            if diagram_path:
                realization.task_graph_diagram(diagram_path)
            else:
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
                # Extract all source file paths from the source fileset
                source_files = {sf.path for sf in realization.source_fileset.get_all_files()}
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
                 plan: 'BuildPlan',
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
            semaphore = self.project.semaphore,
            base_output_path = self.project.base_output_path,
            parent_reporter = self.plan)

        num_files = len(self.source_fileset.get_all_files())
        # Extract unique library names from partition names (library.partition format)
        library_names = {p.split('.', 1)[0] for p in self.source_fileset.partitions}
        num_libs = len(library_names)
        num_partitions = len(self.source_fileset.partitions)
        logger.info(f"  Output group '{self.plan.output_group.name}':")
        logger.info(f"    Topcell: {self.plan.output_group.topcell}")
        logger.info(f"    Sources: {num_files} files in {num_partitions} partitions ({num_libs} libraries)")
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
        # Pass types_with_library from plan so only declared types get library classification
        self.build_ctx.populate_pending(self.source_fileset, self.plan.types_with_library)

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
            contributed_dispatchers = pass_metadata.pass_obj.dispatchers(self.build_ctx)
            for dispatcher in contributed_dispatchers:
                self.dispatcher_registry.register(dispatcher)
                logger.info(f"  Registered dispatcher: {dispatcher.name}")

        from ..plugins import get_plugin_registry
        plugin_registry = get_plugin_registry()

        for plugin in plugin_registry.get_all_plugins():
            for dispatcher in plugin.generic_dispatchers(self.build_ctx):
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

    async def execute(self):
        # Launch all steps and await them
        if self.build_ctx.steps:
            import asyncio
            from ..build.task import BuildError
            async with self.build_ctx.build():
                # build() calls _launch() which launches all steps
                # Now await all running tasks
                await asyncio.gather(*self.build_ctx.running)

            # After cleanup, check if build failed and raise if needed
            if self.build_ctx.build_failed:
                raise BuildError("Build failed")

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
                print_func(f"      {resource.name} ({resource.file_type} source in {resource.library})")

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

    def task_graph_diagram(self, output_path: Path):
        """Generate a graphviz diagram of the build graph

        Args:
            output_path: Path to save the SVG diagram
        """
        from ..build.task import Resource, VirtualResource, Task, ResourceTypology
        import hashlib
        from collections import defaultdict

        try:
            from graphviz import Digraph
        except ImportError:
            raise ImportError("graphviz package required for diagram generation. Install with: pip install graphviz")

        # Get file URL template from config (same as used for OSC-8 links)
        file_url_template = self.build_ctx.gbs_config.file_url_template

        # Organize steps by type
        resources = []
        virtual_resources = []
        tasks = []

        for step in self.build_ctx.steps:
            if isinstance(step, VirtualResource):
                virtual_resources.append(step)
            elif isinstance(step, Resource):
                resources.append(step)
            elif isinstance(step, Task):
                tasks.append(step)

        # Create digraph
        dot = Digraph(comment='Build Graph')
        dot.attr(rankdir='TB')  # Top to bottom layout

        # Helper function to get stable hue from task name
        def get_task_hue(name: str) -> str:
            """Get a stable hue (0-360) from task name hash"""
            hash_val = int(hashlib.md5(name.encode()).hexdigest(), 16)
            hue = hash_val % 360
            return f"{hue / 360.0} 0.3 0.95"  # HSV: hue, 30% saturation, 95% value

        # Helper function to create file URL from path
        def get_file_url(file_path: Path) -> str:
            """Create file URL using template"""
            abs_path = file_path.resolve()
            return file_url_template.format(
                path=abs_path,
                line=1,
                column=0
            )

        # Helper function to group source resources by file type
        def group_sources_by_task(task: Task) -> dict:
            """Group source resources that are inputs to a task by file_type"""
            grouped = defaultdict(list)
            for dep in task.depends_on:
                if isinstance(dep, Resource) and dep.typology == ResourceTypology.SOURCE:
                    file_type = dep.file_type or "unknown"
                    grouped[file_type].append(dep)
            return grouped

        # Track which resources are used (to avoid creating nodes for unused ones)
        used_resources = set()
        for task in tasks:
            for dep in task.depends_on:
                if isinstance(dep, Resource):
                    used_resources.add(dep)
            for exp in task.expected_by:
                if isinstance(exp, Resource):
                    used_resources.add(exp)

        # Add task nodes
        for task in tasks:
            hue = get_task_hue(task.name)
            dot.node(
                f"task_{id(task)}",
                label=task.description or task.name,
                shape="cds",
                style="filled",
                fillcolor=hue
            )

        # Process resources and create grouped/individual nodes
        resource_to_node = {}  # Map resource to its node ID

        for task in tasks:
            # Group source inputs if more than 5 of same type
            grouped_sources = group_sources_by_task(task)

            for file_type, source_list in grouped_sources.items():
                if len(source_list) > 5:
                    # Create a grouped node
                    group_id = f"group_{id(task)}_{file_type}"
                    label = f"{len(source_list)} {file_type} files"
                    # Create tooltip with list of basenames
                    tooltip = "\\n".join(sorted([res.path.name for res in source_list]))
                    dot.node(
                        group_id,
                        label=label,
                        shape="folder",
                        style="filled",
                        fillcolor="0.6 0.5 1.0",  # Blue with 50% saturation
                        tooltip=tooltip
                    )
                    # Map all these resources to the group node
                    for res in source_list:
                        resource_to_node[res] = group_id
                else:
                    # Create individual nodes
                    for res in source_list:
                        if res not in resource_to_node:
                            node_id = f"res_{id(res)}"
                            label = res.path.name
                            file_url = get_file_url(res.path)
                            dot.node(
                                node_id,
                                label=label,
                                shape="note",
                                style="filled",
                                fillcolor="0.6 0.5 1.0",  # Blue
                                URL=file_url
                            )
                            resource_to_node[res] = node_id

        # Add nodes for non-source resources (INTERMEDIATE and OUTPUT)
        for res in used_resources:
            if res.typology != ResourceTypology.SOURCE and res not in resource_to_node:
                node_id = f"res_{id(res)}"
                label = res.path.name

                # Determine color based on typology
                if res.typology == ResourceTypology.OUTPUT:
                    fillcolor = "0.33 0.5 1.0"  # Green with 50% saturation
                else:  # INTERMEDIATE
                    fillcolor = "0.16 0.5 1.0"  # Yellow with 50% saturation

                file_url = get_file_url(res.path)
                dot.node(
                    node_id,
                    label=label,
                    shape="note",
                    style="filled",
                    fillcolor=fillcolor,
                    URL=file_url
                )
                resource_to_node[res] = node_id

        # Add nodes for virtual resources
        # Virtual resources are represented as octagon shapes
        virtual_to_node = {}
        for vres in virtual_resources:
            node_id = f"vres_{id(vres)}"
            label = vres.name
            dot.node(
                node_id,
                label=label,
                shape="octagon",
                style="filled",
                fillcolor="0.83 0.3 0.95"  # Purple-ish with 30% saturation
            )
            virtual_to_node[vres] = node_id

        # Add edges: resource/virtual -> task (task reads resource/virtual)
        # Use task.inputs instead of task.depends_on to show only explicit inputs
        # Track edges to avoid duplicates when resources are grouped
        edges_drawn = set()
        for task in tasks:
            task_node = f"task_{id(task)}"
            for inp in task.inputs:
                if isinstance(inp, VirtualResource) and inp in virtual_to_node:
                    edge = (virtual_to_node[inp], task_node)
                    if edge not in edges_drawn:
                        dot.edge(edge[0], edge[1])
                        edges_drawn.add(edge)
                elif isinstance(inp, Resource) and inp in resource_to_node:
                    edge = (resource_to_node[inp], task_node)
                    if edge not in edges_drawn:
                        dot.edge(edge[0], edge[1])
                        edges_drawn.add(edge)

        # Add edges: task -> resource/virtual (task produces resource/virtual)
        # Use task.outputs instead of task.expected_by to show only explicit outputs
        for task in tasks:
            task_node = f"task_{id(task)}"
            for out in task.outputs:
                if isinstance(out, VirtualResource) and out in virtual_to_node:
                    edge = (task_node, virtual_to_node[out])
                    if edge not in edges_drawn:
                        dot.edge(edge[0], edge[1])
                        edges_drawn.add(edge)
                elif isinstance(out, Resource) and out in resource_to_node:
                    edge = (task_node, resource_to_node[out])
                    if edge not in edges_drawn:
                        dot.edge(edge[0], edge[1])
                        edges_drawn.add(edge)

        # Add edges: task -> task (task dependencies)
        # These are lighter gray to distinguish from resource dependencies
        for task in tasks:
            task_node = f"task_{id(task)}"
            for dep in task.depends_on:
                if isinstance(dep, Task):
                    dep_task_node = f"task_{id(dep)}"
                    edge = (dep_task_node, task_node)
                    if edge not in edges_drawn:
                        dot.edge(edge[0], edge[1], color="gray60")
                        edges_drawn.add(edge)

        # Add implicit dependency edges: any BuildStep -> any BuildStep (not in inputs/outputs but in depends_on)
        # These are very light gray (30% gray) to show dependencies without cluttering
        # Collect all explicit relationships
        for step in self.build_ctx.steps:
            if isinstance(step, Task):
                step_node = f"task_{id(step)}"
                explicit_inputs = set(step.inputs)
                explicit_outputs = set(step.outputs)

                # Find implicit dependencies (in depends_on but not in explicit inputs/outputs)
                for dep in step.depends_on:
                    if dep not in explicit_inputs and dep not in explicit_outputs:
                        dep_node = None
                        if isinstance(dep, VirtualResource) and dep in virtual_to_node:
                            dep_node = virtual_to_node[dep]
                        elif isinstance(dep, Resource) and dep in resource_to_node:
                            dep_node = resource_to_node[dep]
                        elif isinstance(dep, Task):
                            # Task->task dependencies are already handled above
                            continue

                        if dep_node:
                            edge = (dep_node, step_node)
                            if edge not in edges_drawn:
                                dot.edge(edge[0], edge[1], color="gray30", style="dashed")
                                edges_drawn.add(edge)

        # Render to SVG
        dot.render(output_path.stem, directory=output_path.parent, format='svg', cleanup=True)
        logger.info(f"Diagram saved to {output_path}")

    def clean(self, dry_run: bool = False) -> set:
        """Clean build artifacts for this realization

        Asks each dispatcher what paths it wants cleaned and returns them.

        Args:
            dry_run: If True, just return paths without cleaning

        Returns:
            Set of paths that were cleaned (or would be cleaned if dry_run)
        """
        import click
        from ..utils import clean_paths

        # Collect paths to clean from all dispatchers
        paths_to_clean = set()
        for dispatcher in self.dispatcher_registry:
            dispatcher_paths = dispatcher.get_clean_paths()
            paths_to_clean |= dispatcher_paths
        paths_to_clean |= self.build_ctx.to_clean()

        # Clean paths using utility function
        return clean_paths(paths_to_clean, dry_run=dry_run, echo_func=click.echo)

__all__ = [
    'LoadError',
    'Project',
    'PlanRealization',
]
