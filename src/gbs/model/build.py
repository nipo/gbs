"""AsyncIO-Native Task System for GBS

This is a redesigned task system where both Tasks and Resources are asyncio awaitables.
The dependency graph is implicit and resolved at runtime by asyncio.

Key concepts:
- Resource: Represents a file (input or output). Awaiting it waits for the file to be ready.
- VirtualResource: Represents in-memory data. Awaiting it waits for the data to be produced.
- Task: Represents work to be done. Awaits inputs, runs, resolves outputs.
- BuildContext: Shared context with semaphore and build configuration.
- Resource Registry: Ensures singleton Resources for each file path.

The build process:
1. Create Resources for all desired outputs
2. await asyncio.gather(*outputs)
3. AsyncIO automatically schedules Tasks based on dependencies
4. Failures cascade naturally through awaits
"""

from __future__ import annotations
from pathlib import Path
from typing import Any, Callable, Awaitable, Optional
import asyncio
from dataclasses import dataclass
from enum import Enum
import time

from ..logging import get_logger

try:
    import click
except ImportError:
    click = None


class BuildError(Exception):
    """Error during build execution"""
    pass

class PrerequisiteFailed(Exception):
    """Prerequisite failed while we were waiting on it"""
    pass


class MessageSeverity(Enum):
    """Severity levels for tool messages"""
    DEBUG = "debug"
    NOTICE = "notice"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"

    def __str__(self) -> str:
        return self.value


@dataclass
class ToolMessage:
    """Standardized message from EDA tools and backends

    Provides a homogeneous representation of messages from various tools,
    including errors, warnings, and informational messages.
    """
    severity: MessageSeverity
    message: str
    identifier: Optional[str] = None
    extended_message: Optional[str] = None
    file_path: Optional[Path] = None
    line: Optional[int] = None
    column: Optional[int] = None
    origin: "BuildStep" = None

    def __str__(self) -> str:
        """Format message for display"""
        parts = [f"[{self.severity.value.upper()}]"]

        if self.identifier:
            parts.append(f"({self.identifier})")

        if self.file_path:
            location = str(self.file_path)
            if self.line is not None:
                location += f":{self.line}"
                if self.column is not None:
                    location += f":{self.column}"
            parts.append(f"{location}:")

        parts.append(self.message)

        result = " ".join(parts)

        if self.extended_message:
            result += "\n" + self.extended_message

        return result

    def pprint(self):
        print(str(self))

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
        self.logger.debug("created")
        self.__messages: list[ToolMessage] = []

        # Progress tracking
        self._progress_condition: Optional[asyncio.Condition] = None
        self._progress_version = 0

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

    def get_resource(self, path: Path) -> 'Resource':
        """Get or create a Resource for a file path (singleton)

        Args:
            path: File path

        Returns:
            Resource instance (same instance for same path)
        """
        path = path.resolve()  # Normalize path
        if path not in self._resources:
            self._resources[path] = Resource(self, path)
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

    def get_topcell(self) -> Optional[str]:
        """Get the project topcell name

        Returns:
            Topcell name or None if no project is set
        """
        if self.project is None:
            return None
        return self.project.topcell

    def get_topcell_library(self) -> Optional[str]:
        """Get the library name containing the topcell

        Returns:
            Root library name or None if no project is set
        """
        if self.project is None:
            return None
        return self.project.root_library_name

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
            
    def populate_fileset(self, build_set, fileset):
        """Populate fileset from resolved build set

        Args:
            build_set: Resolved BuildSet from dependency resolver
            fileset: BuildFileSet to populate

        Returns:
            The populated fileset
        """
        from ..tasks import BuildResource

        # First pass: create all BuildResources
        partition_to_resources: dict[tuple[str, str], list] = {}

        for lib_name in build_set.libraries:
            for part_name in build_set.partitions.get(lib_name, []):
                files = build_set.files.get((lib_name, part_name), [])
                partition_key = (lib_name, part_name)
                partition_to_resources[partition_key] = []

                for source_file in files:
                    # Map language to file type
                    file_type = source_file.language
                    if source_file.variant:
                        file_type = f"{file_type}_{source_file.variant}"

                    # Create BuildResource
                    br = BuildResource(
                        resource=self.get_resource(source_file.path),
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

    def load_backends(self):
        """Load backends from project configuration

        Returns:
            BackendRegistry with loaded backends

        Raises:
            ValueError: If no backends are configured
        """
        from ..backend_loader import load_backends_from_project

        if not self.project or not hasattr(self.project, 'raw_config'):
            raise ValueError("BuildContext has no project configuration")

        registry = load_backends_from_project(self.project.raw_config)

        if len(registry) == 0:
            raise ValueError("No backends configured in project")

        return registry

    async def run_backends(self, fileset, max_iterations: int = 100):
        """Load and run backend iteration

        Args:
            fileset: BuildFileSet to process
            max_iterations: Maximum iterations for backend convergence

        Returns:
            Number of iterations until convergence
        """
        from ..backend import run_backend_iteration

        # Load backends
        registry = self.load_backends()

        # Run iteration
        iterations = await run_backend_iteration(
            self,
            fileset,
            registry,
            max_iterations=max_iterations
        )

        return iterations, registry

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
                        from ..progress import run_with_progress
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
                
class ContextBuildManager:
    def __init__(self, context):
        self.context = context

    async def __aenter__(self):
        await self.context._launch()

    async def __aexit__(self, exc_type, exc, tb):
        await self.context._cleanup()
                
class BuildStep(asyncio.Future):
    """
    A step in a build, either a task, an artifact, a dependency, etc.
    It is a node in the build graph.
    """
    def __init__(self, context: BuildContext, name: str):
        """Initialize resource

        Args:
            context: Build context
            log_name: Name for logger
        """
        super().__init__()
        self.context = context
        self.depends_on = set()
        self.expected_by = set()
        self.name = name
        self._log_name = f"{self.__class__.__name__}({name})"
        self.logger = get_logger(self._log_name)
        self.logger.debug("created")
        self.task = None

        # Progress tracking
        self.progress: float = 0.0  # 0.0 to 1.0
        self.progress_message: Optional[str] = None
        self.progress_started: Optional[float] = None
        self.progress_updated: Optional[float] = None
        self.completed: bool = False

        self.context.step_register(self)

    def dependency_add(self, dep: BuildStep) -> None:
        """Add a relation between us and another step we depend
        on. Also adds the reverse relation.
        """
        self.depends_on.add(dep)
        dep.expected_by.add(self)
        
    def __repr__(self) -> str:
        return self._log_name

    def get_percentage(self) -> int:
        """Get progress as percentage (0-100)"""
        return int(self.progress * 100)

    async def update_progress(self, progress: float, message: Optional[str] = None):
        """Update this step's progress

        Updates the step's own state and notifies BuildContext
        to wake any UI watchers.

        Args:
            progress: Progress value 0.0 to 1.0
            message: Optional status message
        """
        progress = max(0.0, min(1.0, progress))

        # Update own state
        if self.progress_started is None:
            self.progress_started = time.time()

        self.progress = progress
        self.progress_message = message
        self.progress_updated = time.time()

        if progress >= 1.0:
            self.completed = True

        self.logger.info(f"{self.name} progress: {progress * 100}%: {message or ''}")

        # Notify watchers
        await self.context.notify_progress_update()

    def add_message(
        self,
        severity: MessageSeverity,
        message: str,
        identifier: Optional[str] = None,
        extended_message: Optional[str] = None,
        file_path: Optional[Path] = None,
        line: Optional[int] = None,
        column: Optional[int] = None,
    ) -> ToolMessage:
        """Add a tool message from individual fields

        Creates a ToolMessage and adds it to this step's message collection.
        Also logs the message using the step's logger.

        Args:
            severity: Message severity level
            message: The message text
            identifier: Optional tool-specific error identifier
            extended_message: Optional multi-line extended details
            file_path: Optional source file path
            line: Optional line number
            column: Optional column number

        Returns:
            The created ToolMessage
        """
        msg = ToolMessage(
            severity=severity,
            message=message,
            identifier=identifier,
            extended_message=extended_message,
            file_path=file_path,
            line=line,
            column=column,
        )
        self.add_message_obj(msg)
        return msg

    def add_message_obj(self, msg: ToolMessage) -> None:
        """Add a pre-constructed ToolMessage

        Adds an existing ToolMessage to this step's message collection.
        Also logs the message using the step's logger.

        Args:
            msg: The ToolMessage to add
        """
        msg.source = self
        self.context.message_add(msg)
        self._log_message(msg)

    def _log_message(self, msg: ToolMessage) -> None:
        """Log a ToolMessage using the step's logger

        Args:
            msg: The message to log
        """
        # Map severity to logger method
        log_method = {
            MessageSeverity.DEBUG: self.logger.debug,
            MessageSeverity.NOTICE: self.logger.info,
            MessageSeverity.INFO: self.logger.info,
            MessageSeverity.WARNING: self.logger.warning,
            MessageSeverity.ERROR: self.logger.error,
            MessageSeverity.FATAL: self.logger.critical,
        }.get(msg.severity, self.logger.info)

        log_method(str(msg))

    def launch(self) -> asyncio.Task:
        if self.task:
            return self.task
        self.logger.debug("launch")
        self.task = asyncio.create_task(self.__worker())
        return self.task

    async def __worker(self):
        # Initialize progress
        await self.update_progress(0.0, "Starting")

        # Wait for all dependencies if any
        deps_failed = None
        pending = self.depends_on
        while pending:
            self.logger.debug("Waiting pending %s", pending)
            done, pending = await asyncio.wait(pending, return_when = asyncio.FIRST_COMPLETED)
            for fut in done:
                self.logger.debug("- done: %s", fut)
                try:
                    r = fut.result()
                except Exception as e:
                    deps_failed = PrerequisiteFailed(fut)

        if deps_failed is not None:
            self.logger.debug("Some deps failed: %s", deps_failed)
            await self.update_progress(1.0, "Failed (dependency)")
            self.__mark_done(deps_failed)
            return

        self.logger.debug("Starting work")
        try:
            await self._work()
        except Exception as e:
            self.logger.debug("Work excepted: %s", e)
            import traceback
            for line in traceback.format_exc().split("\n"):
                self.logger.error(line)
            await self.update_progress(1.0, "Failed")
            self.__mark_done(e)
            return

        self.logger.debug("Work done")
        # Auto-complete if not already at 100%
        if self.progress < 1.0:
            await self.update_progress(1.0, "Complete")
        self.__mark_done()

    async def _work(self):
        """
        Work to be done, should call work in the end, but may
        implement generic filter here
        """
        return await self.work()
        
    def __mark_done(self, exc: Exception = None):
        """Mark step as done"""
        if exc:
            self.set_exception(exc)
        else:
            self.set_result(None)
        
    async def work(self):
        """
        Actual work load item.
        """
        ...
    
class VirtualResource(BuildStep):
    """An in-memory resource

    Similar to Resource but for data that doesn't correspond to a file.
    """

    def __init__(self, context: BuildContext, name: str):
        """Initialize virtual resource

        Args:
            context: Build context
            name: Unique name for this resource
        """
        super().__init__(context, name)

    async def work(self):
        """
        Actual work load item.
        """
        pass

class Resource(BuildStep):
    """A file resource

    Its only work is to check the file exists
    """

    def __init__(self, context: BuildContext, path: Path):
        """Initialize resource

        Args:
            context: Build context
            path: Path to file
        """
        super().__init__(context, path.name)
        self.path = path
        self.metadata = {}  # Backend-specific metadata (file_type, library, etc.)

    def exists(self) -> bool:
        return self.path.exists()

    def mtime_get(self) -> int | None:
        try:
            return self.path.stat().st_mtime
        except:
            return None
        
    async def work(self):
        """
        Actual work load item - check file exists.
        On success, set result to the file path.
        """
        if not self.exists():
            raise BuildError(f"File {self.path} missing")

class Task(BuildStep):
    """A build task that awaits inputs, runs, and resolves outputs
    """

    def __init__(
        self,
        context: BuildContext,
        name: str,
        inputs: list[BuildStep],
        outputs: list[BuildStep],
        description: str = ""
    ):
        """Initialize task

        Args:
            context: Build context
            name: Unique task name
            inputs: Input resources (files or virtual)
            outputs: Output resources (files or virtual)
            description: Human-readable description
        """
        super().__init__(context, name)
        self.description = description or name
        self.inputs = inputs
        self.outputs = outputs

        for o in outputs:
            o.dependency_add(self)
        for i in inputs:
            self.dependency_add(i)

    def is_rebuild_needed(self) -> bool:
        """Check if rebuild is needed based on timestamps

        Returns:
            True if rebuild needed, False if outputs are up-to-date
        """
        # If any input is virtual, always rebuild
        # (Virtual resources have no timestamp)
        if any(isinstance(inp, VirtualResource) for inp in self.depends_on):
            self.logger.debug("Rebuild needed: has virtual inputs")
            return True

        # If any output is virtual, always rebuild
        if any(isinstance(out, VirtualResource) for out in self.expected_by):
            self.logger.debug("Rebuild needed: has virtual outputs")
            return True

        # Get file inputs and outputs
        file_inputs = [inp for inp in self.depends_on if isinstance(inp, Resource)]
        file_outputs = [out for out in self.expected_by if isinstance(out, Resource)]

        # If no file outputs, always rebuild (shouldn't happen)
        if not file_outputs:
            self.logger.debug("Rebuild needed: no file outputs")
            return True

        # Check if any output doesn't exist
        for output in file_outputs:
            if not output.exists():
                self.logger.debug(f"Rebuild needed: output {output.path} doesn't exist")
                return True

        # If no inputs, outputs exist, so we're up-to-date
        if not file_inputs:
            self.logger.debug("Up-to-date: no inputs, outputs exist")
            return False

        # Get oldest output timestamp
        oldest_output = min(out.mtime_get() for out in file_outputs)
        newest_input = max(i.mtime_get() for i in file_inputs if i.exists())

        if newest_input > oldest_output:
            self.logger.debug(f"Rebuild needed, some input is newer than outputs")
            return True

        # All outputs exist and are newer than all inputs
        self.logger.debug("Up-to-date: all outputs newer than all inputs")
        return False

    async def _work(self) -> None:
        if not self.is_rebuild_needed():
            self.logger.info(f"Task {self.name}: up-to-date, skipping")
            return
        else:
            return await self.work()

    async def work(self) -> None:
        """Execute the task work

        Must be overridden by subclasses
        """
        ...

# Type for task executor function
TaskExecutor = Callable[['BuildContext', list[Any]], Awaitable[list[Any]]]

class ExecutorTask(Task):
    """A build task that awaits inputs, runs executor, and resolves outputs
    """

    def __init__(
        self,
        context: BuildContext,
        name: str,
        inputs: list[BuildStep],
        outputs: list[BuildStep],
        executor: TaskExecutor,
        description: str = ""
    ):
        """Initialize task

        Args:
            context: Build context
            name: Unique task name
            inputs: Input resources (files or virtual)
            outputs: Output resources (files or virtual)
            executor: Optional executor function
            description: Human-readable description
        """
        super().__init__(context, name, inputs = inputs, outputs =
                         outputs, description = description)
        self.executor = executor

    async def work(self) -> None:
        self.logger.info(f"Task {self.name}: {self.description} - executing")

        # Run executor with semaphore
        async with self.context.semaphore:
            output_values = await self.executor(self.context, self.inputs)

        # Validate outputs
        if len(output_values) != len(self.outputs):
            raise RuntimeError(
                f"Task {self.name} produced {len(output_values)} outputs, "
                f"expected {len(self.outputs)}"
            )

        # Check file outputs were created
        for output, value in zip(self.outputs, output_values):
            if isinstance(output, Resource):
                if not output.path.exists():
                    raise RuntimeError(
                        f"Task {self.name} did not create output file {output.path}"
                    )


@dataclass
class BuildResource:
    """Represents a source or generated file in the build with metadata

    This is distinct from Resource which is an asyncio Future. BuildResource
    is a data class that holds file metadata used by backends.

    Attributes:
        resource: The underlying Resource (asyncio Future for the file)
        file_type: Type of file (e.g., 'vhdl', 'verilog', 'systemverilog', 'c', 'vhd_elab')
        library: Library name for HDL files (None for non-HDL)
        language_version: Language version (e.g., '2008' for VHDL, '2005' for Verilog)
        is_source: True if source file, False if generated
        depends_on: Set of BuildResources this file depends on (for dep tracking)
        generated_by: Backend name that generated this file (None for source files)
        metadata: Additional backend-specific metadata
    """
    resource: Resource
    file_type: str
    library: str | None = None
    language_version: str | None = None
    is_source: bool = True
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
