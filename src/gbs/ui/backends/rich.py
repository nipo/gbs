"""Rich backend for fancy terminal output

Provides colorized output, live progress bars, and styled formatting
using the Rich library. Automatically falls back gracefully if Rich
is not available.
"""

from __future__ import annotations
import sys
import time
import math
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


class AptStyleProgressColumn:
    """Apt-style progress column - full width with inverted colors and gradient animation"""

    def __init__(self, console_width: int = 80, color: str = "cyan", transient: bool = False):
        """Initialize apt-style progress column

        Args:
            console_width: Width of the terminal
            color: Base color for the progress bar
            transient: Whether this progress bar is transient
        """
        from rich.progress import ProgressColumn, Task
        from rich.text import Text

        self.console_width = console_width
        self.color = color
        self.transient = transient
        self.start_time = time.time()

    def __call__(self, task) -> "Text":
        """Render the progress column"""
        from rich.text import Text

        # Calculate how many characters should be inverted
        if task.total:
            completed_width = int((task.completed / task.total) * self.console_width)
        else:
            completed_width = 0

        # Create the full line of text (truncate description to fit)
        percentage = f"{int(task.percentage):>3}%" if task.total else "---"
        max_desc_len = self.console_width - len(percentage) - 2  # -2 for spaces
        description = task.description[:max_desc_len] if len(task.description) > max_desc_len else task.description
        full_text = f" {percentage} {description}"

        # Pad to full console width
        full_text = full_text.ljust(self.console_width)

        # For WIP animation: create a subtle gradient near the progress edge
        # Use a sine wave based on time for smooth animation
        elapsed = time.time() - self.start_time
        wave_offset = int(math.sin(elapsed * 3) * 2)  # Oscillate ±2 chars

        # Create the result with inverted styling
        result = Text()

        # Completed portion - inverted (color on white)
        if completed_width > 0:
            # Apply gradient effect near the edge (last few chars)
            gradient_start = max(0, completed_width - 6)

            # Solid completed portion
            if gradient_start > 0:
                result.append(full_text[:gradient_start], style=f"black on {self.color}")

            # Gradient portion (slight variations in brightness)
            for i in range(gradient_start, completed_width):
                char_pos = i - gradient_start
                # Create pulsing effect
                if abs((i + wave_offset) - completed_width) < 2 and task.completed < task.total:
                    # Near edge - use brighter variant
                    result.append(full_text[i], style=f"black on bright_{self.color}")
                else:
                    result.append(full_text[i], style=f"black on {self.color}")

        # Remaining portion - normal (color on black)
        if completed_width < self.console_width:
            remaining_text = full_text[completed_width:]
            result.append(remaining_text, style=f"{self.color} on black")

        return result


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
        from rich.progress import Progress
        from rich.progress import ProgressColumn

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

        # Get console width for apt-style progress
        self.console_width = self.console.width

        # Create progress display with apt-style rendering
        # We'll create a custom ProgressColumn that uses our AptStyleProgressColumn
        class AptProgressColumnWrapper(ProgressColumn):
            """Wrapper to use AptStyleProgressColumn in Rich Progress"""
            def __init__(self, console_width: int, color: str = "cyan"):
                super().__init__()
                self.apt_renderer = None  # Will be set per task
                self.console_width = console_width
                self.default_color = color
                self.task_colors = {}  # task_id -> AptStyleProgressColumn instance

            def render(self, task):
                if task.id not in self.task_colors:
                    # Get color from task fields if available
                    color = self.default_color
                    if hasattr(task, "fields") and "color" in task.fields:
                        color = task.fields["color"]

                    self.task_colors[task.id] = AptStyleProgressColumn(
                        console_width=self.console_width,
                        color=color
                    )
                return self.task_colors[task.id](task)

        self.progress = Progress(
            AptProgressColumnWrapper(console_width=self.console_width, color="cyan"),
            console=self.console,
            transient=False,  # Don't auto-remove - we'll manage transient tasks manually
            expand=True  # Take full terminal width
        )

        # Track active progress tasks
        self._progress_tasks: Dict[str, int] = {}  # task_id -> rich task_id
        self._progress_transient: Dict[str, bool] = {}  # task_id -> is_transient
        self._progress_colors: Dict[str, str] = {}  # task_id -> color
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

        # Choose color based on whether it's transient (task) or not (BuildContext)
        # BuildContext: cyan, Tasks: blue
        color = "blue" if msg.transient else "cyan"

        # Description is not truncated - apt-style uses full terminal width
        # Add task to progress with color field
        rich_task_id = self.progress.add_task(
            msg.description,
            total=msg.total,
            color=color
        )
        self._progress_tasks[msg.task_id] = rich_task_id
        self._progress_transient[msg.task_id] = msg.transient
        self._progress_colors[msg.task_id] = color

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
            # No truncation - apt-style uses full terminal width
            self.progress.update(rich_task_id, description=msg.message)

        # Sort tasks so most advanced ones are on top
        self._sort_progress_tasks()

    async def _render_progress_end(self, msg: ProgressEnd):
        """Complete progress task"""
        if not self.show_progress or msg.task_id not in self._progress_tasks:
            return

        rich_task_id = self._progress_tasks[msg.task_id]
        is_transient = self._progress_transient.get(msg.task_id, False)

        # Get task info before marking complete
        try:
            task = self.progress.tasks[rich_task_id]
            task_description = task.description
        except (IndexError, KeyError):
            # Task may have been removed already, clean up tracking and return
            if msg.task_id in self._progress_tasks:
                del self._progress_tasks[msg.task_id]
            if msg.task_id in self._progress_transient:
                del self._progress_transient[msg.task_id]
            return

        # Mark as complete
        try:
            if task.total:
                self.progress.update(rich_task_id, completed=task.total)
        except (IndexError, KeyError):
            pass  # Task removed during update

        # For transient tasks, remove them and print completion message
        if is_transient:
            # Remove the progress bar
            try:
                self.progress.remove_task(rich_task_id)
            except (IndexError, KeyError):
                # Task may have already been removed
                pass

            # Print completion message to console (only for failures or if message provided)
            if not msg.success and msg.message:
                # No truncation needed - apt-style uses full width
                self.console.print(f"[red][FAILED] {task_description}: {msg.message}[/red]")
            elif not msg.success:
                self.console.print(f"[red][FAILED] {task_description}[/red]")
            # Don't print success message for transient tasks - they just disappear
        else:
            # For non-transient tasks, keep the bar visible
            # (task already updates description with "complete" or "failed" status)
            pass

        # Remove from tracking
        if msg.task_id in self._progress_tasks:
            del self._progress_tasks[msg.task_id]
        if msg.task_id in self._progress_transient:
            del self._progress_transient[msg.task_id]

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
