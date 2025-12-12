"""Simple plain-text backend for terminal and CI output

Renders messages as plain text without colors or fancy formatting.
Suitable for:
- CI/CD environments
- Non-interactive terminals
- Log files
- Simple terminals without color support
"""

from __future__ import annotations
import sys
from typing import TextIO, Optional

from .base import FeedbackBackend
from ..messages import (
    ToolMessage, LogMessage, ProgressStart, ProgressUpdate,
    ProgressEnd, BuildStatus, MessageSeverity, LogLevel
)

__all__ = ["SimpleBackend"]


class SimpleBackend(FeedbackBackend):
    """Plain text output backend

    Renders all messages as simple text lines to stdout/stderr.
    No colors, no progress bars, just straightforward text output.
    """

    def __init__(
        self,
        output: Optional[TextIO] = None,
        error: Optional[TextIO] = None,
        show_progress: bool = True,
        min_severity: MessageSeverity = MessageSeverity.WARNING,
        min_log_level: LogLevel = LogLevel.WARNING
    ):
        """Initialize simple backend

        Args:
            output: Stream for normal output (default: sys.stdout)
            error: Stream for error output (default: sys.stderr)
            show_progress: Whether to show progress messages
            min_severity: Minimum severity for ToolMessages to display
            min_log_level: Minimum level for LogMessages to display
        """
        self.output = output or sys.stdout
        self.error = error or sys.stderr
        self.show_progress = show_progress
        self.min_severity = min_severity
        self.min_log_level = min_log_level

        # Track active progress tasks for indentation
        self._progress_stack: list[str] = []  # Stack of task IDs
        self._progress_indent: dict[str, int] = {}  # task_id -> indent level

    async def start(self):
        """Initialize backend"""
        pass

    async def stop(self):
        """Flush output"""
        self.output.flush()
        self.error.flush()

    async def render(self, msg):
        """Render a message to plain text

        Args:
            msg: Message to render
        """
        # Route to appropriate handler
        if isinstance(msg, ToolMessage):
            await self._render_tool_message(msg)
        elif isinstance(msg, LogMessage):
            await self._render_log_message(msg)
        elif isinstance(msg, ProgressStart):
            await self._render_progress_start(msg)
        elif isinstance(msg, ProgressUpdate):
            await self._render_progress_update(msg)
        elif isinstance(msg, ProgressEnd):
            await self._render_progress_end(msg)
        elif isinstance(msg, BuildStatus):
            await self._render_build_status(msg)
        else:
            # Fallback: just str() the message
            self.output.write(str(msg) + "\n")

    async def _render_tool_message(self, msg: ToolMessage):
        """Render a tool message (compiler-style output)"""
        # Filter based on minimum severity
        if msg.severity < self.min_severity:
            return

        # Use the message's __str__ which formats it properly
        text = str(msg)

        # Route errors to stderr, others to stdout
        stream = self.error if msg.severity >= MessageSeverity.ERROR else self.output

        stream.write(text + "\n")
        stream.flush()

    async def _render_log_message(self, msg: LogMessage):
        """Render a log message"""
        # Filter based on minimum log level
        if msg.level < self.min_log_level:
            return

        # Route errors to stderr
        stream = self.error if msg.level >= LogLevel.ERROR else self.output

        stream.write(str(msg) + "\n")
        stream.flush()

    async def _render_progress_start(self, msg: ProgressStart):
        """Render progress start"""
        if not self.show_progress:
            return

        # Determine indent level
        if msg.parent_id and msg.parent_id in self._progress_indent:
            indent_level = self._progress_indent[msg.parent_id] + 1
        else:
            indent_level = 0

        self._progress_indent[msg.task_id] = indent_level
        self._progress_stack.append(msg.task_id)

        # Render with indentation
        indent = "  " * indent_level
        total_str = f"/{msg.total}" if msg.total is not None else ""
        self.output.write(f"{indent}▸ {msg.description}{total_str}\n")
        self.output.flush()

    async def _render_progress_update(self, msg: ProgressUpdate):
        """Render progress update"""
        if not self.show_progress:
            return

        # For simple backend, we only render updates with messages
        # (not every increment, that would be too noisy)
        if msg.message:
            indent_level = self._progress_indent.get(msg.task_id, 0)
            indent = "  " * (indent_level + 1)
            completed_str = f"[{msg.completed}] " if msg.completed is not None else ""
            self.output.write(f"{indent}{completed_str}{msg.message}\n")
            self.output.flush()

    async def _render_progress_end(self, msg: ProgressEnd):
        """Render progress end"""
        if not self.show_progress:
            return

        # Remove from stack
        if msg.task_id in self._progress_stack:
            self._progress_stack.remove(msg.task_id)

        indent_level = self._progress_indent.pop(msg.task_id, 0)
        indent = "  " * indent_level

        # Show completion status
        if msg.success:
            status = "✓"
        else:
            status = "✗"

        message_str = f": {msg.message}" if msg.message else ""
        self.output.write(f"{indent}{status} Done{message_str}\n")
        self.output.flush()

    async def _render_build_status(self, msg: BuildStatus):
        """Render build status"""
        self.output.write(str(msg) + "\n")
        self.output.flush()
