"""GBS Core Data Models - Compatibility Shim

This module provides backward compatibility by re-exporting from gbs.model.repository.
All new code should import from gbs.model.repository directly.
"""

from __future__ import annotations

# Re-export everything from gbs.model.repository
from gbs.model.repository import (
    SourceFile,
    FilterCondition,
    ConditionalGroup,
    Partition,
    Library,
    Repository,
    Project,
    BuildFileSet,
)

__all__ = [
    "SourceFile",
    "FilterCondition",
    "ConditionalGroup",
    "Partition",
    "Library",
    "Repository",
    "Project",
    "BuildFileSet",
]
