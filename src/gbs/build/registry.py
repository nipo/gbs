"""Shared Resource Registry

Provides a singleton registry for Resources across multiple BuildContexts.
When multiple output groups share a registry, a Resource at a given resolved
path is the same Python object regardless of which BuildContext creates or
references it. This enables cross-output-group dependencies: if group A
produces a file and group B consumes it, both see the same Resource future.
"""

from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .task import Resource

__all__ = ["ResourceRegistry"]


class ResourceRegistry:
    """Shared singleton registry for Resources across BuildContexts.

    Ensures that a Resource at a given resolved path is the same
    Python object regardless of which BuildContext creates or
    references it. This is the foundation for cross-output-group
    dependency tracking.
    """

    def __init__(self):
        self._resources: dict[Path, Resource] = {}

    def get(self, path: Path) -> 'Resource | None':
        """Get existing Resource by resolved path, or None."""
        return self._resources.get(path.resolve())

    def register(self, path: Path, resource: 'Resource') -> None:
        """Register a Resource at a resolved path."""
        self._resources[path.resolve()] = resource

    def __contains__(self, path: Path) -> bool:
        return path.resolve() in self._resources

    def __len__(self) -> int:
        return len(self._resources)
