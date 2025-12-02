"""Backend Registry for Pass-Based Build Planning

This module provides the global registry of backends and passes.
Backends are discovered from built-in modules and entry points.
"""

from __future__ import annotations
import importlib
from typing import Optional
from dataclasses import dataclass

from ..model.passes import Pass
# Note: This registry uses the old Backend class pattern.
# It will need to be rewritten to use the new Backend interface from model.backend
# For now, we'll create a temporary compatibility shim
from ..logging import get_logger


# Temporary Backend class for backward compatibility with old registry
# This will be replaced when the new Planner is implemented
class Backend:
    """Temporary Backend class for old registry compatibility"""
    passes: list[type[Pass]]

    @classmethod
    def get_passes(cls) -> list[type[Pass]]:
        return cls.passes


logger = get_logger(__name__)


@dataclass
class PassInfo:
    """Information about a registered pass

    Attributes:
        pass_class: The Pass class
        backend_module: Module path of the backend (e.g., "gbs.backend.ghdl")
        full_name: Full pass name (e.g., "gbs.backend.ghdl:simulate")
    """
    pass_class: type[Pass]
    backend_module: str
    full_name: str


@dataclass
class BackendInfo:
    """Information about a registered backend

    Attributes:
        backend_class: The Backend class
        module_path: Module path (e.g., "gbs.backend.ghdl")
        passes: List of PassInfo for this backend
    """
    backend_class: type[Backend]
    module_path: str
    passes: list[PassInfo]


class BackendRegistry:
    """Global registry of backends and passes

    The registry discovers backends from:
    1. Built-in backend modules (hardcoded list)
    2. Third-party backends via entry points

    Passes are identified by their full name: "backend_module:pass_name"
    """

    def __init__(self):
        """Initialize empty registry"""
        self._backends: dict[str, BackendInfo] = {}  # module_path -> BackendInfo
        self._passes: dict[str, PassInfo] = {}  # full_name -> PassInfo

    def discover_backends(self):
        """Discover all backends (built-in and plugins)"""
        logger.info("Discovering backends...")

        # Built-in backends
        builtin_modules = [
            "gbs.backend.ghdl",
            "gbs.backend.gowin",
            "gbs.backend.verilog_to_vhdl",
            "gbs.backend.mem_init",
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
            f"Backend discovery complete: {len(self._backends)} backends, "
            f"{len(self._passes)} passes"
        )

    def _load_backend_module(self, module_path: str):
        """Load a backend from a module path

        Args:
            module_path: Python module path (e.g., "gbs.backend.ghdl")

        Raises:
            ImportError: If module cannot be imported
            AttributeError: If module doesn't have get_backend() function
            TypeError: If get_backend() doesn't return a Backend subclass
        """
        logger.debug(f"Loading backend module: {module_path}")

        # Import the module
        module = importlib.import_module(module_path)

        # Get the backend class via get_backend() function
        if not hasattr(module, 'get_backend'):
            raise AttributeError(
                f"Backend module {module_path} must define get_backend() function"
            )

        backend_class = module.get_backend()

        if not isinstance(backend_class, type) or not issubclass(backend_class, Backend):
            raise TypeError(
                f"get_backend() in {module_path} must return a Backend subclass, "
                f"got {backend_class}"
            )

        # Register the backend
        self._register_backend(module_path, backend_class)

    def _register_backend(self, module_path: str, backend_class: type[Backend]):
        """Register a backend and its passes

        Args:
            module_path: Module path (e.g., "gbs.backend.ghdl")
            backend_class: The Backend class
        """
        if module_path in self._backends:
            logger.warning(f"Backend {module_path} already registered, replacing")
            # Remove old passes from this backend
            old_backend_info = self._backends[module_path]
            for old_pass_info in old_backend_info.passes:
                if old_pass_info.full_name in self._passes:
                    del self._passes[old_pass_info.full_name]

        # Get passes from backend
        pass_classes = backend_class.get_passes()

        # Register each pass
        pass_infos = []
        for pass_class in pass_classes:
            full_name = f"{module_path}:{pass_class.name}"

            if full_name in self._passes:
                logger.warning(
                    f"Pass {full_name} already registered, replacing "
                    f"(backend {module_path} has duplicate pass names?)"
                )

            pass_info = PassInfo(
                pass_class=pass_class,
                backend_module=module_path,
                full_name=full_name
            )

            self._passes[full_name] = pass_info
            pass_infos.append(pass_info)

            logger.debug(
                f"Registered pass: {full_name} "
                f"(inputs: {pass_class.input_types}, outputs: {pass_class.output_types})"
            )

        # Register backend
        backend_info = BackendInfo(
            backend_class=backend_class,
            module_path=module_path,
            passes=pass_infos
        )

        self._backends[module_path] = backend_info

        logger.info(
            f"Registered backend: {module_path} with {len(pass_infos)} passes"
        )

    def get_pass(self, full_name: str) -> Optional[type[Pass]]:
        """Get pass class by full name

        Args:
            full_name: Full pass name (e.g., "gbs.backend.ghdl:simulate")

        Returns:
            Pass class, or None if not found
        """
        pass_info = self._passes.get(full_name)
        if pass_info:
            return pass_info.pass_class
        return None

    def get_pass_info(self, full_name: str) -> Optional[PassInfo]:
        """Get pass info by full name

        Args:
            full_name: Full pass name (e.g., "gbs.backend.ghdl:simulate")

        Returns:
            PassInfo, or None if not found
        """
        return self._passes.get(full_name)

    def get_backend(self, module_path: str) -> Optional[type[Backend]]:
        """Get backend class by module path

        Args:
            module_path: Backend module path (e.g., "gbs.backend.ghdl")

        Returns:
            Backend class, or None if not found
        """
        backend_info = self._backends.get(module_path)
        if backend_info:
            return backend_info.backend_class
        return None

    def get_backend_info(self, module_path: str) -> Optional[BackendInfo]:
        """Get backend info by module path

        Args:
            module_path: Backend module path (e.g., "gbs.backend.ghdl")

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

    def list_passes(self) -> list[str]:
        """List all registered pass full names

        Returns:
            List of pass full names
        """
        return list(self._passes.keys())

    def find_passes_by_output_type(self, output_type: str) -> list[PassInfo]:
        """Find all passes that can produce a given output type

        Args:
            output_type: Output file type (e.g., "simulator", "gowin-fs")

        Returns:
            List of PassInfo for passes that can produce this type
        """
        results = []
        for pass_info in self._passes.values():
            if output_type in pass_info.pass_class.output_types:
                results.append(pass_info)
        return results

    def find_passes_by_input_type(self, input_type: str) -> list[PassInfo]:
        """Find all passes that can consume a given input type

        Args:
            input_type: Input file type (e.g., "vhdl", "verilog")

        Returns:
            List of PassInfo for passes that can consume this type
        """
        results = []
        for pass_info in self._passes.values():
            if input_type in pass_info.pass_class.input_types:
                results.append(pass_info)
        return results


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
