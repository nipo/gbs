"""Backend Registry for Build Planning

This module provides the global registry for discovering and loading backends.
Backends are discovered via the unified plugin system.
"""

from __future__ import annotations
from typing import Optional
from dataclasses import dataclass

from ..backend.protocol import Backend
from ..logging import get_logger
from ..plugins import get_plugin_registry

logger = get_logger(__name__)

__all__ = ["BackendInfo", "BackendRegistry", "get_backend_registry"]

@dataclass
class BackendInfo:
    """Information about a registered backend

    Attributes:
        backend: The Backend instance
        module_path: Module path (e.g., "gbs.builtin.ghdl")
    """
    backend: Backend
    module_path: str


class BackendRegistry:
    """Global registry of backends

    The registry discovers backends via the unified plugin system.
    Provides backward-compatible API by mapping plugin names to backends.

    Backends implement the Backend Protocol with:
    - contribute_passes(config, output_types) -> list[type[Pass]]
    - create_dispatcher(config) -> Dispatcher
    """

    def __init__(self):
        """Initialize empty registry"""
        self._backends: dict[str, BackendInfo] = {}  # module_path -> BackendInfo

    def discover_backends(self):
        """Discover all backends via the unified plugin system"""
        logger.info("Discovering backends via plugin system...")

        # Get the plugin registry (which auto-discovers plugins)
        plugin_registry = get_plugin_registry()

        # Iterate over each plugin and get its backends
        for plugin in plugin_registry.get_all_plugins():
            try:
                # Get backends provided by this plugin
                backends = plugin.enumerate_backends()

                # Register each backend
                for backend in backends:
                    # Use the plugin name as the module path for backward compatibility
                    # For built-in plugins, this is "gbs.builtin.ghdl", etc.
                    module_path = plugin.name

                    # Create BackendInfo
                    backend_info = BackendInfo(
                        backend=backend,
                        module_path=module_path
                    )

                    self._backends[module_path] = backend_info
                    logger.debug(f"Registered backend: {backend.name} from {module_path}")

            except Exception as e:
                logger.error(f"Error enumerating backends from plugin {plugin.name}: {e}")

        logger.info(
            f"Backend discovery complete: {len(self._backends)} backends"
        )

    def get_backend(self, module_path: str) -> Optional[Backend]:
        """Get backend instance by module path

        Args:
            module_path: Backend module path (e.g., "gbs.builtin.ghdl")

        Returns:
            Backend instance, or None if not found
        """
        backend_info = self._backends.get(module_path)
        if backend_info:
            return backend_info.backend
        return None

    def get_backend_info(self, module_path: str) -> Optional[BackendInfo]:
        """Get backend info by module path

        Args:
            module_path: Backend module path (e.g., "gbs.builtin.ghdl")

        Returns:
            BackendInfo, or None if not found
        """
        return self._backends.get(module_path)

    def list_backends(self) -> list[str]:
        """List all registered backend module paths

        Returns:
            List of backend module paths
        """
        return list(self._backends.keys())

    def get_all_backends(self) -> list[Backend]:
        """Get all registered backend instances

        Returns:
            List of Backend instances
        """
        return [info.backend for info in self._backends.values()]


# Global singleton registry
_global_registry: Optional[BackendRegistry] = None


def get_backend_registry() -> BackendRegistry:
    """Get the global backend registry (singleton)

    Returns:
        Global BackendRegistry instance
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = BackendRegistry()
        _global_registry.discover_backends()
    return _global_registry


def reset_backend_registry():
    """Reset the global registry (for testing)"""
    global _global_registry
    _global_registry = None
