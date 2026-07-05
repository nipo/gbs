"""Base Plugin Implementation

Abstract base class for plugins. Subclass this to create new plugins.
"""

from __future__ import annotations
from abc import ABC
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocol import Backend, Dispatcher, ToolchainProvider
    from ..build.context import BuildContext
    from ..plugins.loader import RepositoryLoader

__all__ = ["BasePlugin"]


class BasePlugin(ABC):
    """Base class for GBS plugins

    Plugins act as factories for pluggable components. A plugin can provide:
    - Backends: Planning and execution engines
    - Generic Dispatchers: Useful behavior with any backend
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

    def enumerate_backends(self) -> list[Backend]:
        """Enumerate backend instances provided by this plugin

        Backends provide build planning (passes) and execution (dispatchers).

        Default implementation returns empty list. Override to provide backends.

        Returns:
            List of Backend instances
        """
        return []

    def generic_dispatchers(self, context: BuildContext) -> list[Dispatcher]:
        """Enumerate generic dispatchers that can provide useful
        behavior with any backend

        Default implementation returns empty list. Override to provide dispatchers.

        Args:
            context: Build context to pass to dispatcher constructors

        Returns:
            List of Dispatcher instances
        """
        return []

    def enumerate_repository_parsers(self) -> dict[str, type[RepositoryLoader]]:
        """Enumerate repository parser classes provided by this plugin

        Repository parsers load source definitions from various formats
        (YAML, TOML, JSON, custom formats). Returns a dict mapping loader
        names to RepositoryLoader classes (not instances).

        The classes will be instantiated with a Path argument when needed.

        Default implementation returns empty dict. Override to provide parsers.

        Returns:
            Dict mapping loader name to RepositoryLoader class
        """
        return {}

    def enumerate_toolchain_providers(self) -> dict[str, type[ToolchainProvider]]:
        """Enumerate toolchain provider classes provided by this plugin

        Toolchain providers expand a single `toolchains:` config entry
        into multiple ToolConfig entries. Returns a dict mapping the
        `type:` key (as used in config) to a ToolchainProvider class.

        Default implementation returns empty dict. Override to provide providers.

        Returns:
            Dict mapping toolchain type name to ToolchainProvider class
        """
        return {}

    def transform_filter_vars(
        self,
        filter_vars: dict[str, Any],
    ) -> dict[str, Any]:
        """Contribute extra filter variables synthesised from the
        canonical set.

        Called once per build after per-pass filter_vars have been
        unioned into a single flat dictionary. Returned keys are
        merged into the environment used by repository loaders, but
        never overwrite variables already present in the canonical
        set.

        Default implementation returns an empty dict. Override to
        contribute legacy aliases.

        Args:
            filter_vars: The current merged filter environment.

        Returns:
            Extra variables to merge in.
        """
        return {}

    def __repr__(self) -> str:
        return f"Plugin(name={self.name!r}, version={self.version})"

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"
