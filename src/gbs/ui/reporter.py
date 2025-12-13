"""UI reporting mixin for classes that need logging and progress tracking

Provides a clean interface for classes to report status, progress, and messages
without directly managing hub references or logger instances.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    from .messages import MessageSeverity, LogLevel

__all__ = ["UIReporter"]


class UIReporter:
    """Mixin class providing UI reporting and logging capabilities

    This mixin provides convenient methods for:
    - Logging (debug, info, warning, error, critical)
    - Tool messages (compiler-style diagnostics)
    - Progress tracking (start_progress, update_progress, end_progress)
    - Build status reporting

    Example:
        class MyTask(UIReporter):
            def __init__(self, name: str):
                UIReporter.__init__(self, reporter_name=f"MyTask({name})")

            async def work(self):
                self.start_progress("Building project", total=100)
                for i in range(100):
                    await process_item(i)
                    self.update_progress(i + 1, f"Processing item {i}")
                self.end_progress(success=True)
    """

    # Internal attributes
    _reporter_name: str = "unknown"
    _reporter_progress_id: Optional[str] = None  # Progress task ID (set by start_progress)
    _reporter_parent: Optional['UIReporter'] = None  # Parent reporter for nested progress

    def __init__(self, reporter_name: str, parent_reporter: Optional['UIReporter'] = None):
        """Initialize UIReporter

        Args:
            reporter_name: Name/identifier for this reporter (used as log source)
            parent_reporter: Optional parent UIReporter for automatic progress nesting
        """
        self._reporter_name = reporter_name
        self._reporter_progress_id = None
        self._reporter_parent = parent_reporter

    def _emit_log(self, level: 'LogLevel', message: str):
        """Internal method to emit a log message with automatic context

        Args:
            level: Log level enum value
            message: Log message
        """
        from .hub import get_global_hub
        from .messages import LogMessage

        hub = get_global_hub()
        hub.emit(LogMessage(
            level=level,
            message=message,
            source=self._reporter_name
        ))

    def debug(self, message: str):
        """Emit a DEBUG log message

        Args:
            message: Log message
        """
        from .messages import LogLevel
        self._emit_log(LogLevel.DEBUG, message)

    def info(self, message: str):
        """Emit an INFO log message

        Args:
            message: Log message
        """
        from .messages import LogLevel
        self._emit_log(LogLevel.INFO, message)

    def warning(self, message: str):
        """Emit a WARNING log message

        Args:
            message: Log message
        """
        from .messages import LogLevel
        self._emit_log(LogLevel.WARNING, message)

    def error(self, message: str, exc_info: bool = False):
        """Emit an ERROR log message

        Args:
            message: Log message
            exc_info: If True, append exception traceback to message
        """
        from .messages import LogLevel

        # If exc_info is True, append traceback to message
        if exc_info:
            import traceback
            tb = traceback.format_exc()
            message = f"{message}\n{tb}"

        self._emit_log(LogLevel.ERROR, message)

    def critical(self, message: str):
        """Emit a CRITICAL log message

        Args:
            message: Log message
        """
        from .messages import LogLevel
        self._emit_log(LogLevel.CRITICAL, message)

    def emit_tool_message(
        self,
        severity: 'MessageSeverity',
        message: str,
        file_path: Optional[Path] = None,
        line: Optional[int] = None,
        column: Optional[int] = None,
        identifier: Optional[str] = None,
        extended_message: Optional[str] = None
    ):
        """Emit a tool message (compiler-style diagnostic)

        Args:
            severity: Message severity
            message: Main message text
            file_path: Source file path
            line: Line number
            column: Column number
            identifier: Error/warning identifier code
            extended_message: Extended diagnostic information
        """
        from .hub import get_global_hub
        from .messages import ToolMessage

        hub = get_global_hub()
        hub.emit(ToolMessage(
            severity=severity,
            message=message,
            file_path=file_path,
            line=line,
            column=column,
            identifier=identifier,
            extended_message=extended_message
        ))

    def emit_build_status(
        self,
        target: str,
        status: str,
        duration: Optional[float] = None,
        message: Optional[str] = None
    ):
        """Emit a build status message

        Args:
            target: Build target name
            status: Status string (started, success, failure, etc.)
            duration: Optional duration in seconds
            message: Optional status message
        """
        from .hub import get_global_hub
        from .messages import BuildStatus

        hub = get_global_hub()
        hub.emit(BuildStatus(
            target=target,
            status=status,
            duration=duration,
            message=message
        ))

    # Progress tracking methods

    def start_progress(
        self,
        description: str,
        total: Optional[int] = None,
        transient: bool = True
    ):
        """Start a progress task

        Args:
            description: Human-readable description of the task
            total: Total number of steps (None for indeterminate progress)
            transient: If True, progress bar disappears when complete
        """
        from .hub import get_global_hub
        from .messages import ProgressStart
        import uuid

        # Generate unique task ID
        self._reporter_progress_id = uuid.uuid4().hex

        # Automatically get parent's progress ID if available
        parent_id = None
        if self._reporter_parent and hasattr(self._reporter_parent, '_reporter_progress_id'):
            parent_id = self._reporter_parent._reporter_progress_id

        hub = get_global_hub()
        hub.emit(ProgressStart(
            task_id=self._reporter_progress_id,
            description=description,
            total=total,
            parent_id=parent_id,
            transient=transient
        ))

    def update_progress(
        self,
        completed: Optional[int] = None,
        message: Optional[str] = None
    ):
        """Update progress task

        Args:
            completed: Number of completed steps
            message: Optional status message
        """
        if self._reporter_progress_id is None:
            # Progress not started, ignore update
            return

        from .hub import get_global_hub
        from .messages import ProgressUpdate

        hub = get_global_hub()
        hub.emit(ProgressUpdate(
            task_id=self._reporter_progress_id,
            completed=completed,
            message=message
        ))

    def end_progress(
        self,
        success: bool = True,
        message: Optional[str] = None
    ):
        """End a progress task

        Args:
            success: Whether the task completed successfully
            message: Optional completion message (typically only for failures)
        """
        if self._reporter_progress_id is None:
            # Progress not started, ignore end
            return

        from .hub import get_global_hub
        from .messages import ProgressEnd

        hub = get_global_hub()
        hub.emit(ProgressEnd(
            task_id=self._reporter_progress_id,
            success=success,
            message=message
        ))

        # Clear the progress ID
        self._reporter_progress_id = None
