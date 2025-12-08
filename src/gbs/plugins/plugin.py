"""Plugin base class for GBS extensibility

The Plugin system provides a unified way to extend GBS with:
- Passes (for build planning)
- Backends (for execution)
- Repository parsers (for loading source definitions)

Plugins are discovered via:
1. Built-in plugins (pkgutil.iter_modules)
2. External plugins (PEP 420 namespace packages under gbs.plugin)

Each plugin module must define a `gbs_register()` function that returns
one or more Plugin instances.
"""

from __future__ import annotations
from abc import ABC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..backend.protocol import Backend
    # from ..repository.parser import RepositoryParser  # TODO: when parsers exist


class Plugin(ABC):
    """Base class for GBS plugins

    Plugins act as factories for pluggable components. A plugin can provide:
    - Passes: Planning metadata for build transformations
    - Backends: Execution engines that create dispatchers
    - Repository Parsers: Loaders for different source definition formats

    Attributes:
        name: Unique plugin identifier (e.g., "gbs.builtin.ghdl")
        description: Human-readable description
        version: Plugin version string
    """

    def __init__(self, name: str, description: str = "", version: str = "1.0.0"):
        """Initialize plugin

        Args:
            name: Unique plugin name (e.g., "gbs.builtin.ghdl")
            description: Plugin description
            version: Version string
        """
        self.name = name
        self.description = description
        self.version = version

    def enumerate_backends(self) -> list['Backend']:
        """Enumerate backend instances provided by this plugin

        Backends provide build planning (passes) and execution (dispatchers).

        Returns:
            List of Backend instances

        Example:
            >>> def enumerate_backends(self):
            ...     from .backend import GHDLBackend
            ...     return [GHDLBackend()]
        """
        return []

    def generic_dispatchers(self, context: 'BuildContext') -> list['Dispatcher']:
        """Enumerate generic dispatchers that can provide useful
        behavior with any backend

        Args:
            context: Build context to pass to dispatcher constructors

        Returns:
            List of Dispatcher instances
        """
        return []

    def enumerate_repository_parsers(self) -> dict[str, type['RepositoryLoader']]:
        """Enumerate repository parser classes provided by this plugin

        Repository parsers load source definitions from various formats
        (YAML, TOML, JSON, custom formats). Returns a dict mapping loader
        names to RepositoryLoader classes (not instances).

        The classes will be instantiated with a Path argument when needed.

        Returns:
            Dict mapping loader name to RepositoryLoader class

        Example:
            >>> def enumerate_repository_parsers(self):
            ...     from .repository import TreeLoader
            ...     return {"nsl-tree": TreeLoader}
        """
        return {}

    def __repr__(self) -> str:
        return f"Plugin(name={self.name!r}, version={self.version})"

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"


__all__ = ["Plugin"]
