"""GBS User Interface Module

Centralized system for all user-facing output.
"""

from .messages import (
    MessageSeverity,
    LogLevel,
    ToolMessage,
    LogMessage,
    ProgressStart,
    ProgressUpdate,
    ProgressEnd,
    BuildStatus,
)

from .hub import (
    FeedbackHub,
    NullHub,
    ProgressTracker,
    set_global_hub,
    get_global_hub,
    emit,
)

from .backends import (
    FeedbackBackend,
    SimpleBackend,
)

# Conditionally import RichBackend if available
try:
    from .backends import RichBackend, is_rich_available
    _has_rich = True
except ImportError:
    _has_rich = False

__all__ = [
    "MessageSeverity",
    "LogLevel",
    "ToolMessage",
    "LogMessage",
    "ProgressStart",
    "ProgressUpdate",
    "ProgressEnd",
    "BuildStatus",
    "FeedbackHub",
    "NullHub",
    "ProgressTracker",
    "set_global_hub",
    "get_global_hub",
    "emit",
    "FeedbackBackend",
    "SimpleBackend",
]

if _has_rich:
    __all__.extend(["RichBackend", "is_rich_available"])
