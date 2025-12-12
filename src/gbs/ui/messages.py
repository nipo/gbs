"""User feedback message types for GBS

All user-facing output (logs, errors, progress, tool messages) flows through
these message types to a central FeedbackHub for rendering.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Any
from datetime import datetime

__all__ = [
    "MessageSeverity",
    "LogLevel",
    "ToolMessage",
    "LogMessage",
    "ProgressStart",
    "ProgressUpdate",
    "ProgressEnd",
    "BuildStatus",
]


class MessageSeverity(Enum):
    """Severity levels for tool messages"""
    DEBUG = "debug"
    NOTICE = "notice"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"

    def __str__(self) -> str:
        return self.value

    def __lt__(self, other):
        """Allow severity comparison"""
        if not isinstance(other, MessageSeverity):
            return NotImplemented
        order = [
            MessageSeverity.DEBUG,
            MessageSeverity.NOTICE,
            MessageSeverity.INFO,
            MessageSeverity.WARNING,
            MessageSeverity.ERROR,
            MessageSeverity.FATAL,
        ]
        return order.index(self) < order.index(other)

    def __le__(self, other):
        """Allow severity comparison"""
        if not isinstance(other, MessageSeverity):
            return NotImplemented
        return self < other or self == other

    def __gt__(self, other):
        """Allow severity comparison"""
        if not isinstance(other, MessageSeverity):
            return NotImplemented
        return not self <= other

    def __ge__(self, other):
        """Allow severity comparison"""
        if not isinstance(other, MessageSeverity):
            return NotImplemented
        return not self < other


class LogLevel(Enum):
    """Log levels for general logging messages"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    def __str__(self) -> str:
        return self.value

    def __lt__(self, other):
        """Allow log level comparison"""
        if not isinstance(other, LogLevel):
            return NotImplemented
        order = [
            LogLevel.DEBUG,
            LogLevel.INFO,
            LogLevel.WARNING,
            LogLevel.ERROR,
            LogLevel.CRITICAL,
        ]
        return order.index(self) < order.index(other)

    def __le__(self, other):
        """Allow log level comparison"""
        if not isinstance(other, LogLevel):
            return NotImplemented
        return self < other or self == other

    def __gt__(self, other):
        """Allow log level comparison"""
        if not isinstance(other, LogLevel):
            return NotImplemented
        return not (self <= other)

    def __ge__(self, other):
        """Allow log level comparison"""
        if not isinstance(other, LogLevel):
            return NotImplemented
        return not self < other


@dataclass
class ToolMessage:
    """Standardized message from EDA tools and backends

    Provides a homogeneous representation of messages from various tools,
    including errors, warnings, and informational messages.

    This is the compiler-like output format: file:line:column: severity: message
    """
    severity: MessageSeverity
    message: str
    identifier: Optional[str] = None
    extended_message: Optional[str] = None
    file_path: Optional[Path] = None
    line: Optional[int] = None
    column: Optional[int] = None
    origin: Optional[Any] = None  # BuildStep reference
    timestamp: datetime = field(default_factory=datetime.now)

    def __str__(self) -> str:
        """Format message for display"""
        if self.file_path:
            location = str(self.file_path)
            if self.line is not None:
                location += f":{self.line}"
                if self.column is not None:
                    location += f":{self.column}"
            parts = [f"{location}:{self.severity.value.upper()}:"]
        else:
            parts = [f"[{self.severity.value.upper()}]"]

        if self.identifier:
            parts.append(f"({self.identifier})")

        parts.append(self.message)

        result = " ".join(parts)

        if self.extended_message:
            result += "\n" + self.extended_message

        return result

    def pprint(self):
        """Print message to stdout (legacy compatibility)"""
        print(str(self))


@dataclass
class LogMessage:
    """Generic log message

    Used for general logging output that doesn't fit the tool message format.
    """
    level: LogLevel
    message: str
    source: Optional[str] = None  # Logger name or module
    timestamp: datetime = field(default_factory=datetime.now)

    def __str__(self) -> str:
        if self.source:
            return f"[{self.level.upper()}] {self.source}: {self.message}"
        return f"[{self.level.upper()}] {self.message}"


@dataclass
class ProgressStart:
    """Start a new progress task

    Used to indicate the beginning of a potentially long-running operation.
    Can be nested via parent_id.
    """
    task_id: str
    description: str
    total: Optional[int] = None  # None for indeterminate progress
    parent_id: Optional[str] = None  # For nested progress
    transient: bool = False  # If True, progress bar disappears when complete
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ProgressUpdate:
    """Update progress on an existing task"""
    task_id: str
    completed: Optional[int] = None  # Current completion count
    message: Optional[str] = None  # Optional status message
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ProgressEnd:
    """End a progress task"""
    task_id: str
    success: bool = True  # False if task failed
    message: Optional[str] = None  # Optional completion message
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class BuildStatus:
    """High-level build status message

    Used for suite/project-level status updates.
    """
    status: str  # "started", "success", "failure", "error", "skipped"
    target: str  # Project name, suite name, etc.
    message: Optional[str] = None
    duration: Optional[float] = None  # Seconds
    timestamp: datetime = field(default_factory=datetime.now)

    def __str__(self) -> str:
        parts = [f"{self.target}: {self.status}"]
        if self.duration is not None:
            parts.append(f"({self.duration:.1f}s)")
        if self.message:
            parts.append(f"- {self.message}")
        return " ".join(parts)
