"""Rich backend for fancy terminal output

Provides colorized output, live progress bars, and styled formatting
using the Rich library. Automatically falls back gracefully if Rich
is not available.
"""

from __future__ import annotations
import sys
from typing import TextIO, Optional, Dict
from pathlib import Path

from .base import FeedbackBackend
from ..messages import (
    ToolMessage, LogMessage, ProgressStart, ProgressUpdate,
    ProgressEnd, BuildStatus, MessageSeverity, LogLevel
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
        force_terminal: Optional[bool] = None
    ):
        """Initialize Rich backend

        Args:
            use_colors: Whether to use colors (default: True)
            show_progress: Whether to show progress bars
            min_severity: Minimum severity for ToolMessages to display
            min_log_level: Minimum level for LogMessages to display
            force_terminal: Override terminal detection (None = auto-detect)
        """
        if not is_rich_available():
            raise ImportError(
                "Rich library not available. Install with: pip install rich\n"
                "Or use SimpleBackend instead."
            )

        from rich.console import Console
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

        self.use_colors = use_colors
        self.show_progress = show_progress
        self.min_severity = min_severity
        self.min_log_level = min_log_level

        # Create console
        self.console = Console(
            force_terminal=force_terminal,
            highlight=False,  # Don't auto-highlight, we control styling
            color_system="auto" if use_colors else None
        )

        # Create progress display
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=self.console,
            transient=False  # Keep progress visible after completion
        )

        # Track active progress tasks
        self._progress_tasks: Dict[str, int] = {}  # task_id -> rich task_id
        self._progress_started = False

    async def start(self):
        """Initialize backend"""
        pass

    async def stop(self):
        """Stop progress and flush"""
        if self._progress_started:
            self.progress.stop()
            self._progress_started = False

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
        else:
            # Fallback: just print
            self.console.print(str(msg))

    async def _render_tool_message(self, msg: ToolMessage):
        """Render a tool message with colors"""
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
        style = severity_styles.get(msg.severity, "")

        # Format location
        if msg.file_path:
            location = str(msg.file_path)
            if msg.line is not None:
                location += f":{msg.line}"
                if msg.column is not None:
                    location += f":{msg.column}"
            location_text = f"[bold]{location}[/bold]:{msg.severity.value.upper()}:"
        else:
            location_text = f"[{msg.severity.value.upper()}]"

        # Format message
        if msg.identifier:
            text = f"{location_text} [dim]({msg.identifier})[/dim] {msg.message}"
        else:
            text = f"{location_text} {msg.message}"

        # Print with style
        self.console.print(text, style=style)

        # Print extended message if present
        if msg.extended_message:
            self.console.print(f"  {msg.extended_message}", style="dim")

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

        # Format message
        if msg.source:
            text = f"[{msg.level.upper()}] {msg.source}: {msg.message}"
        else:
            text = f"[{msg.level.upper()}] {msg.message}"

        self.console.print(text, style=style)

    async def _render_progress_start(self, msg: ProgressStart):
        """Start a progress task"""
        if not self.show_progress:
            return

        # Start progress display if not already started
        if not self._progress_started:
            self.progress.start()
            self._progress_started = True

        # Add task to progress
        rich_task_id = self.progress.add_task(
            msg.description,
            total=msg.total
        )
        self._progress_tasks[msg.task_id] = rich_task_id

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

    async def _render_progress_end(self, msg: ProgressEnd):
        """Complete progress task"""
        if not self.show_progress or msg.task_id not in self._progress_tasks:
            return

        rich_task_id = self._progress_tasks[msg.task_id]

        # Mark as complete
        task = self.progress.tasks[rich_task_id]
        if task.total:
            self.progress.update(rich_task_id, completed=task.total)

        # Update description with status
        if not msg.success and msg.message:
            self.progress.update(
                rich_task_id,
                description=f"[red]✗ {task.description}: {msg.message}[/red]"
            )
        elif not msg.success:
            self.progress.update(
                rich_task_id,
                description=f"[red]✗ {task.description}[/red]"
            )
        else:
            self.progress.update(
                rich_task_id,
                description=f"[green]✓ {task.description}[/green]"
            )

        # Remove from tracking
        del self._progress_tasks[msg.task_id]

    async def _render_build_status(self, msg: BuildStatus):
        """Render build status with colors"""
        # Choose style based on status
        status_styles = {
            "started": "blue",
            "success": "green",
            "failure": "red",
            "error": "bold red",
            "skipped": "dim",
        }
        style = status_styles.get(msg.status, "")

        # Format status
        parts = [f"{msg.target}: {msg.status}"]
        if msg.duration is not None:
            parts.append(f"({msg.duration:.1f}s)")
        if msg.message:
            parts.append(f"- {msg.message}")

        self.console.print(" ".join(parts), style=style)
