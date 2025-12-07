"""AsyncIO-Native Task System for GBS"""

from __future__ import annotations
from pathlib import Path
from typing import Any
from ..logging import get_logger, get_log_file
from .message import *
from .task import VirtualResource, Resource
import asyncio

class BuildContext:
    """Shared build context passed to all tasks and resources

    Contains:
    - Semaphore for parallel execution control
    - Project configuration
    - Build settings
    - Resource registry
    - All steps
    """

    def __init__(
        self,
        max_parallel: int = 4,
        project_config: Optional[dict[str, Any]] = None,
        project: Optional[Any] = None,
        gbs_config: Optional[Any] = None,
        semaphore: Optional[asyncio.Semaphore] = None
    ):
        """Initialize build context

        Args:
            max_parallel: Maximum number of tasks to run in parallel (ignored if semaphore provided)
            project_config: Optional project configuration (deprecated, use project instead)
            project: Optional Project instance
            gbs_config: Optional GBSConfig instance for tool lookup
            semaphore: Optional shared semaphore for parallel execution control
                      (if provided, max_parallel is ignored)
        """
        self._max_parallel = max_parallel
        self._semaphore: Optional[asyncio.Semaphore] = semaphore  # Use provided semaphore or None
        self._resources: dict[Path, 'Resource'] = {}
        self._virtual_resources: dict[str, 'VirtualResource'] = {}
        self.project_config = project_config or {}
        self.project = project
        self.gbs_config = gbs_config
        self.steps = set()
        self.running = set()
        self.logger = get_logger("BuildContext")
        self.__messages: list[ToolMessage] = []

        # Output group context (set when building a specific output group)
        self._topcell: Optional[str] = None
        self._topcell_library: Optional[str] = None
        self._output_group: Optional[Any] = None  # OutputGroup instance
        self.output_path = Path("gbs-build") / "undefined_yet"

        # Progress tracking
        self._progress_condition: Optional[asyncio.Condition] = None
        self._progress_version = 0

        # Pending work queue (merged from BuildFileSet)
        self._pending: dict[Path, 'Resource'] = {}  # path -> Resource (pending work)
        self._pending_modification_serial = 0
        self._pending_dependents: dict[Path, set['Resource']] = {}  # path -> dependents
        self._pending_source_deps: dict[Path, set['Resource']] = {}  # path -> source dependencies (partition deps)

        # Build result flag
        self.build_failed = False

    def messages_get(
        self,
        severity: Optional[MessageSeverity] = None,
        min_severity: Optional[MessageSeverity] = None,
    ) -> list[ToolMessage]:
        """Get messages collected by this step

        Args:
            severity: If provided, only return messages with this exact severity
            min_severity: If provided, only return messages at or above this severity

        Returns:
            List of ToolMessage objects matching the filter criteria
        """
        if severity is not None:
            return [msg for msg in self.__messages if msg.severity == severity]
        elif min_severity is not None:
            # Define severity ordering
            severity_order = [
                MessageSeverity.DEBUG,
                MessageSeverity.NOTICE,
                MessageSeverity.INFO,
                MessageSeverity.WARNING,
                MessageSeverity.ERROR,
                MessageSeverity.FATAL,
            ]
            min_index = severity_order.index(min_severity)
            return [
                msg for msg in self.__messages
                if severity_order.index(msg.severity) >= min_index
            ]
        return self.__messages.copy()

    def message_add(self, message: ToolMessage):
        """
        Add a message to the build context
        """
        self.__messages.append(message)

    @property
    def semaphore(self) -> asyncio.Semaphore:
        """Get semaphore, creating it lazily if needed"""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_parallel)
        return self._semaphore

    def get_resource(
        self,
        path: Path,
        file_type: str | None = None,
        library: str | None = None,
        file_type_version: str | None = None,
        typology: 'ResourceTypology | None' = None,
        generated_by: str | None = None,
        metadata: dict[str, str] | None = None
    ) -> 'Resource':
        """Get or create a Resource for a file path (singleton)

        Args:
            path: File path
            file_type: Type of file (e.g., 'vhdl', 'verilog')
            library: Library name for HDL files
            file_type_version: File type version
            typology: Resource typology (SOURCE, INTERMEDIATE, OUTPUT), defaults to INTERMEDIATE
            generated_by: Backend name that generated this file
            metadata: Additional backend-specific metadata

        Returns:
            Resource instance (same instance for same path)
        """
        from .task import ResourceTypology

        path = path.resolve()  # Normalize path
        if path not in self._resources:
            # Use provided typology or default to INTERMEDIATE
            if typology is None:
                typology = ResourceTypology.INTERMEDIATE

            r = Resource(
                self, path,
                file_type=file_type,
                library=library,
                file_type_version=file_type_version,
                typology=typology,
                generated_by=generated_by
            )
            if metadata:
                r.metadata.update(metadata)
            self._resources[path] = r
        else:
            # Update existing resource if new metadata provided
            r = self._resources[path]
            if file_type is not None:
                r.file_type = file_type
            if library is not None:
                r.library = library
            if file_type_version is not None:
                r.file_type_version = file_type_version
            if typology is not None:
                r.typology = typology
            if generated_by is not None:
                r.generated_by = generated_by
            if metadata:
                r.metadata.update(metadata)

        return self._resources[path]

    def get_virtual_resource(self, name: str) -> 'VirtualResource':
        """Get or create a VirtualResource (singleton)

        Args:
            name: Unique name for virtual resource

        Returns:
            VirtualResource instance (same instance for same name)
        """
        if name not in self._virtual_resources:
            self._virtual_resources[name] = VirtualResource(self, name)
        return self._virtual_resources[name]

    def step_register(self, step: "BuildStep") -> None:
        self.steps.add(step)

    def set_output_group_context(self, topcell: str, topcell_library: Optional[str] = None, output_group: Optional[Any] = None):
        """Set the current output group build context

        Args:
            topcell: Top-level entity/module name for this output group
            topcell_library: Library containing topcell (defaults to 'work')
            output_group: The OutputGroup being built (for output path resolution)
        """
        self._topcell = topcell
        self._topcell_library = topcell_library or (self.project.root_library_name if self.project else "work")
        self._output_group = output_group
        self.output_path = Path("gbs-build") / output_group.name

    def get_topcell(self) -> Optional[str]:
        """Get the current output group topcell name

        Returns:
            Topcell name or None if not set
        """
        return self._topcell

    def get_topcell_library(self) -> Optional[str]:
        """Get the library name containing the topcell

        Returns:
            Topcell library name or None if not set
        """
        return self._topcell_library

    def get_output_group(self) -> Optional[Any]:
        """Get the current output group being built

        Returns:
            OutputGroup instance or None if not set
        """
        return self._output_group

    def get_tool(self, identifier: str, required: bool = True) -> Optional[dict[str, Any]]:
        """Get tool configuration by identifier

        Args:
            identifier: Tool identifier in format 'name' or 'name:variant'
            required: If True, raise error if tool not found

        Returns:
            Tool config dict, or None if not found and not required

        Raises:
            BuildError: If required=True and tool not found

        Examples:
            >>> ctx.get_tool("ghdl:llvm")  # Specific variant
            {'executable': '/usr/bin/ghdl'}
            >>> ctx.get_tool("gcc")  # Any variant
            {'executable': 'gcc'}
        """
        from .task import BuildError

        if self.gbs_config is None:
            if required:
                raise BuildError(f"Tool '{identifier}' requested but no GBS config loaded")
            return None

        tool = self.gbs_config.get_tool(identifier)

        if tool is None:
            if required:
                raise BuildError(f"Tool '{identifier}' not found in configuration")
            return None

        return tool.config

    @property
    def progress_condition(self) -> asyncio.Condition:
        """Get progress condition, creating it lazily if needed"""
        if self._progress_condition is None:
            self._progress_condition = asyncio.Condition()
        return self._progress_condition

    async def notify_progress_update(self):
        """Notify all watchers that some task's progress changed

        Called by tasks when they update their own progress.
        Wakes all UI coroutines waiting for updates.
        """
        async with self.progress_condition:
            self._progress_version += 1
            self.progress_condition.notify_all()

    async def wait_for_progress_update(self) -> int:
        """Wait for any task to update its progress

        Returns:
            Current version number (increments on each update)
        """
        async with self.progress_condition:
            await self.progress_condition.wait()
            return self._progress_version

    def get_progress_version(self) -> int:
        """Get current progress version (non-blocking)"""
        return self._progress_version

    async def _launch(self) -> None:
        self.logger.debug("Launch")
        for s in self.steps:
            self.running.add(s.launch())

    async def progress_bar(self):
        """Show progress bar for build (requires click library)"""
        if click is None:
            return
        pending = self.running
        with click.progressbar(length = len(pending), label = "Building") as bar:
            while pending:
                done, pending = await asyncio.wait(pending, return_when = asyncio.FIRST_COMPLETED)
                bar.update(len(done))

    async def _cleanup(self) -> None:
        self.logger.debug("Cleanup")

        # Collect failed steps from BuildStep futures
        # (not from running tasks, since we catch exceptions in execute())
        failed_steps = []
        for step in self.steps:
            if step.done():
                try:
                    exc = step.exception()  # Retrieve to suppress asyncio warning
                    if exc is not None:
                        failed_steps.append((step, exc))
                except Exception:
                    pass  # Ignore errors when retrieving

        # Cancel any tasks that are still running
        for p in self.running:
            if not p.done():
                p.cancel()

        # If build failed, print structured error summary
        if failed_steps:
            self._print_failure_summary(failed_steps)
            # Set a flag so execute() knows the build failed
            self.build_failed = True
        else:
            # Success - just print warnings as before
            for m in self.messages_get(min_severity = MessageSeverity.WARNING):
                m.pprint()
            self.build_failed = False

    def _print_failure_summary(self, failed_steps: list[tuple['BuildStep', Exception]]):
        """Print structured summary of build failures

        Args:
            failed_steps: List of (BuildStep, Exception) tuples for failed steps
        """
        import click
        from .task import Task, Resource, PrerequisiteFailed, MissingToolError, BuildError

        # First, print all warnings (not just from failed steps)
        warnings = self.messages_get(severity=MessageSeverity.WARNING)
        if warnings:
            click.echo("\n" + click.style("Build Warnings:", fg="yellow", bold=True))
            for m in warnings:
                m.pprint()

        # Print failure summary
        click.echo("\n" + click.style("Build Failed!", fg="red", bold=True))
        click.echo()

        # Find root cause failures (not dependency failures)
        root_causes = []
        dependency_failures = []

        for step, exc in failed_steps:
            if isinstance(exc, PrerequisiteFailed):
                dependency_failures.append((step, exc))
            else:
                root_causes.append((step, exc))

        # For each failed step, collect the associated task and its messages
        tasks_with_messages = {}

        for step, exc in root_causes:
            task = None
            if isinstance(step, Task):
                # The task itself failed
                task = step
            elif isinstance(step, Resource) and isinstance(exc, BuildError):
                # Find the task that should have created this resource
                for dep in step.depends_on:
                    if isinstance(dep, Task):
                        task = dep
                        break

            if task and task not in tasks_with_messages:
                # Get ALL warnings and errors from this task (not just errors)
                # Include messages with no origin (backend parsers often don't set it)
                task_messages = [m for m in self.__messages
                               if (m.origin == task or m.origin is None) and
                                  m.severity in (MessageSeverity.WARNING, MessageSeverity.ERROR, MessageSeverity.FATAL)]
                if task_messages:
                    tasks_with_messages[task] = task_messages

        # Also check ALL tasks for ERROR/FATAL messages, even if they didn't fail
        # A task can complete successfully but still have errors (e.g., syntax errors that don't stop the tool)
        for step in self.steps:
            if isinstance(step, Task) and step not in tasks_with_messages:
                # Check if this task has any ERROR or FATAL messages
                task_errors = [m for m in self.__messages
                             if m.origin == step and
                                m.severity in (MessageSeverity.ERROR, MessageSeverity.FATAL)]
                if task_errors:
                    # Get all warnings and errors from this task
                    task_messages = [m for m in self.__messages
                                   if m.origin == step and
                                      m.severity in (MessageSeverity.WARNING, MessageSeverity.ERROR, MessageSeverity.FATAL)]
                    tasks_with_messages[step] = task_messages

        # Print root cause failures
        if root_causes or tasks_with_messages:
            click.echo(click.style("Root Cause Failures:", fg="red", bold=True))
            click.echo()

            # First show Tasks that have messages
            for task, messages in tasks_with_messages.items():
                click.echo(click.style(f"  ✗ {task.name}", fg="red", bold=True))

                if task.description and task.description != task.name:
                    click.echo(f"    {task.description}")

                # Show outputs that failed
                failed_outputs = [step for step, exc in root_causes
                                if isinstance(step, Resource) and step in task.expected_by]
                if failed_outputs:
                    click.echo(f"    Failed outputs: {', '.join(str(o.name) for o in failed_outputs[:3])}" +
                             (f" (+{len(failed_outputs)-3} more)" if len(failed_outputs) > 3 else ""))

                # Show all warnings and errors from this task
                click.echo(click.style(f"    Messages:", fg="yellow"))
                for msg in messages[:10]:  # Limit to 10 messages
                    for line in str(msg).split('\n'):
                        click.echo(f"      {line}")
                if len(messages) > 10:
                    click.echo(f"      ... and {len(messages) - 10} more messages")
                click.echo()

            # Show other root cause failures that aren't covered above
            shown_resources = set()
            for task in tasks_with_messages:
                shown_resources.update(step for step, _ in root_causes
                                     if isinstance(step, Resource) and step in task.expected_by)

            # Track which steps have already been shown
            shown_steps = set(tasks_with_messages.keys()) | shown_resources

            for step, exc in root_causes:
                if step in shown_steps:
                    continue

                if isinstance(step, Task):
                    click.echo(click.style(f"  ✗ {step.name}", fg="red", bold=True))

                    if step.description and step.description != step.name:
                        click.echo(f"    {step.description}")

                    if isinstance(exc, MissingToolError):
                        click.echo(click.style(f"    Reason: {exc}", fg="red"))
                        click.echo(click.style(f"    Hint: Check tool configuration", fg="yellow"))
                    else:
                        exc_msg = str(exc)
                        if exc_msg:
                            click.echo(click.style(f"    Reason: {exc_msg}", fg="red"))

                    # Show warnings and errors from this task
                    task_messages = [m for m in self.__messages
                                   if m.origin == step and m.severity in (MessageSeverity.WARNING, MessageSeverity.ERROR, MessageSeverity.FATAL)]
                    if task_messages:
                        click.echo(click.style(f"    Messages:", fg="yellow"))
                        for msg in task_messages[:10]:
                            for line in str(msg).split('\n'):
                                click.echo(f"      {line}")
                        if len(task_messages) > 10:
                            click.echo(f"      ... and {len(task_messages) - 10} more messages")
                    click.echo()

        # Show log file location
        log_file = get_log_file()
        if log_file:
            click.echo(click.style(f"Full build log: {log_file}", fg="blue"))

    # ========================================================================
    # Pending Work Queue Methods (merged from BuildFileSet)
    # ========================================================================

    @property
    def pending_modification_serial(self) -> int:
        """Current modification serial number for pending work queue

        Increments each time the pending queue is modified. Used to detect convergence.
        """
        return self._pending_modification_serial

    def add_pending(
        self,
        resource: 'Resource',
        source_dependencies: Optional[set['Resource']] = None
    ) -> None:
        """Add a Resource to the pending work queue

        Args:
            resource: The Resource to add to pending queue
            source_dependencies: Optional set of Resources this depends on (partition dependencies)

        Note:
            - Increments modification serial
            - Updates dependency tracking
            - If resource already exists in queue, replaces it
        """
        path = resource.path.resolve()

        # Remove old resource if exists (to update dependencies properly)
        if path in self._pending:
            self.remove_pending(path)

        # Add new resource
        self._pending[path] = resource
        self._pending_modification_serial += 1

        # Update dependency tracking
        if path not in self._pending_dependents:
            self._pending_dependents[path] = set()

        # Store source dependencies if provided
        if source_dependencies:
            self._pending_source_deps[path] = source_dependencies

            # Register this resource as dependent on its source dependencies
            for dep in source_dependencies:
                dep_path = dep.path.resolve()
                if dep_path not in self._pending_dependents:
                    self._pending_dependents[dep_path] = set()
                self._pending_dependents[dep_path].add(resource)

    def remove_pending(self, path: Path) -> set['Resource']:
        """Remove a Resource from the pending work queue

        Args:
            path: Path of resource to remove

        Returns:
            Set of Resources that depended on the removed resource

        Note:
            - Increments modification serial
            - Updates dependency tracking
            - Returns dependents so caller can handle them (usually add as task deps)
        """
        path = path.resolve()

        if path not in self._pending:
            return set()

        resource = self._pending[path]
        del self._pending[path]
        self._pending_modification_serial += 1

        # Get dependents before cleanup
        dependents = self._pending_dependents.get(path, set()).copy()

        # Clean up dependency tracking
        # Remove this resource from its source dependencies' dependent lists
        source_deps = self._pending_source_deps.get(path, set())
        for dep in source_deps:
            dep_path = dep.path.resolve()
            if dep_path in self._pending_dependents:
                self._pending_dependents[dep_path].discard(resource)

        # Remove from dependents dict
        if path in self._pending_dependents:
            del self._pending_dependents[path]

        # Remove from source deps dict
        if path in self._pending_source_deps:
            del self._pending_source_deps[path]

        return dependents

    def filter_pending(self, **criteria) -> list['Resource']:
        """Query pending resources by criteria

        Args:
            **criteria: Key-value pairs to match against Resource attributes
                       Special handling:
                       - library=None matches resources with library=None
                       - file_type can be a string or list of strings
                       - typology can be a ResourceTypology enum value

        Returns:
            List of matching Resources in stable order

        Examples:
            context.filter_pending(file_type='vhdl', library='work')
            context.filter_pending(file_type=['vhdl', 'verilog'])
            context.filter_pending(typology=ResourceTypology.SOURCE)
        """
        results = []

        for resource in self._pending.values():
            match = True
            for key, value in criteria.items():
                attr_value = getattr(resource, key, None)

                # Handle list of acceptable values
                if isinstance(value, (list, tuple, set)):
                    if attr_value not in value:
                        match = False
                        break
                # Handle exact match
                elif attr_value != value:
                    match = False
                    break

            if match:
                results.append(resource)

        return results

    def get_pending(self, path: Path) -> Optional['Resource']:
        """Get pending Resource by path

        Args:
            path: Path to look up

        Returns:
            Resource if found in pending queue, None otherwise
        """
        return self._pending.get(path.resolve())

    def pending_count(self) -> int:
        """Get number of resources in pending queue"""
        return len(self._pending)

    def iter_pending(self):
        """Iterate over all pending Resources"""
        return iter(self._pending.values())

    def get_pending_by_library_ordered(self) -> list[tuple[Optional[str], list['Resource']]]:
        """Get pending resources grouped by library in dependency order

        Returns:
            List of (library_name, resources) tuples ordered by library dependencies.
            Libraries are in topological order (dependencies before dependents).
            Resources within each library are in stable order (sorted by path).
        """
        # Build library dependency graph
        lib_deps = self._pending_library_dependency_graph()

        # Topological sort of libraries
        ordered_libs = self._pending_libraries_in_dependency_order()

        # Group resources by library
        result = []
        for lib in ordered_libs:
            lib_resources = self.filter_pending(library=lib)
            if lib_resources:
                # Preserve insertion order from partition definition
                # (files are already in dependency order from the source YAML)
                result.append((lib, lib_resources))

        # Also include resources with no library
        no_lib_resources = self.filter_pending(library=None)
        if no_lib_resources:
            result.append((None, no_lib_resources))

        return result

    def _pending_library_dependency_graph(self) -> dict[str, set[str]]:
        """Build dependency graph between libraries in pending queue

        Returns:
            Dict mapping library_name -> set of libraries it depends on (direct only)

        Note:
            Only includes libraries that are actually in the pending queue.
        """
        graph: dict[str, set[str]] = {}

        for resource in self._pending.values():
            if resource.library is None:
                continue

            if resource.library not in graph:
                graph[resource.library] = set()

            # Add dependencies from source dependencies
            source_deps = self._pending_source_deps.get(resource.path.resolve(), set())
            for dep in source_deps:
                if dep.library and dep.library != resource.library:
                    graph[resource.library].add(dep.library)

        return graph

    def _pending_library_dependency_graph_transitive(self) -> dict[str, set[str]]:
        """Build transitive dependency graph between libraries in pending queue

        Returns:
            Dict mapping library_name -> set of ALL libraries it depends on (directly or transitively)
        """
        # Start with direct dependencies
        graph = self._pending_library_dependency_graph()

        # Compute transitive closure
        changed = True
        while changed:
            changed = False
            for lib in list(graph.keys()):
                # Get current dependencies
                current_deps = graph[lib].copy()

                # Add transitive dependencies
                for dep in current_deps:
                    if dep in graph:
                        for trans_dep in graph[dep]:
                            if trans_dep not in graph[lib] and trans_dep != lib:
                                graph[lib].add(trans_dep)
                                changed = True

        return graph

    def _pending_libraries_in_dependency_order(self) -> list[str]:
        """Get libraries in topological dependency order

        Returns:
            List of library names ordered so dependencies come before dependents

        Raises:
            ValueError: If circular dependency detected
        """
        graph = self._pending_library_dependency_graph()

        if not graph:
            return []

        # Kahn's algorithm for topological sort
        # graph[lib] = libraries that lib depends on
        # We want: dependencies before dependents
        # in_degree = number of dependencies each library has

        in_degree = {lib: len(graph[lib]) for lib in graph}

        # Find all nodes with no dependencies
        queue = [lib for lib in graph if in_degree[lib] == 0]
        result = []

        while queue:
            # Sort for stable ordering
            queue.sort()
            lib = queue.pop(0)
            result.append(lib)

            # For each library that depends on lib, decrease its dependency count
            for dependent_lib in graph:
                if lib in graph[dependent_lib]:
                    in_degree[dependent_lib] -= 1
                    if in_degree[dependent_lib] == 0:
                        queue.append(dependent_lib)

        # Check for cycles
        if len(result) != len(graph):
            remaining = set(graph.keys()) - set(result)
            raise ValueError(f"Circular dependency detected in libraries: {remaining}")

        return result

    def get_pending_dependents(self, path: Path) -> set['Resource']:
        """Get all pending resources that depend on the given path

        Args:
            path: Path to query

        Returns:
            Set of Resources that depend on this path
        """
        return self._pending_dependents.get(path.resolve(), set()).copy()

    def get_pending_unsatisfied_outputs(self) -> list['Resource']:
        """Get output goals in pending queue that don't have producers yet.

        An output is unsatisfied if:
        - typology=OUTPUT (it's a desired output goal)
        - The Resource has no task dependencies (no task produces it)

        Returns:
            List of Resources that are outputs without producers
        """
        from .task import ResourceTypology

        unsatisfied = []
        for resource in self._pending.values():
            if resource.typology == ResourceTypology.OUTPUT and not resource.depends_on:
                unsatisfied.append(resource)
        return unsatisfied

    def populate_pending(self, build_set):
        """Populate pending work queue from resolved build set

        Args:
            build_set: Resolved BuildSet from dependency resolver

        Returns:
            Number of resources added to pending queue
        """
        from .task import ResourceTypology

        # First pass: create all Resources and add to pending queue
        partition_to_resources: dict[tuple[str, str], list['Resource']] = {}

        for lib_name in build_set.libraries:
            for part_name in build_set.partitions.get(lib_name, []):
                files = build_set.files.get((lib_name, part_name), [])
                partition_key = (lib_name, part_name)
                partition_to_resources[partition_key] = []

                for source_file in files:
                    # Map language to file type
                    file_type = source_file.file_type
                    if source_file.variant:
                        file_type = f"{file_type}_{source_file.variant}"

                    # Create Resource with SOURCE typology
                    res = self.get_resource(
                        source_file.path,
                        file_type=file_type,
                        library=lib_name,
                        typology=ResourceTypology.SOURCE,
                        generated_by=None
                    )

                    partition_to_resources[partition_key].append(res)

        # Second pass: compute source dependencies based on partition dependencies
        # and add resources to pending queue
        for partition_key, resources in partition_to_resources.items():
            lib_name, part_name = partition_key
            partition_deps = build_set.partition_deps.get(partition_key, set())

            # Collect all dependency resources from dependent partitions
            source_deps = set()
            for dep_lib, dep_part in partition_deps:
                dep_partition_key = (dep_lib, dep_part)
                dep_resources = partition_to_resources.get(dep_partition_key, [])
                source_deps.update(dep_resources)

            # Add each resource to pending queue with its source dependencies
            for res in resources:
                self.add_pending(res, source_dependencies=source_deps)

        return len(self._pending)

    # ========================================================================
    # End of Pending Work Queue Methods
    # ========================================================================


    def build(self):
        return ContextBuildManager(self)

    def to_clean(self) -> set(Path):
        from ..build.task import Task, Resource
        ret = set()
        for step in self.steps:
            if isinstance(step, Task):
                for o in step.outputs:
                    if isinstance(o, Resource):
                        ret.add(o.path)
        return ret
    
class ContextBuildManager:
    def __init__(self, context):
        self.context = context

    async def __aenter__(self):
        await self.context._launch()

    async def __aexit__(self, exc_type, exc, tb):
        await self.context._cleanup()
