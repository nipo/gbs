"""GBS Model Package

This package contains the core model classes for the GBS build system:
- build: Build system classes (BuildContext, BuildFileSet, Task, Resource, etc.)
- repository: Repository data models (Repository, Library, Partition, etc.)
- backend: Backend base classes and registry
"""

# Re-export everything from build
from .build import (
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

# Re-export everything from repository
from .repository import (
    SourceFile,
    FilterCondition,
    ConditionalGroup,
    Partition,
    Library,
    Repository,
    Project,
    # Note: BuildFileSet is also defined in repository but we prefer the one from build
)

# Re-export everything from backend
from .backend import (
    Backend,
    BaseBackend,
    BackendRegistry,
    run_backend_iteration,
)

__all__ = [
    # build module
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
    # repository module
    "SourceFile",
    "FilterCondition",
    "ConditionalGroup",
    "Partition",
    "Library",
    "Repository",
    "Project",
    # backend module
    "Backend",
    "BaseBackend",
    "BackendRegistry",
    "run_backend_iteration",
]
