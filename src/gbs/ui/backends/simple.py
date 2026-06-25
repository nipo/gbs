"""Simple plain-text backend for terminal and CI output

Renders messages as plain text without colors or fancy formatting.
Suitable for:
- CI/CD environments
- Non-interactive terminals
- Log files
- Simple terminals without color support
"""

from __future__ import annotations
import io
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
        self.output = output or self._safe_stream(sys.stdout)
        self.error = error or self._safe_stream(sys.stderr)
        self.show_progress = show_progress
        self.min_severity = min_severity
        self.min_log_level = min_log_level

        # Track active progress tasks for indentation and end-of-task
        # reporting. We only show progress start descriptions on
        # completion ("[OK] {description}") rather than at each step, to
        # keep non-TTY output focused on outcomes.
        self._progress_stack: list[str] = []  # Stack of task IDs
        self._progress_indent: dict[str, int] = {}  # task_id -> indent level
        self._progress_description: dict[str, str] = {}  # task_id -> description

    @staticmethod
    def _safe_stream(stream: TextIO) -> TextIO:
        """Wrap a stream to handle encoding errors gracefully.

        On Windows, sys.stdout may use a legacy encoding (e.g., CP1252)
        that cannot represent all Unicode characters. This wraps the
        stream to replace unencodable characters instead of raising.
        """
        if hasattr(stream, 'buffer'):
            encoding = getattr(stream, 'encoding', 'utf-8') or 'utf-8'
            try:
                return io.TextIOWrapper(
                    stream.buffer, encoding=encoding, errors='replace',
                    line_buffering=stream.line_buffering,
                )
            except Exception:
                pass
        return stream

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
        """Record progress start (no output).

        The non-TTY backend doesn't print "> description" lines because
        the matching ProgressEnd carries everything we need to convey:
        the outcome and what it was. Stashing description and indent
        here lets the end handler emit a single, fully-identified line.
        """
        if not self.show_progress:
            return

        # Determine indent level
        if msg.parent_id and msg.parent_id in self._progress_indent:
            indent_level = self._progress_indent[msg.parent_id] + 1
        else:
            indent_level = 0

        self._progress_indent[msg.task_id] = indent_level
        self._progress_description[msg.task_id] = msg.description
        self._progress_stack.append(msg.task_id)

    async def _render_progress_update(self, msg: ProgressUpdate):
        """Skip intermediate progress updates.

        BuildStep auto-emits "waiting" / "starting" / "complete"
        lifecycle messages that duplicate what ProgressEnd already
        conveys, and inline progress in non-TTY logs is more noise
        than signal. Final outcome (with description) lands via
        ProgressEnd.
        """
        return

    async def _render_progress_end(self, msg: ProgressEnd):
        """Render progress end"""
        if not self.show_progress:
            return

        # Remove from stack
        if msg.task_id in self._progress_stack:
            self._progress_stack.remove(msg.task_id)

        indent_level = self._progress_indent.pop(msg.task_id, 0)
        description = self._progress_description.pop(msg.task_id, "")
        indent = "  " * indent_level

        # Show completion status with the description recorded at start
        # (falls back to msg.message if the start was never seen)
        if msg.success:
            status = "[OK]"
        else:
            status = "[FAILED]"

        label = description or msg.message or "Done"
        suffix = f": {msg.message}" if msg.message and msg.message != description else ""
        self.output.write(f"{indent}{status} {label}{suffix}\n")
        self.output.flush()

    async def _render_build_status(self, msg: BuildStatus):
        """Render build status in the same shape as ProgressEnd.

        "started" is dropped: the corresponding "[OK] target" already
        announces what happened, and the "started" line carries no
        extra information once the task is done. Failure/error/skipped
        map to dedicated tags so the outcome is the first thing visible.
        """
        status_map = {
            "success": "[OK]",
            "failure": "[FAILED]",
            "error": "[FAILED]",
            "skipped": "[SKIPPED]",
        }
        if msg.status == "started":
            return
        status = status_map.get(msg.status, f"[{msg.status.upper()}]")

        duration_str = f" ({msg.duration:.1f}s)" if msg.duration is not None else ""
        message_str = f" - {msg.message}" if msg.message else ""
        self.output.write(f"{status} {msg.target}{duration_str}{message_str}\n")
        self.output.flush()
