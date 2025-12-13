"""File backend for structured logging

Renders all messages to a structured log file with stable output format.
This backend is designed to be used alongside terminal backends to provide
a complete record of the build process.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Optional, TextIO
from datetime import datetime

from .base import FeedbackBackend
from ..messages import (
    ToolMessage, LogMessage, ProgressStart, ProgressUpdate,
    ProgressEnd, BuildStatus, MessageSeverity, LogLevel
)

__all__ = ["FileBackend"]


class FileBackend(FeedbackBackend):
    """Structured file logging backend

    Writes all messages to a log file in a stable, parseable format.
    Output is designed to be:
    - Stable across runs (no terminal control codes, no animations)
    - Complete (includes all message metadata)
    - Parseable (structured format)
    - Human-readable (while still being structured)

    Can output in two formats:
    - 'text': Human-readable structured text (default)
    - 'json': JSON Lines format (one JSON object per line)
    """

    def __init__(
        self,
        file_path: Path | str,
        format: str = 'text',
        min_severity: MessageSeverity = MessageSeverity.DEBUG,
        min_log_level: LogLevel = LogLevel.DEBUG,
        include_progress: bool = True
    ):
        """Initialize file backend

        Args:
            file_path: Path to log file
            format: Output format ('text' or 'json')
            min_severity: Minimum severity for ToolMessages to log
            min_log_level: Minimum level for LogMessages to log
            include_progress: Whether to include progress messages
        """
        self.file_path = Path(file_path)
        self.format = format
        self.min_severity = min_severity
        self.min_log_level = min_log_level
        self.include_progress = include_progress
        self._file: Optional[TextIO] = None

        # Track progress state for text format
        self._active_tasks: dict[str, dict] = {}  # task_id -> task info

    async def start(self):
        """Open log file"""
        # Create parent directory if needed
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

        # Open file in append mode with UTF-8 encoding
        self._file = open(self.file_path, 'a', encoding='utf-8')

        # Write header
        if self.format == 'json':
            self._write_json({
                'type': 'session_start',
                'timestamp': datetime.now().isoformat(),
                'log_file': str(self.file_path)
            })
        else:
            self._write_line(f"{'=' * 80}")
            self._write_line(f"GBS Build Log - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self._write_line(f"{'=' * 80}")

    async def stop(self):
        """Close log file"""
        if self._file:
            # Write footer
            if self.format == 'json':
                self._write_json({
                    'type': 'session_end',
                    'timestamp': datetime.now().isoformat()
                })
            else:
                self._write_line(f"{'=' * 80}")
                self._write_line(f"Session ended - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                self._write_line(f"{'=' * 80}")

            self._file.close()
            self._file = None

    def _write_line(self, text: str):
        """Write a line to the log file"""
        if self._file:
            self._file.write(text + '\n')
            self._file.flush()

    def _write_json(self, obj: dict):
        """Write a JSON object to the log file"""
        if self._file:
            self._file.write(json.dumps(obj) + '\n')
            self._file.flush()

    async def render(self, msg):
        """Render a message to the log file

        Args:
            msg: Message to render
        """
        if not self._file:
            return

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
            # Unknown message type - log it anyway
            if self.format == 'json':
                self._write_json({
                    'type': 'unknown',
                    'timestamp': datetime.now().isoformat(),
                    'message': str(msg)
                })
            else:
                self._write_line(f"[UNKNOWN] {msg}")

    async def _render_tool_message(self, msg: ToolMessage):
        """Render a tool message"""
        # Filter based on minimum severity
        if msg.severity < self.min_severity:
            return

        if self.format == 'json':
            obj = {
                'type': 'tool_message',
                'timestamp': msg.timestamp.isoformat(),
                'severity': msg.severity.value,
                'message': msg.message,
            }
            if msg.file_path:
                obj['file'] = str(msg.file_path)
            if msg.line is not None:
                obj['line'] = msg.line
            if msg.column is not None:
                obj['column'] = msg.column
            if msg.identifier:
                obj['identifier'] = msg.identifier
            if msg.extended_message:
                obj['extended_message'] = msg.extended_message
            self._write_json(obj)
        else:
            # Text format: compiler-style output
            if msg.file_path:
                # Compiler-style format: file:line:level: message
                # Use full word for emacs compilation-mode compatibility
                location = str(msg.file_path)
                if msg.line is not None:
                    location += f":{msg.line}"
                    if msg.column is not None:
                        location += f":{msg.column}"
                location_prefix = f"{location}:{msg.severity.value}: "
            else:
                # Short bracketed form for non-file messages
                severity_code = msg.severity.short_code()
                location_prefix = f"[{severity_code}]: "

            identifier_str = f"({msg.identifier}) " if msg.identifier else ""

            self._write_line(f"{location_prefix}{identifier_str}{msg.message}")
            if msg.extended_message:
                # Indent extended message
                for line in msg.extended_message.split('\n'):
                    self._write_line(f"  {line}")

    async def _render_log_message(self, msg: LogMessage):
        """Render a log message"""
        # Filter based on minimum log level
        if msg.level < self.min_log_level:
            return

        if self.format == 'json':
            obj = {
                'type': 'log',
                'timestamp': msg.timestamp.isoformat(),
                'level': msg.level.value,
                'message': msg.message,
            }
            if msg.source:
                obj['source'] = msg.source
            self._write_json(obj)
        else:
            source_str = f"{msg.source}: " if msg.source else ""
            level_code = msg.level.short_code()
            self._write_line(f"[{level_code}] {source_str}{msg.message}")

    async def _render_progress_start(self, msg: ProgressStart):
        """Render progress start"""
        if not self.include_progress:
            return

        # Track this task
        self._active_tasks[msg.task_id] = {
            'description': msg.description,
            'total': msg.total,
            'started': datetime.now(),
            'transient': msg.transient
        }

        if self.format == 'json':
            self._write_json({
                'type': 'progress_start',
                'timestamp': msg.timestamp.isoformat(),
                'task_id': msg.task_id,
                'description': msg.description,
                'total': msg.total,
                'transient': msg.transient
            })
        else:
            total_str = f"/{msg.total}" if msg.total else ""
            transient_str = " (transient)" if msg.transient else ""
            self._write_line(f"> {msg.description}{total_str}{transient_str}")

    async def _render_progress_update(self, msg: ProgressUpdate):
        """Render progress update"""
        if not self.include_progress:
            return

        # Update task state
        if msg.task_id in self._active_tasks:
            if msg.completed is not None:
                self._active_tasks[msg.task_id]['completed'] = msg.completed
            if msg.message:
                self._active_tasks[msg.task_id]['current_message'] = msg.message

        if self.format == 'json':
            obj = {
                'type': 'progress_update',
                'timestamp': datetime.now().isoformat(),
                'task_id': msg.task_id,
            }
            if msg.completed is not None:
                obj['completed'] = msg.completed
            if msg.message:
                obj['message'] = msg.message
            self._write_json(obj)
        else:
            # Only log updates with messages to avoid spam
            if msg.message:
                completed_str = ""
                if msg.completed is not None and msg.task_id in self._active_tasks:
                    task = self._active_tasks[msg.task_id]
                    if task.get('total'):
                        pct = int(msg.completed / task['total'] * 100)
                        completed_str = f"[{pct}%] "
                self._write_line(f"  {completed_str}{msg.message}")

    async def _render_progress_end(self, msg: ProgressEnd):
        """Render progress end"""
        if not self.include_progress:
            return

        # Get task info
        task_info = self._active_tasks.pop(msg.task_id, None)

        if self.format == 'json':
            obj = {
                'type': 'progress_end',
                'timestamp': datetime.now().isoformat(),
                'task_id': msg.task_id,
                'success': msg.success,
            }
            if msg.message:
                obj['message'] = msg.message
            if task_info:
                duration = (datetime.now() - task_info['started']).total_seconds()
                obj['duration_seconds'] = duration
            self._write_json(obj)
        else:
            status = "[OK]" if msg.success else "[FAILED]"
            message_str = f": {msg.message}" if msg.message else ""
            duration_str = ""

            if task_info:
                duration = (datetime.now() - task_info['started']).total_seconds()
                duration_str = f" ({duration:.1f}s)"
                desc = task_info['description']
            else:
                desc = msg.task_id

            self._write_line(f"{status} {desc}{duration_str}{message_str}")

    async def _render_build_status(self, msg: BuildStatus):
        """Render build status"""
        if self.format == 'json':
            obj = {
                'type': 'build_status',
                'timestamp': msg.timestamp.isoformat(),
                'target': msg.target,
                'status': msg.status,
            }
            if msg.duration is not None:
                obj['duration_seconds'] = msg.duration
            if msg.message:
                obj['message'] = msg.message
            self._write_json(obj)
        else:
            duration_str = f" ({msg.duration:.1f}s)" if msg.duration is not None else ""
            message_str = f" - {msg.message}" if msg.message else ""
            self._write_line(f"[BUILD] {msg.target}: {msg.status}{duration_str}{message_str}")
