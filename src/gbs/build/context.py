"""AsyncIO-Native Task System for GBS"""

from __future__ import annotations
from pathlib import Path
from typing import Any
from ..logging import get_logger
from .message import *
from .task import VirtualResource, Resource
from dataclasses import dataclass
import asyncio

@dataclass
class BuildResource:
    """Represents a source or generated file in the build with metadata

    This is distinct from Resource which is an asyncio Future. BuildResource
    is a data class that holds file metadata used by backends.

    Attributes:
        resource: The underlying Resource (asyncio Future for the file)
        file_type: Type of file (e.g., 'vhdl', 'verilog', 'systemverilog', 'c', 'vhd_elab')
        library: Library name for HDL files (None for non-HDL)
        file_type_version: File type version (e.g., '2008' for VHDL, '2005' for Verilog)
        is_source: True if source file, False if generated
        is_output: True if this is a desired output (goal) that needs a producer
        depends_on: Set of BuildResources this file depends on (for dep tracking)
        generated_by: Backend name that generated this file (None for source files)
        metadata: Additional backend-specific metadata
    """
    resource: Resource
    file_type: str
    library: str | None = None
    file_type_version: str | None = None
    is_source: bool = True
    is_output: bool = False
    depends_on: set['BuildResource'] = None
    generated_by: str | None = None
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.depends_on is None:
            self.depends_on = set()
        if self.metadata is None:
            self.metadata = {}

    @property
    def path(self) -> Path:
        """Convenience property to access the file path"""
        return self.resource.path

    def __hash__(self):
        """Hash based on resource path for set membership"""
        return hash(self.resource.path)

    def __eq__(self, other):
        """Equality based on resource path"""
        if not isinstance(other, BuildResource):
            return False
        return self.resource.path == other.resource.path

    def __repr__(self):
        return f"BuildResource({self.path}, {self.file_type}, lib={self.library})"


class BuildFileSet:
    """Mutable collection of BuildResources with dependency tracking

    The BuildFileSet is iteratively transformed by backends. Each backend can:
    - Add new generated files
    - Remove processed files
    - Replace files with transformed versions
    - Query files by various criteria

    The fileset tracks:
    - Forward dependencies (what each file depends on)
    - Reverse dependencies (what depends on each file)
    - Modification serial (for detecting convergence)
    - Stable iteration order

    All modifications must go through the provided methods to maintain
    consistency of dependency tracking.
    """

    def __init__(self, context: BuildContext):
        """Initialize an empty fileset

        Args:
            context: Build context (for creating Resources)
        """
        self.context = context
        self._resources: dict[Path, BuildResource] = {}  # path -> BuildResource
        self._modification_serial = 0
        self._dependents: dict[Path, set[BuildResource]] = {}  # path -> set of BuildResources that depend on it

    @property
    def modification_serial(self) -> int:
        """Current modification serial number

        Increments each time the fileset is modified. Used to detect convergence.
        """
        return self._modification_serial

    def __len__(self) -> int:
        """Number of resources in fileset"""
        return len(self._resources)

    def __iter__(self):
        """Iterate over BuildResources in insertion order

        Note: Insertion order is significant for VHDL compilation where files must
        be analyzed in dependency order. The BuildFileSet is populated in partition
        dependency order, which must be preserved.
        """
        return iter(self._resources.values())

    def __contains__(self, path: Path) -> bool:
        """Check if path is in fileset"""
        return path.resolve() in self._resources

    def add(self, build_resource: BuildResource) -> None:
        """Add a BuildResource to the fileset

        Args:
            build_resource: The BuildResource to add

        Note:
            - Increments modification serial
            - Updates dependency tracking
            - If resource already exists, replaces it
        """
        path = build_resource.path.resolve()

        # Remove old resource if exists (to update dependencies properly)
        if path in self._resources:
            self.remove(path)

        # Add new resource
        self._resources[path] = build_resource
        self._modification_serial += 1

        # Update dependency tracking
        if path not in self._dependents:
            self._dependents[path] = set()

        # Register this resource as dependent on its dependencies
        for dep in build_resource.depends_on:
            dep_path = dep.path.resolve()
            if dep_path not in self._dependents:
                self._dependents[dep_path] = set()
            self._dependents[dep_path].add(build_resource)

    def remove(self, path: Path) -> set[BuildResource]:
        """Remove a BuildResource from the fileset

        Args:
            path: Path of resource to remove

        Returns:
            Set of BuildResources that depended on the removed resource

        Note:
            - Increments modification serial
            - Updates dependency tracking
            - Returns dependents so caller can handle them
        """
        path = path.resolve()

        if path not in self._resources:
            return set()

        resource = self._resources[path]
        del self._resources[path]
        self._modification_serial += 1

        # Get dependents before cleanup
        dependents = self._dependents.get(path, set()).copy()

        # Clean up dependency tracking
        # Remove this resource from its dependencies' dependent lists
        for dep in resource.depends_on:
            dep_path = dep.path.resolve()
            if dep_path in self._dependents:
                self._dependents[dep_path].discard(resource)

        # Remove from dependents dict
        if path in self._dependents:
            del self._dependents[path]

        return dependents

    def replace(
        self,
        old_path: Path,
        new_resource: BuildResource,
        transfer_dependencies: bool = True
    ) -> set[BuildResource]:
        """Replace a resource with a new one

        Args:
            old_path: Path of resource to replace
            new_resource: New BuildResource
            transfer_dependencies: If True, transfer dependents from old to new

        Returns:
            Set of BuildResources that were updated to depend on new_resource

        Note:
            - Increments modification serial (via remove and add)
            - If transfer_dependencies=True, updates all dependents to point to new resource
        """
        old_path = old_path.resolve()

        # Get the old resource before removing
        old_resource = self._resources.get(old_path)

        # Get dependents before removing
        dependents = self.remove(old_path)

        # Transfer dependencies if requested
        if transfer_dependencies and old_resource:
            for dependent in dependents:
                # Remove old dependency
                dependent.depends_on.discard(old_resource)
                # Add new dependency
                dependent.depends_on.add(new_resource)

        # Add new resource
        self.add(new_resource)

        return dependents if transfer_dependencies else set()

    def filter(self, **criteria) -> list[BuildResource]:
        """Query resources by criteria

        Args:
            **criteria: Key-value pairs to match against BuildResource attributes
                       Special handling:
                       - library=None matches resources with library=None
                       - file_type can be a string or list of strings
                       - is_source can be True/False

        Returns:
            List of matching BuildResources in stable order

        Examples:
            fileset.filter(file_type='vhdl', library='work')
            fileset.filter(is_source=True)
            fileset.filter(file_type=['vhdl', 'verilog'])
        """
        results = []

        for resource in self:
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

    def get(self, path: Path) -> BuildResource | None:
        """Get BuildResource by path

        Args:
            path: Path to look up

        Returns:
            BuildResource if found, None otherwise
        """
        return self._resources.get(path.resolve())

    def by_library_ordered(self) -> list[tuple[str, list[BuildResource]]]:
        """Get resources grouped by library in dependency order

        Returns:
            List of (library_name, resources) tuples ordered by library dependencies.
            Libraries are in topological order (dependencies before dependents).
            Resources within each library are in stable order (sorted by path).
        """
        # Build library dependency graph
        lib_deps = self.library_dependency_graph()

        # Topological sort of libraries
        ordered_libs = self.libraries_in_dependency_order()

        # Group resources by library
        result = []
        for lib in ordered_libs:
            lib_resources = self.filter(library=lib)
            if lib_resources:
                result.append((lib, lib_resources))

        # Also include resources with no library
        no_lib_resources = self.filter(library=None)
        if no_lib_resources:
            result.append((None, no_lib_resources))

        return result

    def library_dependency_graph(self) -> dict[str, set[str]]:
        """Build dependency graph between libraries

        Returns:
            Dict mapping library_name -> set of libraries it depends on (direct only)

        Note:
            Only includes libraries that are actually in the fileset.
            For transitive dependencies, use library_dependency_graph_transitive().
        """
        graph: dict[str, set[str]] = {}

        for resource in self:
            if resource.library is None:
                continue

            if resource.library not in graph:
                graph[resource.library] = set()

            # Add dependencies from this resource
            for dep in resource.depends_on:
                if dep.library and dep.library != resource.library:
                    graph[resource.library].add(dep.library)

        return graph

    def library_dependency_graph_transitive(self) -> dict[str, set[str]]:
        """Build transitive dependency graph between libraries

        Returns:
            Dict mapping library_name -> set of ALL libraries it depends on (directly or transitively)

        Note:
            This computes the transitive closure of library_dependency_graph().
            Useful for GHDL which needs all transitive dependencies in -P flags.
        """
        # Start with direct dependencies
        graph = self.library_dependency_graph()

        # Compute transitive closure using Floyd-Warshall-like algorithm
        # For each library, add dependencies of its dependencies
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

    def libraries_in_dependency_order(self) -> list[str]:
        """Get libraries in topological dependency order

        Returns:
            List of library names ordered so dependencies come before dependents

        Raises:
            ValueError: If circular dependency detected
        """
        graph = self.library_dependency_graph()

        # Kahn's algorithm for topological sort
        # graph[lib] contains the libraries that lib depends on
        # We need to reverse this: for each lib, count how many libs depend on it
        in_degree = {lib: 0 for lib in graph}

        # Count incoming edges: if lib2 depends on lib1, then lib1 has incoming edge from lib2
        for lib in graph:
            for dep in graph[lib]:
                if dep not in in_degree:
                    in_degree[dep] = 0
                # lib depends on dep, so dep should come before lib
                # We increment lib's in-degree (lib has a dependency)

        # Actually, let me recalculate: the graph shows dependencies
        # graph[A] = {B, C} means A depends on B and C
        # So B and C must come before A
        # In-degree of A = number of libraries that depend on A

        # Build reverse graph: who depends on me?
        reverse_graph = {lib: set() for lib in graph}
        for lib in graph:
            for dep in graph[lib]:
                if dep not in reverse_graph:
                    reverse_graph[dep] = set()
                reverse_graph[dep].add(lib)

        # Now in-degree is the count of dependents
        in_degree = {lib: len(reverse_graph.get(lib, set())) for lib in graph}

        # Find all nodes with no incoming edges (no one depends on them, they can go last)
        # Wait, that's backwards. Let me think again...

        # graph[lib] = dependencies of lib (what lib needs)
        # We want: dependencies before dependents
        # So if lib depends on dep, dep should come first
        # In Kahn's: in_degree = number of dependencies
        # Start with nodes that have 0 dependencies

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

    def get_dependents(self, path: Path) -> set[BuildResource]:
        """Get all resources that depend on the given path

        Args:
            path: Path to query

        Returns:
            Set of BuildResources that depend on this path
        """
        return self._dependents.get(path.resolve(), set()).copy()

    def get_unsatisfied_outputs(self) -> list[BuildResource]:
        """Get output goals that don't have producers yet.

        An output is unsatisfied if:
        - is_output=True (it's a desired output goal)
        - The underlying Resource has no dependencies (no task produces it)

        Returns:
            List of BuildResources that are outputs without producers
        """
        unsatisfied = []
        for br in self:
            if br.is_output and not br.resource.depends_on:
                unsatisfied.append(br)
        return unsatisfied

class BuildContext:
    """Shared build context passed to all tasks and resources

    Contains:
    - Semaphore for parallel execution control
    - Project configuration
    - Build settings
    - Resource registry
    - All steps
    """

    def __init__(self, max_parallel: int = 4, project_config: Optional[dict[str, Any]] = None, project: Optional[Any] = None, gbs_config: Optional[Any] = None):
        """Initialize build context

        Args:
            max_parallel: Maximum number of tasks to run in parallel
            project_config: Optional project configuration (deprecated, use project instead)
            project: Optional Project instance
            gbs_config: Optional GBSConfig instance for tool lookup
        """
        self._max_parallel = max_parallel
        self._semaphore: Optional[asyncio.Semaphore] = None
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
        for p in self.running:
            if not p.done():
                p.cancel()
            else:
                p.exception()

        for m in self.messages_get(min_severity = MessageSeverity.WARNING):
            m.pprint()

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

    def populate_fileset(self, build_set, fileset):
        """Populate fileset from resolved build set

        Args:
            build_set: Resolved BuildSet from dependency resolver
            fileset: BuildFileSet to populate

        Returns:
            The populated fileset
        """
        # First pass: create all BuildResources
        partition_to_resources: dict[tuple[str, str], list] = {}

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

                    res = self.get_resource(
                        source_file.path,
                        metadata = {
                            "file_type": source_file.file_type,
                            "library": lib_name,
                            },
                        generated = False,
                    )

                    # Create BuildResource
                    br = BuildResource(
                        resource=res,
                        file_type=file_type,
                        library=lib_name,
                    )

                    partition_to_resources[partition_key].append(br)
                    fileset.add(br)

        # Second pass: populate BuildResource.depends_on based on partition dependencies
        for partition_key, resources in partition_to_resources.items():
            lib_name, part_name = partition_key
            partition_deps = build_set.partition_deps.get(partition_key, set())

            # For each resource in this partition, add dependencies from dependent partitions
            for br in resources:
                for dep_lib, dep_part in partition_deps:
                    dep_partition_key = (dep_lib, dep_part)
                    dep_resources = partition_to_resources.get(dep_partition_key, [])
                    br.depends_on.update(dep_resources)

        return fileset

    async def execute_build(self, fileset, show_progress: bool = False):
        """Execute the build for given fileset

        Args:
            fileset: BuildFileSet to build
            show_progress: Whether to show progress bars

        Returns:
            Number of files processed
        """
        import asyncio

        async with self.build():
            # Gather all resources
            all_resources = [br.resource for br in fileset]
            if all_resources:
                if show_progress:
                    # Use progress monitoring if available
                    try:
                        from ..ui.progress import run_with_progress
                        await run_with_progress(self, all_resources)
                    except ImportError:
                        # Fall back if progress module not available
                        await asyncio.gather(*all_resources)
                else:
                    # Run without progress bars
                    await asyncio.gather(*all_resources)

        return len(fileset)

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
