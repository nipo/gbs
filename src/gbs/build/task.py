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
from .message import *

from ..logging import get_logger

__all__ = ["BuildError", "MissingToolError", "PrerequisiteFailed", "BuildStep",
           "VirtualResource", "Resource", "Task", "ExecutorTask", "ResourceTypology"]


class ResourceTypology(Enum):
    """Typology (classification) of a resource in the build graph"""
    SOURCE = "source"           # User-provided source file
    INTERMEDIATE = "intermediate"  # Generated during build (default)
    OUTPUT = "output"           # Final deliverable

try:
    import click
except ImportError:
    click = None


class BuildError(Exception):
    """Error during build execution"""
    pass

class MissingToolError(BuildError):
    """Required tool not found or not executable

    This exception indicates a tool (like ghdl, yosys, etc.) is missing
    or not properly configured.
    """
    pass

class PrerequisiteFailed(Exception):
    """Prerequisite failed while we were waiting on it"""
    pass

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

        tool_config = self.dispatcher.get_tool_config()
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
                    self.logger.debug(
                        f"Message {msg.identifier}: severity {old_severity.value} → {new_severity.value}"
                    )
                except KeyError:
                    self.logger.warning(
                        f"Invalid severity level '{new_severity_str}' for message {msg.identifier}, "
                        f"using original severity {msg.severity.value}"
                    )

        msg.origin = self
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
        self.task = asyncio.create_task(self.__worker())
        return self.task

    async def __worker(self):
        # Initialize progress
        await self.update_progress(0.0, "Waiting")

        # Wait for all dependencies if any
        deps_failed = None
        pending = self.depends_on
        failing = []
        while pending:
            self.logger.debug("%s Blocked by %s", self.name, pending)
            done, pending = await asyncio.wait(pending, return_when = asyncio.FIRST_COMPLETED)
            for fut in done:
                try:
                    r = fut.result()
                except Exception as e:
                    deps_failed = PrerequisiteFailed(fut)
                    failing.append(fut)

        if deps_failed is not None:
            self.logger.debug("%s deps failed: %s", self.name, deps_failed)
            await self.update_progress(.001, f"Prereq failed: {failing}")
            self.__mark_done(deps_failed)
            return

        await self.update_progress(0.001, "Starting")
        try:
            await self._work()
        except Exception as e:
            self.logger.debug("%s failed: %s", self.name, e)
            await self.update_progress(1.0, "Failed")
            self.__mark_done(e)
            return

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

        # Populate metadata dict for backward compatibility with code that accesses
        # metadata["file_type"], metadata["library"], etc.
        self.metadata = {}
        if file_type is not None:
            self.metadata["file_type"] = file_type
        if library is not None:
            self.metadata["library"] = library
        if file_type_version is not None:
            self.metadata["file_type_version"] = file_type_version

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

    @property
    def is_source(self) -> bool:
        """Compatibility property for legacy code"""
        return self.typology == ResourceTypology.SOURCE

    @property
    def is_output(self) -> bool:
        """Compatibility property for legacy code"""
        return self.typology == ResourceTypology.OUTPUT

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

    def __init__(
        self,
        dispatcher: 'Dispatcher',
        name: str,
        inputs: list[BuildStep],
        outputs: list[BuildStep],
        description: str = ""
    ):
        """Initialize task

        Args:
            dispatcher: Creating dispatcher (provides context and tool config access)
            name: Unique task name
            inputs: Input resources (files or virtual)
            outputs: Output resources (files or virtual)
            description: Human-readable description
        """
        super().__init__(dispatcher.context, name)
        self.description = description or name
        self.inputs = inputs
        self.outputs = outputs
        self.dispatcher = dispatcher

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
        return list(filter(lambda x: isinstance(x, Resource) and x.metadata.get("file_type") == type, self.inputs))

    def outputs_of_type(self, type : str) -> List[Resource]:
        return list(filter(lambda x: isinstance(x, Resource) and x.metadata.get("file_type") == type, self.outputs))
        
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
