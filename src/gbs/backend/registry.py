"""Backend Registry for Build Planning

This module provides the global registry for discovering and loading backends.
Backends are discovered via the unified plugin system.
"""

from __future__ import annotations
from typing import Optional
from dataclasses import dataclass

from ..protocol import Backend
from ..logging import get_logger
from ..plugins import get_plugin_registry

logger = get_logger(__name__)

__all__ = ["BackendInfo", "BackendRegistry", "get_backend_registry",
           "DispatcherRegistry", "run_dispatcher_iteration"]

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
    Maps plugin names (module paths) to backend instances.

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
            # Get backends provided by this plugin
            backends = plugin.enumerate_backends()

            # Register each backend
            for backend in backends:
                # Use the plugin name as the module path
                # For built-in plugins, this is "gbs.builtin.ghdl", etc.
                module_path = plugin.name

                # Create BackendInfo
                backend_info = BackendInfo(
                    backend=backend,
                    module_path=module_path
                )

                self._backends[module_path] = backend_info
                logger.debug(f"Registered backend: {backend.name} from {module_path}")

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


class DispatcherRegistry:
    """Registry for managing dispatchers

    Maintains the list of registered dispatchers and provides methods for:
    - Registering dispatchers
    - Iterating over dispatchers
    """

    def __init__(self):
        """Initialize empty registry"""
        from ..protocol import Dispatcher
        self._dispatchers: list[Dispatcher] = []
        self.logger = get_logger("DispatcherRegistry")

    def register(self, dispatcher) -> None:
        """Register a dispatcher

        Args:
            dispatcher: Dispatcher to register

        Raises:
            ValueError: If dispatcher with same name already registered
        """
        # Check for duplicate names
        if any(d.name == dispatcher.name for d in self._dispatchers):
            raise ValueError(f"Dispatcher with name '{dispatcher.name}' already registered")

        self._dispatchers.append(dispatcher)
        self.logger.debug(f"Registered dispatcher: {dispatcher.name}")

    def __len__(self) -> int:
        """Number of registered dispatchers"""
        return len(self._dispatchers)

    def __iter__(self):
        """Iterate over dispatchers in registration order"""
        return iter(self._dispatchers)


async def run_dispatcher_iteration(
    context,
    registry: DispatcherRegistry,
    max_iterations: int = 100
) -> int:
    """Run dispatcher iteration loop until convergence

    Iteratively runs all dispatchers until the pending queue stops changing
    (modification serial stabilizes).

    Args:
        context: Build context (contains pending work queue)
        registry: Dispatcher registry
        max_iterations: Maximum iterations before giving up

    Returns:
        Number of iterations performed

    Raises:
        RuntimeError: If max_iterations exceeded without convergence

    Example:
        registry = DispatcherRegistry()
        registry.register(VerilogToVHDLDispatcher())
        registry.register(GHDLDispatcher())

        # Populate pending queue with source files
        types_with_library = {"vhdl", "verilog"}
        context.populate_pending(build_set, types_with_library)

        iterations = await run_dispatcher_iteration(context, registry)
        print(f"Converged after {iterations} iterations")
    """
    iteration_logger = get_logger("DispatcherIteration")
    iteration = 0

    iteration_logger.info(f"Starting dispatcher iteration with {len(registry)} dispatchers")

    while iteration < max_iterations:
        iteration += 1
        serial_before = context.pending_modification_serial

        iteration_logger.debug(f"Iteration {iteration}: serial={serial_before}, pending={context.pending_count()}")

        # Run all dispatchers in registration order
        for dispatcher in registry:
            s = context.pending_modification_serial
            iteration_logger.debug(f"Running dispatcher: {dispatcher.name}")
            await dispatcher.process()
            if s != context.pending_modification_serial:
                iteration_logger.debug(f"  Changes happened")

        serial_after = context.pending_modification_serial

        # Check for convergence
        if serial_after == serial_before:
            iteration_logger.info(
                f"Converged after {iteration} iterations "
                f"(serial={serial_after}, pending={context.pending_count()})"
            )

            # Verify all outputs have producers
            unsatisfied = context.get_pending_unsatisfied_outputs()
            if unsatisfied:
                unsatisfied_info = [
                    f"  - {res.file_type} at {res.path}" for res in unsatisfied
                ]
                raise RuntimeError(
                    f"Build planning failed: {len(unsatisfied)} output(s) have no producer:\n"
                    + "\n".join(unsatisfied_info)
                )

            return iteration

        iteration_logger.debug(
            f"Iteration {iteration} complete: "
            f"serial {serial_before} -> {serial_after}, "
            f"pending={context.pending_count()}"
        )

    # Failed to converge
    raise RuntimeError(
        f"Dispatcher iteration did not converge after {max_iterations} iterations. "
        f"This may indicate a dispatcher is continuously modifying the pending queue."
    )
