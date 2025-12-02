"""GBS Plugin System

Provides plugin discovery for default tool configurations.
Plugins can contribute default tool configurations via the register() function.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ToolConfig

from .logging import get_logger

logger = get_logger(__name__)


class PluginRegistry:
    """Global plugin registry

    Discovers plugins and collects default tool configurations.
    Discovery is lazy and cached at module level.
    """

    def __init__(self):
        self._default_tools: list['ToolConfig'] = []
        self._discovered = False

    def contribute_tool_defaults(self, tools: list['ToolConfig']):
        """Allow plugin to contribute default tool configs

        Args:
            tools: List of default tool configurations
        """
        logger.debug(f"Plugin contributing {len(tools)} default tool configs")
        self._default_tools.extend(tools)

    def get_default_tools(self) -> list['ToolConfig']:
        """Get all default tools contributed by plugins

        Ensures plugins are discovered first.

        Returns:
            List of default tool configurations
        """
        self.discover_plugins()
        return list(self._default_tools)

    def discover_plugins(self):
        """Discover plugins via entry points

        Called lazily on first access. Cached thereafter.
        Discovers from:
        - Built-in backends (gbs.backend package)

        Each plugin module can optionally provide a register(registry)
        function to contribute default tool configurations.
        """
        if self._discovered:
            return

        logger.debug("Discovering plugins...")

        # Register built-in backends first
        try:
            from . import backend
            if hasattr(backend, 'register'):
                backend.register(self)
                logger.debug("Registered built-in backends")
        except Exception as e:
            logger.warning(f"Failed to register built-in backends: {e}")

        self._discovered = True
        logger.debug(f"Plugin discovery complete: {len(self._default_tools)} default tools")


# Module-level singleton (lazy, cached)
_plugin_registry: PluginRegistry | None = None


def get_plugin_registry() -> PluginRegistry:
    """Get the global plugin registry singleton

    Registry is created once per Python runtime.
    Plugin discovery happens lazily on first use.

    Returns:
        Global PluginRegistry instance
    """
    global _plugin_registry
    if _plugin_registry is None:
        _plugin_registry = PluginRegistry()
    return _plugin_registry
