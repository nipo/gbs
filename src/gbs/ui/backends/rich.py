"""Rich backend for fancy terminal output

Provides colorized output, live progress bars, and styled formatting
using the Rich library. Automatically falls back gracefully if Rich
is not available.
"""

from __future__ import annotations
import atexit
import sys
from typing import TextIO, Optional, Dict
from pathlib import Path

from .base import FeedbackBackend
from ..messages import (
    ToolMessage, LogMessage, ProgressStart, ProgressUpdate,
    ProgressEnd, BuildStatus, SummaryLine, MessageSeverity, LogLevel
)

__all__ = ["RichBackend", "is_rich_available"]


def is_rich_available() -> bool:
    """Check if Rich library is available"""
    try:
        import rich
        return True
    except ImportError:
        return False


class RichBackend(FeedbackBackend):
    """Fancy terminal output using Rich library

    Features:
    - Colorized output based on severity
    - Live progress bars with spinners
    - Styled formatting for different message types
    - Smart terminal detection

    Falls back to basic output if Rich is not available.
    """

    def __init__(
        self,
        use_colors: bool = True,
        show_progress: bool = True,
        min_severity: MessageSeverity = MessageSeverity.WARNING,
        min_log_level: LogLevel = LogLevel.WARNING,
        force_terminal: Optional[bool] = None,
        file_url_template: str = ""
    ):
        """Initialize Rich backend

        Args:
            use_colors: Whether to use colors (default: True)
            show_progress: Whether to show progress bars
            min_severity: Minimum severity for ToolMessages to display
            min_log_level: Minimum level for LogMessages to display
            force_terminal: Override terminal detection (None = auto-detect)
            file_url_template: Template for file URLs in OSC 8 hyperlinks
                              Supports {path}, {line}, {column} placeholders
                              Should be provided by caller from GBSConfig.file_url_template
        """
        if not is_rich_available():
            raise ImportError(
                "Rich library not available. Install with: pip install rich\n"
                "Or use SimpleBackend instead."
            )

        from rich.console import Console
        from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, TaskProgressColumn

        self.use_colors = use_colors
        self.show_progress = show_progress
        self.min_severity = min_severity
        self.min_log_level = min_log_level
        self.file_url_template = file_url_template

        # Create console
        self.console = Console(
            force_terminal=force_terminal,
            highlight=False,  # Don't auto-highlight, we control styling
            color_system="auto" if use_colors else None
        )

        self.progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=self.console,
            transient=False,
            expand=False
        )

        # Track active progress tasks
        self._progress_tasks: Dict[str, int] = {}  # task_id -> rich task_id
        self._progress_transient: Dict[str, bool] = {}  # task_id -> is_transient
        self._progress_started = False

        # Register atexit handler to restore terminal state (cursor visibility)
        # in case of abnormal exit while progress bars are active
        atexit.register(self._restore_terminal)

    def _restore_terminal(self):
        """Restore terminal state (show cursor) on exit"""
        if self._progress_started:
            self.progress.stop()
            self._progress_started = False

    async def start(self):
        """Initialize backend"""
        pass

    async def stop(self):
        """Stop progress and flush"""
        if self._progress_started:
            self.progress.stop()
            self._progress_started = False
        atexit.unregister(self._restore_terminal)


    async def render(self, msg):
        """Render a message with Rich formatting

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
        elif isinstance(msg, SummaryLine):
            await self._render_summary_line(msg)
        else:
            # Fallback: just print
            self.console.print(str(msg))

    async def _render_tool_message(self, msg: ToolMessage):
        """Render a tool message with colors and OSC 8 hyperlinks"""
        # Filter based on minimum severity
        if msg.severity < self.min_severity:
            return

        # Choose color based on severity
        severity_styles = {
            MessageSeverity.DEBUG: "dim",
            MessageSeverity.NOTICE: "cyan",
            MessageSeverity.INFO: "blue",
            MessageSeverity.WARNING: "yellow",
            MessageSeverity.ERROR: "red",
            MessageSeverity.FATAL: "bold red",
        }
        base_style = severity_styles.get(msg.severity, "")

        # Escape user content to prevent Rich markup interpretation
        from rich.markup import escape
        from rich.text import Text
        from rich.style import Style

        escaped_message = escape(msg.message)
        escaped_identifier = escape(msg.identifier) if msg.identifier else None

        # Build message using Text API for OSC 8 hyperlink support
        result = Text()

        if msg.file_path:
            # Compiler-style format: file:line:level: message
            # Use full word for emacs compilation-mode compatibility
            location = str(msg.file_path)
            if msg.line is not None:
                location += f":{msg.line}"
                if msg.column is not None:
                    location += f":{msg.column}"

            # Create file URL for OSC 8 hyperlink using template
            from pathlib import Path
            abs_path = Path(msg.file_path).resolve()

            # Use template with placeholders: {path}, {line}, {column}
            file_url = self.file_url_template.format(
                path=abs_path,
                line=msg.line if msg.line is not None else 1,
                column=msg.column if msg.column is not None else 0
            )

            # Add clickable location with hyperlink
            result.append(location, style=Style(color="white", bold=True, link=file_url))
            result.append(f":{msg.severity.value}: ", style=base_style)
        else:
            # Short bracketed form for non-file messages
            severity_code = msg.severity.short_code()
            result.append(f"[{severity_code}] ", style=base_style)

        # Add identifier if present
        if escaped_identifier:
            result.append(f"({escaped_identifier}) ", style=Style(color="bright_black"))

        # Add message
        result.append(escaped_message, style=base_style)

        # Print the assembled text
        self.console.print(result)

        # Print extended message if present
        if msg.extended_message:
            escaped_extended = escape(msg.extended_message)
            self.console.print(f"  {escaped_extended}", style="dim")

    async def _render_log_message(self, msg: LogMessage):
        """Render a log message with colors"""
        # Filter based on minimum log level
        if msg.level < self.min_log_level:
            return

        # Choose style based on level
        level_styles = {
            LogLevel.DEBUG: "dim",
            LogLevel.INFO: "blue",
            LogLevel.WARNING: "yellow",
            LogLevel.ERROR: "red",
            LogLevel.CRITICAL: "bold red",
        }
        style = level_styles.get(msg.level, "")

        # Escape user content to prevent Rich markup interpretation
        from rich.markup import escape
        escaped_message = escape(msg.message)
        escaped_source = escape(msg.source) if msg.source else None

        # Format message with escaped content and short level code
        level_code = msg.level.short_code()
        if escaped_source:
            text = f"[{level_code}] {escaped_source}: {escaped_message}"
        else:
            text = f"[{level_code}] {escaped_message}"

        self.console.print(text, style=style)

    async def _render_progress_start(self, msg: ProgressStart):
        """Start a progress task"""
        if not self.show_progress:
            return

        # Start progress display if not already started
        if not self._progress_started:
            self.progress.start()
            self._progress_started = True

        rich_task_id = self.progress.add_task(
            msg.description,
            total=msg.total,
        )
        self._progress_tasks[msg.task_id] = rich_task_id
        self._progress_transient[msg.task_id] = msg.transient

    def _sort_progress_tasks(self):
        """Sort progress tasks by completion percentage (most advanced on top)"""
        try:
            # Build reverse lookup: rich_task_id -> our task_id
            rich_to_task = {v: k for k, v in self._progress_tasks.items()}

            # Get sorted task IDs
            sorted_ids = sorted(
                self.progress._tasks.keys(),
                key=lambda tid: (
                    # Non-transient tasks first (BuildContext) - False < True
                    self._progress_transient.get(rich_to_task.get(tid, ""), True),
                    # Then by completion percentage (descending) - negate for descending
                    -(self.progress._tasks[tid].completed / self.progress._tasks[tid].total * 100
                      if self.progress._tasks[tid].total else 0)
                )
            )

            # Rebuild _tasks dict in sorted order
            # Python 3.7+ dicts maintain insertion order
            with self.progress._lock:
                sorted_tasks = {tid: self.progress._tasks[tid] for tid in sorted_ids}
                self.progress._tasks.clear()
                self.progress._tasks.update(sorted_tasks)
        except (AttributeError, IndexError, KeyError, TypeError, RuntimeError):
            # If Rich's internals change or tasks don't exist, just skip sorting
            pass

    async def _render_progress_update(self, msg: ProgressUpdate):
        """Update progress task"""
        if not self.show_progress or msg.task_id not in self._progress_tasks:
            return

        rich_task_id = self._progress_tasks[msg.task_id]

        # Update progress
        if msg.completed is not None:
            self.progress.update(rich_task_id, completed=msg.completed)

        if msg.message:
            self.progress.update(rich_task_id, description=msg.message)

        # Sort tasks so most advanced ones are on top
        self._sort_progress_tasks()

    async def _render_progress_end(self, msg: ProgressEnd):
        """Complete progress task"""
        if not self.show_progress or msg.task_id not in self._progress_tasks:
            return

        rich_task_id = self._progress_tasks.pop(msg.task_id)
        is_transient = self._progress_transient.pop(msg.task_id, False)

        # Look up the underlying Task via the keyed dict.
        # progress.tasks is a list indexed by position, not by TaskID, so
        # indexing it with rich_task_id is incorrect once any task has been
        # removed.
        task = self.progress._tasks.get(rich_task_id)
        task_description = task.description if task is not None else ""

        if task is not None and task.total:
            try:
                self.progress.update(rich_task_id, completed=task.total)
            except (IndexError, KeyError):
                pass

        if is_transient:
            try:
                self.progress.remove_task(rich_task_id)
            except (IndexError, KeyError):
                pass

            if not msg.success and msg.message:
                self.console.print(f"[red][FAILED] {task_description}: {msg.message}[/red]")
            elif not msg.success:
                self.console.print(f"[red][FAILED] {task_description}[/red]")

    async def _render_summary_line(self, msg: SummaryLine):
        """Render a single line of the build failure summary.

        Uses Rich's Console.print, which composes above any active
        Live region (the task progress bars) without racing the
        redraw. Style hints from the message map to Rich's inline
        markup — Rich handles ANSI translation for the terminal
        while a future GUI backend can read fg/bold off the message
        directly.
        """
        text = msg.text
        styles = []
        if msg.fg:
            styles.append(msg.fg)
        if msg.bold:
            styles.append("bold")
        style = " ".join(styles) if styles else None
        # escape() so any user-supplied text (paths, error strings)
        # containing Rich markup like '[foo]' does not get parsed.
        from rich.markup import escape
        self.console.print(escape(text), style=style)

    async def _render_build_status(self, msg: BuildStatus):
        """Render build status with colors"""
        # Choose style based on status
        status_styles = {
            "started": "blue",
            "success": "green",
            "failure": "red",
            "error": "bold red",
            "skipped": "dim",
            "unplannable": "dim",
        }
        style = status_styles.get(msg.status, "")

        # Format status
        parts = [f"{msg.target}: {msg.status}"]
        if msg.duration is not None:
            parts.append(f"({msg.duration:.1f}s)")
        if msg.message:
            parts.append(f"- {msg.message}")

        self.console.print(" ".join(parts), style=style)
