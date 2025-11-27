"""AsyncIO-Native Task System for GBS - Compatibility Shim

This module provides backward compatibility by re-exporting from gbs.model.build.
All new code should import from gbs.model.build directly.
"""

from __future__ import annotations

# Re-export everything from gbs.model.build
from gbs.model.build import (
    BuildError,
    PrerequisiteFailed,
    BuildContext,
    ContextBuildManager,
    BuildStep,
    VirtualResource,
    Resource,
    Task,
    TaskExecutor,
    ExecutorTask,
    BuildResource,
    BuildFileSet,
)

__all__ = [
    "BuildError",
    "PrerequisiteFailed",
    "BuildContext",
    "ContextBuildManager",
    "BuildStep",
    "VirtualResource",
    "Resource",
    "Task",
    "TaskExecutor",
    "ExecutorTask",
    "BuildResource",
    "BuildFileSet",
]
