"""Backend loading and discovery for GBS

This module provides utilities for:
- Loading backend classes from Python modules
- Discovering backends via entry points
- Creating backend instances with configuration
- Validating backend implementations
"""

from __future__ import annotations
import importlib
import importlib.metadata
from typing import Any, Type
from pathlib import Path

from gbs.backend import Backend, BaseBackend, BackendRegistry
from gbs.logging import get_logger


class BackendLoadError(Exception):
    """Error loading or configuring a backend"""
    pass


class BackendLoader:
    """Loads and instantiates backends from various sources

    Supports:
    - Loading from module:class specification
    - Discovery via entry points
    - Configuration validation
    - Backend instantiation
    """

    def __init__(self):
        """Initialize backend loader"""
        self.logger = get_logger("BackendLoader")
        self._discovered_backends: dict[str, Type[Backend]] = {}

    def load_backend_class(self, spec: str) -> Type[Backend]:
        """Load a backend class from a module:class specification

        Args:
            spec: String like "gbs.backend:GHDLBackend" or "my_package.backends:CustomBackend"

        Returns:
            Backend class (not instantiated)

        Raises:
            BackendLoadError: If module or class cannot be loaded

        Examples:
            >>> loader = BackendLoader()
            >>> backend_class = loader.load_backend_class("gbs.backend:GHDLBackend")
            >>> backend = backend_class(output_dir="/tmp/build")
        """
        if ':' not in spec:
            raise BackendLoadError(
                f"Invalid backend spec '{spec}'. "
                f"Expected format: 'module.path:ClassName'"
            )

        module_path, class_name = spec.split(':', 1)

        # Import module
        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise BackendLoadError(
                f"Failed to import module '{module_path}': {e}"
            ) from e

        # Get class from module
        if not hasattr(module, class_name):
            raise BackendLoadError(
                f"Module '{module_path}' has no class '{class_name}'"
            )

        backend_class = getattr(module, class_name)

        # Validate it's a Backend
        if not self._is_valid_backend(backend_class):
            raise BackendLoadError(
                f"{module_path}:{class_name} does not implement Backend protocol"
            )

        self.logger.debug(f"Loaded backend class: {module_path}:{class_name}")
        return backend_class

    def create_backend(self, spec: str, config: dict[str, Any] | None = None) -> Backend:
        """Load and instantiate a backend with configuration

        Args:
            spec: Module:class specification
            config: Optional configuration dictionary passed to backend constructor

        Returns:
            Instantiated backend

        Raises:
            BackendLoadError: If loading or instantiation fails

        Examples:
            >>> loader = BackendLoader()
            >>> backend = loader.create_backend(
            ...     "gbs.backend:GHDLBackend",
            ...     config={"output_dir": "/tmp/build"}
            ... )
        """
        backend_class = self.load_backend_class(spec)
        config = config or {}

        try:
            backend = backend_class(**config)
        except TypeError as e:
            raise BackendLoadError(
                f"Failed to instantiate {spec} with config {config}: {e}"
            ) from e

        self.logger.info(f"Created backend: {backend.name} (priority={backend.priority})")
        return backend

    def discover_entry_points(self, group: str = "gbs.backends") -> dict[str, Type[Backend]]:
        """Discover backends via Python entry points

        Args:
            group: Entry point group name (default: "gbs.backends")

        Returns:
            Dictionary mapping backend names to classes

        Note:
            Backends should declare entry points in their setup.py or pyproject.toml:

            [project.entry-points."gbs.backends"]
            ghdl = "my_package.backends:GHDLBackend"
            vivado = "my_package.backends:VivadoBackend"

        Examples:
            >>> loader = BackendLoader()
            >>> backends = loader.discover_entry_points()
            >>> for name, backend_class in backends.items():
            ...     print(f"Found backend: {name}")
        """
        discovered = {}

        try:
            entry_points = importlib.metadata.entry_points()

            # Handle both old and new entry_points API
            if hasattr(entry_points, 'select'):
                # Python 3.10+
                group_eps = entry_points.select(group=group)
            else:
                # Python 3.9
                group_eps = entry_points.get(group, [])

            for ep in group_eps:
                try:
                    backend_class = ep.load()

                    if self._is_valid_backend(backend_class):
                        discovered[ep.name] = backend_class
                        self.logger.debug(f"Discovered backend via entry point: {ep.name}")
                    else:
                        self.logger.warning(
                            f"Entry point '{ep.name}' does not implement Backend protocol"
                        )
                except Exception as e:
                    self.logger.warning(f"Failed to load entry point '{ep.name}': {e}")

        except Exception as e:
            self.logger.warning(f"Failed to discover entry points: {e}")

        self._discovered_backends = discovered
        return discovered

    def load_from_config(self, backend_configs: list[dict[str, Any]]) -> BackendRegistry:
        """Load backends from configuration list

        Args:
            backend_configs: List of backend configuration dictionaries.
                Each dict should have:
                - 'backend': Module:class specification or entry point name
                - 'config': Optional configuration dict for the backend

        Returns:
            BackendRegistry with all loaded backends

        Raises:
            BackendLoadError: If any backend fails to load

        Examples:
            >>> config = [
            ...     {
            ...         "backend": "gbs.backend:VerilogToVHDLBackend",
            ...         "config": {}
            ...     },
            ...     {
            ...         "backend": "gbs.backend:GHDLBackend",
            ...         "config": {"output_dir": "/tmp/build"}
            ...     }
            ... ]
            >>> loader = BackendLoader()
            >>> registry = loader.load_from_config(config)
        """
        registry = BackendRegistry()

        for idx, backend_config in enumerate(backend_configs):
            if 'backend' not in backend_config:
                raise BackendLoadError(
                    f"Backend config at index {idx} missing 'backend' key"
                )

            backend_spec = backend_config['backend']
            config = backend_config.get('config', {})

            # Try to load from entry point first if it looks like a simple name
            if ':' not in backend_spec and self._discovered_backends:
                if backend_spec in self._discovered_backends:
                    backend_class = self._discovered_backends[backend_spec]
                    try:
                        backend = backend_class(**config)
                        registry.register(backend)
                        continue
                    except Exception as e:
                        raise BackendLoadError(
                            f"Failed to instantiate entry point backend '{backend_spec}': {e}"
                        ) from e

            # Load from module:class spec
            backend = self.create_backend(backend_spec, config)
            registry.register(backend)

        self.logger.info(f"Loaded {len(registry)} backends from configuration")
        return registry

    def _is_valid_backend(self, backend_class: Type) -> bool:
        """Check if a class implements the Backend protocol

        Args:
            backend_class: Class to validate

        Returns:
            True if class implements Backend protocol
        """
        # Check for required attributes
        required_attrs = ['name', 'priority', 'get_filter_variables', 'process']

        # For classes (not instances), check if they would have these after instantiation
        # We do this by checking if it's a subclass of BaseBackend or has the methods
        try:
            # Check if it's a subclass of BaseBackend
            if isinstance(backend_class, type) and issubclass(backend_class, BaseBackend):
                return True

            # Check if it has all required methods
            for attr in required_attrs:
                if not hasattr(backend_class, attr):
                    return False

            return True
        except TypeError:
            # Not a class
            return False


def load_backends_from_project(project_config: dict[str, Any]) -> BackendRegistry:
    """Convenience function to load backends from project configuration

    Args:
        project_config: Project configuration dictionary with 'backends' key

    Returns:
        BackendRegistry with loaded backends

    Examples:
        >>> project = {
        ...     "backends": [
        ...         {"backend": "gbs.backend:GHDLBackend", "config": {}}
        ...     ]
        ... }
        >>> registry = load_backends_from_project(project)
    """
    loader = BackendLoader()

    # Discover entry points first
    loader.discover_entry_points()

    # Load from project config
    backend_configs = project_config.get('backends', [])
    return loader.load_from_config(backend_configs)
