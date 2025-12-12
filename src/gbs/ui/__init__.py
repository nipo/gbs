"""GBS User Interface Module

Centralized system for all user-facing output.
"""

from .messages import (
    MessageSeverity,
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

__all__ = [
    "MessageSeverity",
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
