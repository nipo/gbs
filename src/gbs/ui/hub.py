"""Central async feedback hub for all user-facing output

The FeedbackHub is the core of the UI system. It:
1. Provides a single async task that processes all user feedback
2. Routes messages to a pluggable backend for rendering
3. Supports hierarchical progress tracking via context managers
4. Works seamlessly with asyncio and is thread-safe

Usage:
    async with FeedbackHub(backend) as hub:
        hub.emit(LogMessage("info", "Starting build"))

        async with hub.progress("Building project") as prog:
            for task in tasks:
                await task.run()
                prog.update(1)
"""

from __future__ import annotations
import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Optional, TYPE_CHECKING

from .messages import (
    ProgressStart, ProgressUpdate, ProgressEnd,
    LogMessage, ToolMessage, BuildStatus
)

if TYPE_CHECKING:
    from .backends.base import FeedbackBackend

__all__ = ["FeedbackHub", "ProgressTracker", "NullHub"]


class ProgressTracker:
    """Helper for updating progress within a context manager"""

    def __init__(self, task_id: str, hub: FeedbackHub):
        self.task_id = task_id
        self._hub = hub

    def update(self, completed: Optional[int] = None, message: Optional[str] = None):
        """Update progress"""
        self._hub.emit(ProgressUpdate(
            task_id=self.task_id,
            completed=completed,
            message=message
        ))


class FeedbackHub:
    """Central async hub for all user-facing output

    All user feedback (logs, tool messages, progress, status) flows through
    this hub to a backend for rendering. This ensures:
    - Single point of control for all UI output
    - Clean separation between message generation and rendering
    - Backend can be swapped (Rich, simple text, JSON, etc.)
    - Thread-safe and async-safe message emission

    The hub runs a single async task that processes messages from a queue,
    ensuring proper ordering and preventing race conditions in output.
    """

    def __init__(self, backend: FeedbackBackend):
        """Initialize feedback hub

        Args:
            backend: Rendering backend (e.g., SimpleBackend, RichBackend)
        """
        self._backend = backend
        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None
        self._active_tasks: dict[str, ProgressStart] = {}  # Track active progress tasks
        self._started = False

    async def __aenter__(self) -> FeedbackHub:
        """Start the feedback processing task"""
        if self._started:
            raise RuntimeError("FeedbackHub already started")

        self._queue = asyncio.Queue()
        self._started = True

        # Start backend
        await self._backend.start()

        # Start message processing task
        self._task = asyncio.create_task(self._process_messages())

        return self

    async def __aexit__(self, *args):
        """Flush and stop the feedback hub"""
        if not self._started:
            return

        # Send sentinel to stop processing
        await self._queue.put(None)

        # Wait for processing to complete
        if self._task:
            await self._task

        # Stop backend
        await self._backend.stop()

        self._started = False

    async def _process_messages(self):
        """Single task that processes all feedback messages

        This ensures messages are rendered in order and prevents
        race conditions in output.
        """
        while True:
            msg = await self._queue.get()

            # None is sentinel for shutdown
            if msg is None:
                break

            # Track progress task lifecycle
            if isinstance(msg, ProgressStart):
                self._active_tasks[msg.task_id] = msg
            elif isinstance(msg, ProgressEnd):
                self._active_tasks.pop(msg.task_id, None)

            # Render message via backend
            await self._backend.render(msg)

    def emit(self, msg):
        """Emit a message to be rendered

        Thread-safe and async-safe. Messages are queued and processed
        by the hub's async task.

        Args:
            msg: Any message type (ToolMessage, LogMessage, ProgressStart, etc.)
        """
        if not self._started or not self._queue:
            # Hub not started - fall back to direct print
            print(str(msg))
            return

        # Queue message for async processing
        # Use put_nowait to avoid blocking
        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull:
            # Queue full - this shouldn't happen with unbounded queue
            # but handle gracefully
            print(f"Warning: feedback queue full, dropping message: {msg}")

    @asynccontextmanager
    async def progress(
        self,
        description: str,
        total: Optional[int] = None,
        parent_id: Optional[str] = None
    ):
        """Context manager for progress tracking

        Usage:
            async with hub.progress("Building project", total=10) as prog:
                for i in range(10):
                    await do_work()
                    prog.update(i + 1)

        Args:
            description: Human-readable description of the task
            total: Total number of steps (None for indeterminate)
            parent_id: Optional parent task ID for nested progress

        Yields:
            ProgressTracker for updating progress
        """
        task_id = uuid.uuid4().hex

        # Emit start message
        self.emit(ProgressStart(
            task_id=task_id,
            description=description,
            total=total,
            parent_id=parent_id
        ))

        success = True
        error_msg = None

        try:
            # Yield tracker for updates
            yield ProgressTracker(task_id, self)
        except Exception as e:
            success = False
            error_msg = str(e)
            raise
        finally:
            # Always emit end message
            self.emit(ProgressEnd(
                task_id=task_id,
                success=success,
                message=error_msg
            ))

    def log(self, level: str, message: str, source: Optional[str] = None):
        """Convenience method for emitting log messages

        Args:
            level: Log level (debug, info, warning, error, critical)
            message: Log message
            source: Optional source/logger name
        """
        self.emit(LogMessage(level=level, message=message, source=source))

    def tool_message(
        self,
        severity,
        message: str,
        file_path: Optional = None,
        line: Optional[int] = None,
        **kwargs
    ):
        """Convenience method for emitting tool messages

        Args:
            severity: MessageSeverity
            message: Message text
            file_path: Optional source file path
            line: Optional line number
            **kwargs: Additional ToolMessage fields
        """
        from .messages import MessageSeverity
        self.emit(ToolMessage(
            severity=severity,
            message=message,
            file_path=file_path,
            line=line,
            **kwargs
        ))


class NullHub:
    """No-op hub for when no real hub is active

    Provides the same interface as FeedbackHub but does nothing.
    This allows code to call hub methods without checking if hub exists.
    """

    def emit(self, msg):
        """No-op emit"""
        pass

    @asynccontextmanager
    async def progress(
        self,
        description: str,
        total: Optional[int] = None,
        parent_id: Optional[str] = None
    ):
        """No-op progress context manager"""
        tracker = type('NullProgressTracker', (), {
            'task_id': 'null',
            'update': lambda self, *args, **kwargs: None
        })()
        yield tracker

    def log(self, level: str, message: str, source: Optional[str] = None):
        """No-op log"""
        pass

    def tool_message(
        self,
        severity,
        message: str,
        file_path: Optional = None,
        line: Optional[int] = None,
        **kwargs
    ):
        """No-op tool message"""
        pass


# Global hub instance (set by CLI main())
_global_hub: Optional[FeedbackHub] = None
_null_hub = NullHub()


def set_global_hub(hub: Optional[FeedbackHub]):
    """Set the global feedback hub instance

    This allows code that doesn't have direct access to the hub
    (e.g., deep in the call stack) to emit messages.
    """
    global _global_hub
    _global_hub = hub


def get_global_hub() -> FeedbackHub:
    """Get the global feedback hub instance

    Returns a NullHub if no hub is active (e.g., during testing or
    when run outside of normal CLI context). This allows code to
    unconditionally call hub methods without checking for None.
    """
    return _global_hub or _null_hub


def emit(msg):
    """Emit a message to the global hub (if active)

    Convenience function for emitting messages without explicitly
    passing the hub around.
    """
    hub = get_global_hub()
    if hub:
        hub.emit(msg)
    else:
        # No hub - fall back to print
        print(str(msg))
