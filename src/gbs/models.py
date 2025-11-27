"""GBS Core Data Models - Compatibility Shim

This module provides backward compatibility by re-exporting from gbs.model.repository.
All new code should import from gbs.model.repository directly.
"""

from __future__ import annotations

# Re-export everything from gbs.model.repository
from gbs.model.repository import (
    Language,
    SourceFile,
    FilterCondition,
    ConditionalGroup,
    Partition,
    Library,
    Repository,
    ToolsuiteConfig,
    Project,
    BuildFileSet,
)

__all__ = [
    "Language",
    "SourceFile",
    "FilterCondition",
    "ConditionalGroup",
    "Partition",
    "Library",
    "Repository",
    "ToolsuiteConfig",
    "Project",
    "BuildFileSet",
]
