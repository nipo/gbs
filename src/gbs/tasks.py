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

from gbs.logging import get_logger

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


class BuildContext:
    """Shared build context passed to all tasks and resources

    Contains:
    - Semaphore for parallel execution control
    - Project configuration
    - Build settings
    - Resource registry
    - All steps
    """

    def __init__(self, max_parallel: int = 4, project_config: Optional[dict[str, Any]] = None):
        """Initialize build context

        Args:
            max_parallel: Maximum number of tasks to run in parallel
            project_config: Optional project configuration
        """
        self._max_parallel = max_parallel
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._resources: dict[Path, 'Resource'] = {}
        self._virtual_resources: dict[str, 'VirtualResource'] = {}
        self.project_config = project_config or {}
        self.steps = set()
        self.running = set()
        self.logger = get_logger("BuildContext")
        self.logger.debug("created")

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
        
        self.context.step_register(self)

    def dependency_add(self, dep: BuildStep) -> None:
        """Add a relation between us and another step we depend
        on. Also adds the reverse relation.
        """
        self.depends_on.add(dep)
        dep.expected_by.add(self)
        
    def __repr__(self) -> str:
        return self._log_name

    def launch(self) -> asyncio.Task:
        if self.task:
            return self.task
        self.logger.debug("launch")
        self.task = asyncio.create_task(self.__worker())
        return self.task

    async def __worker(self):
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
            self.__mark_done(deps_failed)
            return
                    
        self.logger.debug("Starting work")
        try:
            await self._work()
        except Exception as e:
            self.logger.debug("Work excepted: %s", e)
            #import traceback
            #traceback.print_exc()
            self.__mark_done(e)

        self.logger.debug("Work done")
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
