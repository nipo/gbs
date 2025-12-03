"""Backend Registry for Build Planning

This module provides the global registry for discovering and loading backends.
Backends are discovered from built-in modules and entry points.
"""

from __future__ import annotations
import importlib
from typing import Optional
from dataclasses import dataclass

from ..backend.protocol import Backend
from ..logging import get_logger

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

    The registry discovers backends from:
    1. Built-in backend modules (hardcoded list)
    2. Third-party backends via entry points

    Backends implement the Backend Protocol with:
    - contribute_passes(config, output_types) -> list[type[Pass]]
    - create_dispatcher(config) -> Dispatcher
    """

    def __init__(self):
        """Initialize empty registry"""
        self._backends: dict[str, BackendInfo] = {}  # module_path -> BackendInfo

    def discover_backends(self):
        """Discover all backends (built-in and plugins)"""
        logger.info("Discovering backends...")

        # Built-in backends
        builtin_modules = [
            "gbs.builtin.ghdl",
            "gbs.builtin.gowin",
        ]

        for module_path in builtin_modules:
            try:
                self._load_backend_module(module_path)
            except Exception as e:
                logger.warning(f"Failed to load built-in backend {module_path}: {e}")

        # Plugin backends via entry points
        try:
            from importlib.metadata import entry_points

            # Handle both Python 3.9 and 3.10+ API
            try:
                eps = entry_points(group='gbs.backends')
            except TypeError:
                # Python 3.9 style
                eps = entry_points().get('gbs.backends', [])

            for ep in eps:
                try:
                    module_path = ep.value
                    logger.info(f"Loading plugin backend: {ep.name} from {module_path}")
                    self._load_backend_module(module_path)
                except Exception as e:
                    logger.warning(f"Failed to load plugin backend {ep.name}: {e}")
        except ImportError:
            logger.debug("importlib.metadata not available, skipping entry points")

        logger.info(
            f"Backend discovery complete: {len(self._backends)} backends"
        )

    def _load_backend_module(self, module_path: str):
        """Load a backend from a module path

        Args:
            module_path: Python module path (e.g., "gbs.builtin.ghdl")

        Raises:
            ImportError: If module cannot be imported
            AttributeError: If module doesn't have get_backend() function
            TypeError: If get_backend() doesn't return a Backend instance
        """
        logger.debug(f"Loading backend module: {module_path}")

        # Import the module
        module = importlib.import_module(module_path)

        # Get the backend via get_backend() function
        if not hasattr(module, 'get_backend'):
            raise AttributeError(
                f"Backend module {module_path} must define get_backend() function"
            )

        backend = module.get_backend()

        # Verify it's a Backend instance
        if not isinstance(backend, Backend):
            raise TypeError(
                f"get_backend() in {module_path} must return a Backend instance, "
                f"got {type(backend)}"
            )

        # Register the backend
        self._register_backend(module_path, backend)

    def _register_backend(self, module_path: str, backend: Backend):
        """Register a backend instance

        Args:
            module_path: Module path (e.g., "gbs.builtin.ghdl")
            backend: The Backend instance
        """
        if module_path in self._backends:
            logger.warning(f"Backend {module_path} already registered, replacing")

        backend_info = BackendInfo(
            backend=backend,
            module_path=module_path
        )

        self._backends[module_path] = backend_info

        logger.info(f"Registered backend: {module_path}")

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
