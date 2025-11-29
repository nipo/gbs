"""GBS Plugin System

Provides lazy plugin discovery and registration for:
- Backend plugins (gbs.backends entry point group)
- Repository loader plugins (gbs.loaders entry point group)

Plugins can contribute default tool configurations.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gbs.config import ToolConfig

from gbs.logging import get_logger

logger = get_logger(__name__)


class PluginRegistry:
    """Global plugin registry

    Discovers and registers plugins via entry points.
    Discovery is lazy and cached at module level.
    """

    def __init__(self):
        self._backend_plugins: dict[str, type] = {}
        self._loader_plugins: dict[str, type] = {}
        self._default_tools: list['ToolConfig'] = []
        self._auto_backend_providers: list[callable] = []
        self._discovered = False

    def register_backend(self, name: str, backend_class: type):
        """Register a backend plugin

        Args:
            name: Backend name
            backend_class: Backend class
        """
        logger.debug(f"Registering backend plugin: {name}")
        self._backend_plugins[name] = backend_class

    def register_loader(self, name: str, loader_class: type):
        """Register a repository loader plugin

        Args:
            name: Loader name
            loader_class: Loader class
        """
        logger.debug(f"Registering loader plugin: {name}")
        self._loader_plugins[name] = loader_class

    def contribute_tool_defaults(self, tools: list['ToolConfig']):
        """Allow plugin to contribute default tool configs

        Args:
            tools: List of default tool configurations
        """
        logger.debug(f"Plugin contributing {len(tools)} default tool configs")
        self._default_tools.extend(tools)

    def register_auto_backend_provider(self, provider_func: callable):
        """Register a function that provides auto-included backends

        The function will be called during backend loading with the BackendRegistry
        as an argument, allowing it to inspect what backends are loaded and
        conditionally add additional backends.

        Args:
            provider_func: Function(registry: BackendRegistry) -> list[Backend]
                          Should return list of backend instances to auto-include
        """
        logger.debug(f"Registering auto-backend provider: {provider_func.__name__}")
        self._auto_backend_providers.append(provider_func)

    def get_auto_backends(self, registry) -> list:
        """Get all auto-include backends based on current registry state

        Args:
            registry: Current BackendRegistry with explicitly configured backends

        Returns:
            List of backend instances that should be auto-included
        """
        self.discover_plugins()
        auto_backends = []

        for provider in self._auto_backend_providers:
            try:
                backends = provider(registry)
                if backends:
                    auto_backends.extend(backends)
                    logger.debug(f"Auto-backend provider {provider.__name__} contributed {len(backends)} backends")
            except Exception as e:
                logger.warning(f"Auto-backend provider {provider.__name__} failed: {e}")

        return auto_backends

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
        - gbs.backends entry point group
        - gbs.loaders entry point group

        Each plugin module can optionally provide a register(registry)
        function to register itself and contribute defaults.
        """
        if self._discovered:
            return

        logger.debug("Discovering plugins...")

        # Register built-in backends first
        try:
            import gbs.backend
            if hasattr(gbs.backend, 'register'):
                gbs.backend.register(self)
                logger.debug("Registered built-in backends")
        except Exception as e:
            logger.warning(f"Failed to register built-in backends: {e}")

        try:
            import importlib.metadata as metadata
        except ImportError:
            # Python < 3.8
            import importlib_metadata as metadata

        # Discover gbs.backends entry points
        try:
            backend_eps = metadata.entry_points(group='gbs.backends')
        except TypeError:
            # Python 3.9 compatibility
            backend_eps = metadata.entry_points().get('gbs.backends', [])

        for ep in backend_eps:
            try:
                plugin_module = ep.load()
                # If module has register() function, call it
                if hasattr(plugin_module, 'register'):
                    plugin_module.register(self)
                logger.debug(f"Loaded backend plugin: {ep.name}")
            except Exception as e:
                logger.warning(f"Failed to load backend plugin {ep.name}: {e}")

        # Discover gbs.loaders entry points
        try:
            loader_eps = metadata.entry_points(group='gbs.loaders')
        except TypeError:
            # Python 3.9 compatibility
            loader_eps = metadata.entry_points().get('gbs.loaders', [])

        for ep in loader_eps:
            try:
                plugin_module = ep.load()
                if hasattr(plugin_module, 'register'):
                    plugin_module.register(self)
                logger.debug(f"Loaded loader plugin: {ep.name}")
            except Exception as e:
                logger.warning(f"Failed to load loader plugin {ep.name}: {e}")

        self._discovered = True
        logger.debug(f"Plugin discovery complete: {len(self._backend_plugins)} backends, "
                    f"{len(self._loader_plugins)} loaders, "
                    f"{len(self._default_tools)} default tools")


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
