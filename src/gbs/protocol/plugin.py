"""Plugin Protocol

Pure protocol definition for plugins that extend GBS functionality.
This is used for type checking only - implementations should inherit from
gbs.base.BasePlugin instead.
"""

from __future__ import annotations
from typing import Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from .backend import Backend
    from .dispatcher import Dispatcher
    from ..build.context import BuildContext
    from ..plugins.loader import RepositoryLoader

__all__ = ["Plugin"]


@runtime_checkable
class Plugin(Protocol):
    """Protocol for GBS plugins

    Plugins act as factories for pluggable components. A plugin can provide:
    - Backends: Planning and execution engines
    - Generic Dispatchers: Useful behavior with any backend
    - Repository Parsers: Loaders for different source definition formats

    Attributes:
        name: Unique plugin identifier (e.g., "gbs.builtin.ghdl")
        description: Human-readable description
        version: Plugin version string
    """

    name: str
    description: str
    version: str

    def enumerate_backends(self) -> list[Backend]:
        """Enumerate backend instances provided by this plugin

        Backends provide build planning (passes) and execution (dispatchers).

        Returns:
            List of Backend instances
        """
        ...

    def generic_dispatchers(self, context: BuildContext) -> list[Dispatcher]:
        """Enumerate generic dispatchers that can provide useful
        behavior with any backend

        Args:
            context: Build context to pass to dispatcher constructors

        Returns:
            List of Dispatcher instances
        """
        ...

    def enumerate_repository_parsers(self) -> dict[str, type[RepositoryLoader]]:
        """Enumerate repository parser classes provided by this plugin

        Repository parsers load source definitions from various formats
        (YAML, TOML, JSON, custom formats). Returns a dict mapping loader
        names to RepositoryLoader classes (not instances).

        The classes will be instantiated with a Path argument when needed.

        Returns:
            Dict mapping loader name to RepositoryLoader class
        """
        ...
