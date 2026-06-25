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
from typing import Any, Callable, Awaitable, Optional, AsyncIterator
import asyncio
from dataclasses import dataclass
from enum import Enum
import time
from ..ui.messages import MessageSeverity, ToolMessage

from ..ui.reporter import UIReporter

__all__ = ["BuildError", "MissingToolError", "ToolFailure", "PrerequisiteFailed", "BuildStep",
           "VirtualResource", "Resource", "Task", "ExecutorTask", "ResourceTypology"]


class ResourceTypology(Enum):
    """Typology (classification) of a resource in the build graph"""
    SOURCE = "source"           # User-provided source file
    INTERMEDIATE = "intermediate"  # Generated during build (default)
    OUTPUT = "output"           # Final deliverable
    DEFINITION = "definition"   # Build-influencing metadata file (not an error if unused)

class BuildError(Exception):
    """Error during build execution"""
    pass

class MissingToolError(BuildError):
    """Required tool not found or not executable

    This exception indicates a tool (like ghdl, yosys, etc.) is missing
    or not properly configured.
    """
    pass

class ToolFailure(BuildError):
    """An external tool invocation returned a non-zero exit code.

    Carries the context needed to diagnose the failure without dragging
    the gbs internal traceback into the user's view: which command was
    run, where, with what exit code, and the tail of the tool's own
    output.
    """

    def __init__(
        self,
        message: str,
        tool: str,
        argv: list[str],
        returncode: int | None,
        cwd: Path | None = None,
        env_extra: dict[str, str] | None = None,
        log_tail: list[str] | None = None,
        log_path: Path | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.tool = tool
        # argv entries can be plain strings or pathlib.Path objects in
        # backends that splice resolved paths in directly (gbs and the
        # subprocess API both accept that). Normalize to strings here so
        # downstream renderers / loggers / shlex consumers don't have to
        # know.
        self.argv = [str(a) for a in argv]
        self.returncode = returncode
        self.cwd = cwd
        self.env_extra = dict(env_extra) if env_extra else None
        self.log_tail = list(log_tail) if log_tail else []
        self.log_path = log_path

    def __str__(self) -> str:
        return self.message

class PrerequisiteFailed(Exception):
    """Prerequisite failed while we were waiting on it"""
    pass

class BuildStep(UIReporter, asyncio.Future):
    """
    A step in a build, either a task, an artifact, a dependency, etc.
    It is a node in the build graph.

    Inherits from UIReporter to provide logging and progress reporting.
    """

    # Whether this type of BuildStep should emit UI progress bars
    _EMIT_UI_PROGRESS = False

    def __init__(self, context: BuildContext, name: str):
        """Initialize resource

        Args:
            context: Build context
            log_name: Name for logger
        """
        self.context = context
        self.depends_on = set()
        self.expected_by = set()
        self.name = name
        self._log_name = f"{self.__class__.__name__}({name})"

        # Initialize both parent classes
        # UIReporter first (sets up reporter attributes)
        UIReporter.__init__(self, reporter_name=self._log_name, parent_reporter=None)
        # Then asyncio.Future
        asyncio.Future.__init__(self)

        self.task = None

        # Progress tracking (BuildStep-specific, used for dependency tracking)
        self.progress: float = 0.0  # 0.0 to 1.0
        self.progress_message: Optional[str] = None
        self.progress_started: Optional[float] = None
        self.progress_updated: Optional[float] = None
        self.completed: bool = False

        # UI progress tracking (for BuildStep.update_progress method)
        self._ui_progress_task_id: Optional[str] = None
        self._ui_progress_failed: bool = False

        self.context.step_register(self)

    def dependency_add(self, dep: BuildStep) -> None:
        """Add a relation between us and another step we depend
        on. Also adds the reverse relation.
        """
        self.depends_on.add(dep)
        dep.expected_by.add(self)

    def __repr__(self) -> str:
        return self._log_name

    @property
    def pretty_name(self) -> str:
        return self.name
    
    def get_percentage(self) -> int:
        """Get progress as percentage (0-100)"""
        return int(self.progress * 100)

    async def update_progress(self, progress: float, message: Optional[str] = None, failed: bool = False):
        """Update this step's progress

        Updates the step's own state and notifies BuildContext
        to wake any UI watchers.

        Args:
            progress: Progress value 0.0 to 1.0
            message: Optional status message
            failed: Whether this progress update indicates failure
        """
        progress = max(0.0, min(1.0, progress or 0.))

        # Update own state
        if self.progress_started is None:
            self.progress_started = time.time()

        self.progress = progress
        self.progress_message = message
        self.progress_updated = time.time()

        if failed:
            self._ui_progress_failed = True

        if progress >= 1.0:
            self.completed = True

        self.info(f"{self.name} progress: {progress * 100}%: {message or ''}")

        # Notify watchers
        await self.context.notify_progress_update()

        # Emit UI progress updates only if this class type allows it
        if not self._EMIT_UI_PROGRESS:
            return

        from ..ui.hub import get_global_hub
        from ..ui.messages import ProgressStart, ProgressUpdate, ProgressEnd
        import uuid

        hub = get_global_hub()

        # Start progress on first update (if not already started)
        if self._ui_progress_task_id is None:
            self._ui_progress_task_id = uuid.uuid4().hex
            hub.emit(ProgressStart(
                task_id=self._ui_progress_task_id,
                description=self.pretty_name,
                total=100,  # Percentage-based progress
                transient=True  # Task progress bars are transient
            ))

        # Update progress
        hub.emit(ProgressUpdate(
            task_id=self._ui_progress_task_id,
            completed=int(progress * 100),
            message=message
        ))

        # End progress when complete
        if progress >= 1.0:
            hub.emit(ProgressEnd(
                task_id=self._ui_progress_task_id,
                success=not self._ui_progress_failed,
                message=message if self._ui_progress_failed else None
            ))

    # Note: Logging convenience methods (debug, info, warning, error, critical, emit_tool_message)
    # are now inherited from UIReporter mixin

    async def add_message(
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
        await self.add_message_obj(msg)
        return msg

    async def add_message_obj(self, msg: ToolMessage) -> None:
        """Add a pre-constructed ToolMessage

        Adds an existing ToolMessage to this step's message collection.
        Also logs the message using the step's logger.

        Applies per-backend message severity mangling if configured via
        tool configuration's message.level_force section.

        Args:
            msg: The ToolMessage to add
        """
        # Apply message severity mangling if configured
        assert self.dispatcher

        tool_config = self.dispatcher.tool_config
        if tool_config and msg.identifier:
            # Check for message level overrides in tool config
            level_force = tool_config.get('message', {}).get('level_force', {})
            if msg.identifier in level_force:
                new_severity_str = level_force[msg.identifier].upper()
                try:
                    # Convert string to MessageSeverity enum
                    new_severity = MessageSeverity[new_severity_str]
                    old_severity = msg.severity
                    msg.severity = new_severity
                    self.debug(
                        f"Message {msg.identifier}: severity {old_severity.value} → {new_severity.value}"
                    )
                except KeyError:
                    self.warning(
                        f"Invalid severity level '{new_severity_str}' for message {msg.identifier}, "
                        f"using original severity {msg.severity.value}"
                    )

        msg.origin = self
        self.context.message_add(msg)

        # Emit via UIReporter (only to hub, no double logging)
        self.emit_tool_message(
            severity=msg.severity,
            message=msg.message,
            file_path=msg.file_path,
            line=msg.line,
            column=msg.column,
            identifier=msg.identifier,
            extended_message=msg.extended_message
        )


    def launch(self) -> asyncio.Task:
        if self.task:
            return self.task
        self.task = asyncio.create_task(self.__worker())
        return self.task

    async def __worker(self):
        # Initialize progress
        await self.update_progress(0.0, f"{self.pretty_name} waiting")

        # Wait for all dependencies if any
        deps_failed = None
        pending = self.depends_on
        failing = []
        while pending:
            self.debug(f"{self.name} Blocked by {pending}")
            done, pending = await asyncio.wait(pending, return_when = asyncio.FIRST_COMPLETED)
            for fut in done:
                try:
                    r = fut.result()
                except Exception as e:
                    deps_failed = PrerequisiteFailed(fut)
                    failing.append(fut)

        if deps_failed is not None:
            self.debug(f"{self.name} deps failed: {deps_failed}")
            await self.update_progress(.001, f"Prereq failed: {failing}", failed=True)
            self.__mark_done(deps_failed)
            return

        # Notify context that this step is starting
        self.context._on_step_start(self)

        await self.update_progress(0.001, f"{self.pretty_name} starting")
        try:
            await self._work()
        except Exception as e:
            import traceback as _tb
            self.debug(f"{self.name} excepted with traceback:\n{_tb.format_exc()}")
            if isinstance(e, BuildError):
                # Expected build failure (tool error, missing tool,
                # constraint violation, ...). The structured renderer in
                # the failure summary shows the diagnostic; suppressing
                # the traceback here keeps the console focused on the
                # tool's own output.
                self.error(f"{self.name}: {e}")
            else:
                # Unexpected internal exception. Keep the traceback
                # inline so it is not missed.
                self.error(f"{self.name} excepted: {e}", exc_info=True)
            await self.update_progress(1.0, f"{self.pretty_name} failed", failed=True)
            self.__mark_done(e)
            return

        # Auto-complete if not already at 100%
        if self.progress < 1.0:
            await self.update_progress(1.0, f"{self.pretty_name} complete")
        self.__mark_done()

    async def _work(self):
        """
        Work to be done, should call work in the end, but may
        implement generic filter here
        """
        return await self.work()

    def __mark_done(self, exc: Exception = None):
        """Mark step as done"""
        # Update build context progress
        self.context._on_step_complete(self)

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

    Represents a source or generated file in the build with metadata.
    This is also an asyncio Future - awaiting it waits for the file to be ready.

    Attributes:
        path: File path
        file_type: Type of file (e.g., 'vhdl', 'verilog', 'systemverilog', 'c')
        library: Library name for HDL files (None for non-HDL)
        file_type_version: File type version (e.g., '2008' for VHDL, '2005' for Verilog)
        typology: Resource typology (SOURCE, INTERMEDIATE, OUTPUT)
        generated_by: Backend name that generated this file (None for source files)
        metadata: Additional backend-specific metadata
    """

    def __init__(
        self,
        context: BuildContext,
        path: Path,
        file_type: str | None = None,
        library: str | None = None,
        file_type_version: str | None = None,
        typology: ResourceTypology = ResourceTypology.INTERMEDIATE,
        generated_by: str | None = None
    ):
        """Initialize resource

        Args:
            context: Build context
            path: Path to file
            file_type: Type of file (e.g., 'vhdl', 'verilog')
            library: Library name for HDL files
            file_type_version: File type version
            typology: Resource typology (SOURCE, INTERMEDIATE, OUTPUT), defaults to INTERMEDIATE
            generated_by: Backend name that generated this file
        """
        # Set path BEFORE calling super().__init__() because __hash__ needs it
        self.path = path
        self.file_type = file_type
        self.library = library
        self.file_type_version = file_type_version
        self.typology = typology
        self.generated_by = generated_by

        # Custom metadata dict for additional attributes not in the standard fields
        self.metadata = {}

        # Now call parent __init__ which will call step_register() -> __hash__()
        super().__init__(context, path.name)

    def exists(self) -> bool:
        return self.path.exists()

    def mtime_get(self) -> int | None:
        try:
            return self.path.stat().st_mtime
        except:
            return None

    def __hash__(self):
        """Hash based on path for set membership"""
        return hash(self.path)

    def __eq__(self, other):
        """Equality based on path"""
        if not isinstance(other, Resource):
            return False
        return self.path == other.path


    def __repr__(self):
        return f"Resource({self.path}, {self.file_type}, lib={self.library})"

    async def work(self):
        """
        Actual work load item - check file exists.
        On success, set result to the file path.
        """
        if not self.exists():
            raise BuildError(f"File {self.path} missing")

class Stamp(Resource):
    """A build stamp
    """

    def __init__(
        self,
        context: BuildContext,
        path: Path,
    ):
        super().__init__(context, path, file_type = "build-stamp")

    def touch(self):
        self.path.parent.mkdir(exist_ok = True, parents = True)
        self.path.write_text("")
        
class Task(BuildStep):
    """A build task that awaits inputs, runs, and resolves outputs

    Tasks can optionally hold a reference to their creating dispatcher to access
    tool configuration and other dispatcher-level state without parameter passing.
    """

    # Tasks should emit UI progress bars when they report progress
    _EMIT_UI_PROGRESS = True

    def __init__(
        self,
        dispatcher: 'Dispatcher',
        name: str,
        inputs: list[BuildStep] = None,
        outputs: list[BuildStep] = None,
        description: str = ""
    ):
        """Initialize task

        Args:
            dispatcher: Creating dispatcher (provides context and tool config access)
            name: Unique task name
            inputs: Input resources (files or virtual), optional
            outputs: Output resources (files or virtual), optional
            description: Human-readable description
        """
        # Store dispatcher first (needed for parent_reporter setup)
        self.dispatcher = dispatcher

        # Initialize BuildStep with dispatcher as parent for progress nesting
        super().__init__(dispatcher.context, name)

        # Re-initialize UIReporter with dispatcher as parent
        UIReporter.__init__(self,
            reporter_name=self._log_name,
            parent_reporter=dispatcher  # Dispatcher is the parent for tasks
        )

        self.description = description or name

        # Private storage for inputs/outputs
        self.__inputs: list[BuildStep] = []
        self.__outputs: list[BuildStep] = []

        # Add initial inputs and outputs using the proper methods
        for inp in (inputs or []):
            self.add_input(inp)
        for out in (outputs or []):
            self.add_output(out)

    @property
    def pretty_name(self) -> str:
        return self.description or self.name

    @property
    def inputs(self):
        """Read-only view of task inputs

        Returns iterator over input BuildSteps. To add inputs, use add_input().
        """
        return iter(self.__inputs)

    @property
    def outputs(self):
        """Read-only view of task outputs

        Returns iterator over output BuildSteps. To add outputs, use add_output().
        """
        return iter(self.__outputs)

    def add_input(self, resource: BuildStep, *, consume: bool = None) -> None:
        """Add an input resource to this task

        Properly manages:
        - Optionally removes resource from pending queue based on typology
        - Adds resource to task inputs
        - Adds resource as a dependency of this task
        - Adds any dependents of the resource as dependencies of this task

        Args:
            resource: Input resource or build step to add
            consume: If True, remove from pending queue. If False, keep in pending.
                     If None (default), auto-detect based on typology:
                     - SOURCE: consume (remove from pending)
                     - INTERMEDIATE: don't consume (keep in pending for other tasks)
                     - OUTPUT: don't consume (shouldn't be used as input anyway)
        """
        # Determine if we should consume this resource
        if consume is None and isinstance(resource, Resource):
            # Auto-detect based on typology
            consume = resource.typology == ResourceTypology.SOURCE

        # Consume from pending and collect dependents regardless of whether we
        # have already wired the resource as an input: a Task adopted by a
        # peer dispatcher (cross-project sharing via the ResourceRegistry)
        # still needs to drain its sources from this caller's pending queue.
        if isinstance(resource, Resource) and resource.path and consume:
            dependents = self.dispatcher.context.remove_pending(resource.path)
        else:
            dependents = set()

        # Dependency edges are sets, so wiring them twice is a no-op.
        self.dependency_add(resource)
        for dep in dependents:
            self.dependency_add(dep)

        # The inputs list, however, is not a set; dedupe so work() doesn't see
        # the same resource twice.
        if resource not in self.__inputs:
            self.__inputs.append(resource)

    def add_output(self, resource: BuildStep) -> None:
        """Add an output resource to this task

        Properly manages:
        - Adds resource to task outputs
        - Makes resource depend on this task
        - Adds resource to pending queue (for downstream consumption)

        Args:
            resource: Output resource or build step to add
        """
        # Add to outputs list
        self.__outputs.append(resource)

        # Resource depends on this task
        resource.dependency_add(self)

        # For Resources with paths, add to pending for downstream
        if isinstance(resource, Resource) and resource.path:
            self.dispatcher.context.add_pending(resource)

    def is_rebuild_needed(self) -> bool:
        """Check if rebuild is needed based on timestamps

        Returns:
            True if rebuild needed, False if outputs are up-to-date
        """
        # If any input is virtual, always rebuild
        # (Virtual resources have no timestamp)
        if any(isinstance(inp, VirtualResource) for inp in self.depends_on):
            self.debug("Rebuild needed: has virtual inputs")
            return True

        # If any output is virtual, always rebuild
        if any(isinstance(out, VirtualResource) for out in self.expected_by):
            self.debug("Rebuild needed: has virtual outputs")
            return True

        # Get file inputs and outputs
        file_inputs = [inp for inp in self.depends_on if isinstance(inp, Resource)]
        file_outputs = [out for out in self.expected_by if isinstance(out, Resource)]

        # If no file outputs, always rebuild (shouldn't happen)
        if not file_outputs:
            self.debug("Rebuild needed: no file outputs")
            return True

        # Check if any output doesn't exist
        for output in file_outputs:
            if not output.exists():
                self.debug(f"Rebuild needed: output {output.path} doesn't exist")
                return True

        # If no inputs, outputs exist, so we're up-to-date
        if not file_inputs:
            self.debug("Up-to-date: no inputs, outputs exist")
            return False

        # Get oldest output timestamp
        oldest_output = min(out.mtime_get() for out in file_outputs)
        newest_input = max(i.mtime_get() for i in file_inputs if i.exists())

        if newest_input > oldest_output:
            self.debug(f"Rebuild needed, some input is newer than outputs")
            return True

        # All outputs exist and are newer than all inputs
        self.debug("Up-to-date: all outputs newer than all inputs")
        return False

    async def _work(self) -> None:
        if not self.is_rebuild_needed():
            self.info(f"Task {self.name}: up-to-date, skipping")
            return
        else:
            ret = await self.work()
            for stamp in self.outputs_of_type("build-stamp"):
                stamp.touch()
            return ret

    async def work(self) -> None:
        """Execute the task work

        Must be overridden by subclasses
        """
        ...

    def inputs_of_type(self, type : str) -> List[Resource]:
        return list(filter(lambda x: isinstance(x, Resource) and x.file_type == type, self.__inputs))

    def outputs_of_type(self, type : str) -> List[Resource]:
        return list(filter(lambda x: isinstance(x, Resource) and x.file_type == type, self.__outputs))
        
# Type for task executor function
TaskExecutor = Callable[['BuildContext', list[Any]], Awaitable[list[Any]]]

class ExecutorTask(Task):
    """A build task that awaits inputs, runs executor, and resolves outputs
    """

    def __init__(
        self,
        dispatcher: 'Dispatcher',
        name: str,
        inputs: list[BuildStep],
        outputs: list[BuildStep],
        executor: TaskExecutor,
        description: str = ""
    ):
        """Initialize task

        Args:
            dispatcher: Creating dispatcher (provides context and tool config access)
            name: Unique task name
            inputs: Input resources (files or virtual)
            outputs: Output resources (files or virtual)
            executor: Executor function
            description: Human-readable description
        """
        super().__init__(dispatcher, name, inputs = inputs, outputs =
                         outputs, description = description)
        self.executor = executor

    async def work(self) -> None:
        self.info(f"Task {self.name}: {self.description} - executing")

        # Run executor with semaphore
        # Convert inputs to list since executor expects a list
        inputs_list = list(self.inputs)
        async with self.context.semaphore:
            output_values = await self.executor(self.context, inputs_list)

        # Validate outputs
        outputs_list = list(self.outputs)
        if len(output_values) != len(outputs_list):
            raise RuntimeError(
                f"Task {self.name} produced {len(output_values)} outputs, "
                f"expected {len(outputs_list)}"
            )

        # Check file outputs were created
        for output, value in zip(outputs_list, output_values):
            if isinstance(output, Resource):
                if not output.path.exists():
                    raise RuntimeError(
                        f"Task {self.name} did not create output file {output.path}"
                    )
