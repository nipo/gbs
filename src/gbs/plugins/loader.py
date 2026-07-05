"""Plugin discovery and loading system

Discovers plugins from:
1. Built-in modules (gbs.builtin.*)
2. External namespace packages (gbs.plugin.*)

Each plugin module must define a `gbs_register()` function returning Plugin instances.
"""

from __future__ import annotations
import importlib
import pkgutil
import traceback
from typing import Optional
from pathlib import Path
from dataclasses import dataclass

from ..logging import get_logger
from ..protocol import Plugin, Backend, ToolchainProvider

logger = get_logger(__name__)


@dataclass
class BackendInfo:
    """Information about a registered backend

    Attributes:
        backend: The Backend instance
        module_path: Module path (e.g., "gbs.builtin.ghdl")
    """
    backend: Backend
    module_path: str


class PluginRegistry:
    """Registry for managing discovered plugins

    Discovers plugins from built-in modules and external namespace packages.
    The registry only collects Plugin instances - it doesn't care about what
    they provide (backends, parsers, etc.).
    """

    def __init__(self):
        """Initialize empty plugin registry"""
        self._plugins: dict[str, Plugin] = {}  # name -> Plugin

    def discover_plugins(self):
        """Discover and load all plugins

        Discovery order:
        1. Built-in plugins (gbs.builtin.*)
        2. External plugins (gbs.plugin namespace packages)
        """
        logger.info("Discovering plugins...")

        # Discover built-in plugins
        self._discover_builtins()

        # Discover external plugins via namespace packages
        self._discover_namespace_plugins()

        logger.info(f"Plugin discovery complete: {len(self._plugins)} plugins")

    def _discover_builtins(self):
        """Discover built-in plugins from gbs.builtin.*"""
        try:
            import gbs.builtin as builtin_pkg
        except ImportError:
            logger.warning("gbs.builtin package not found")
            return

        # Use pkgutil to enumerate modules in gbs.builtin
        builtin_path = Path(builtin_pkg.__file__).parent

        for module_info in pkgutil.iter_modules([str(builtin_path)]):
            if module_info.ispkg:
                module_name = f"gbs.builtin.{module_info.name}"
                self._load_plugin_module(module_name, is_builtin=True)

    def _discover_namespace_plugins(self):
        """Discover external plugins from gbs.plugin namespace"""
        try:
            # Import the namespace package
            import gbs.plugin as plugin_ns

            # Enumerate all modules in the namespace
            # PEP 420 namespace packages have __path__ attribute
            if hasattr(plugin_ns, '__path__'):
                for module_info in pkgutil.iter_modules(plugin_ns.__path__, prefix='gbs.plugin.'):
                    self._load_plugin_module(module_info.name, is_builtin=False)
        except ImportError:
            logger.debug("No external plugins found in gbs.plugin namespace")

    def _load_plugin_module(self, module_name: str, is_builtin: bool):
        """Load a plugin from a module

        Args:
            module_name: Full module name (e.g., "gbs.builtin.ghdl")
            is_builtin: True if built-in plugin, False if external
        """
        plugin_type = "built-in" if is_builtin else "external"
        logger.debug(f"Loading {plugin_type} plugin: {module_name}")

        # Import the module
        # NOTE: We allow ImportError to propagate for built-in plugins since they should always work
        # External plugins may genuinely be missing, so we handle that separately
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            if is_builtin:
                # Built-in plugins should always import - this is a real error
                raise
            else:
                # External plugin not installed - this is OK
                logger.debug(f"External plugin {module_name} not available (not installed)")
                return

        # Look for gbs_register function
        if not hasattr(module, 'gbs_register'):
            logger.warning(
                f"Plugin module {module_name} does not define gbs_register() function"
            )
            return

        register_func = getattr(module, 'gbs_register')

        # Call gbs_register() - let exceptions propagate
        # Plugin initialization errors must be visible, not silently suppressed
        result = register_func()

        # Handle single plugin or list of plugins
        plugins = result if isinstance(result, list) else [result]

        # Register each plugin
        for plugin in plugins:
            if not isinstance(plugin, Plugin):
                logger.warning(
                    f"gbs_register() in {module_name} returned non-Plugin instance: {type(plugin)}"
                )
                continue

            self._register_plugin(plugin)

    def _register_plugin(self, plugin: Plugin):
        """Register a plugin instance

        Args:
            plugin: Plugin instance to register
        """
        # Check for duplicate names
        if plugin.name in self._plugins:
            logger.warning(f"Plugin {plugin.name} already registered, replacing")

        self._plugins[plugin.name] = plugin
        logger.info(f"Registered plugin: {plugin.name} v{plugin.version}")

    def get_plugin(self, name: str) -> Optional[Plugin]:
        """Get a plugin by name

        Args:
            name: Plugin name

        Returns:
            Plugin instance or None if not found
        """
        return self._plugins.get(name)

    def list_plugins(self) -> list[str]:
        """List all registered plugin names

        Returns:
            List of plugin names
        """
        return list(self._plugins.keys())

    def get_all_plugins(self) -> list[Plugin]:
        """Get all registered plugins

        Returns:
            List of Plugin instances
        """
        return list(self._plugins.values())

    # Backend methods (backends are discovered via plugins)

    def get_backend(self, module_path: str) -> Optional[Backend]:
        """Get backend instance by module path

        Args:
            module_path: Backend module path (e.g., "gbs.builtin.ghdl")

        Returns:
            Backend instance, or None if not found
        """
        backend_info = self.get_backend_info(module_path)
        return backend_info.backend if backend_info else None

    def get_backend_info(self, module_path: str) -> Optional[BackendInfo]:
        """Get backend info by module path

        Args:
            module_path: Backend module path (e.g., "gbs.builtin.ghdl")

        Returns:
            BackendInfo, or None if not found
        """
        # Find the plugin that matches this module path
        plugin = self._plugins.get(module_path)
        if not plugin:
            return None

        # Get backends from this plugin
        backends = plugin.enumerate_backends()
        if backends:
            # Assume one backend per plugin module
            return BackendInfo(backend=backends[0], module_path=module_path)

        return None

    def list_backends(self) -> list[str]:
        """List all registered backend module paths

        Returns:
            List of backend module paths
        """
        backend_paths = []
        for plugin in self._plugins.values():
            backends = plugin.enumerate_backends()
            if backends:
                # Each plugin provides its own backend(s)
                backend_paths.append(plugin.name)
        return backend_paths

    def get_all_backends(self) -> list[Backend]:
        """Get all registered backend instances

        Returns:
            List of Backend instances
        """
        all_backends = []
        for plugin in self._plugins.values():
            backends = plugin.enumerate_backends()
            all_backends.extend(backends)
        return all_backends

    # Toolchain provider methods

    def get_toolchain_provider_class(self, type_name: str) -> Optional[type[ToolchainProvider]]:
        """Look up a toolchain provider class by its `type` name.

        Scans all plugins in registration order. When two plugins claim
        the same type, the last one to register wins (with a warning).

        Args:
            type_name: The `type:` value from a `toolchains:` config entry.

        Returns:
            ToolchainProvider class, or None if no plugin provides this type.
        """
        found: Optional[type[ToolchainProvider]] = None
        for plugin in self._plugins.values():
            providers = plugin.enumerate_toolchain_providers()
            if type_name in providers:
                if found is not None:
                    logger.warning(
                        f"Toolchain provider type {type_name!r} claimed by multiple plugins; "
                        f"using {plugin.name}"
                    )
                found = providers[type_name]
        return found

    def all_toolchain_provider_types(self) -> list[str]:
        """List every toolchain type registered across plugins.

        Returns:
            Sorted list of unique type names.
        """
        types: set[str] = set()
        for plugin in self._plugins.values():
            types.update(plugin.enumerate_toolchain_providers().keys())
        return sorted(types)


# Global plugin registry singleton
_registry: Optional[PluginRegistry] = None


def get_plugin_registry() -> PluginRegistry:
    """Get the global plugin registry singleton

    On first call, creates the registry and discovers plugins.

    Returns:
        Global PluginRegistry instance
    """
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
        _registry.discover_plugins()
    return _registry


def reset_plugin_registry():
    """Reset the global plugin registry

    Useful for testing. Creates a fresh registry on next get_plugin_registry() call.
    """
    global _registry
    _registry = None


__all__ = [
    "PluginRegistry",
    "get_plugin_registry",
    "reset_plugin_registry",
    "BackendInfo",
]
