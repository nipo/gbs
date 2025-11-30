"""Backend System for GBS

This module implements the unified backend system where all backends
(preprocessing, transpilation, and main compilation) are equal participants
in an iterative transformation process.

Key concepts:
- Backend: Transforms BuildFileSet iteratively
- BackendRegistry: Manages backends and their priorities
- Iteration loop: Runs backends until fileset converges
- Filter variables: Backends provide variables for partition evaluation
"""

from __future__ import annotations
from typing import Protocol, Any
from abc import ABC, abstractmethod

from .build import BuildContext, BuildFileSet
from ..logging import get_logger


class Backend(Protocol):
    """Protocol for backends that transform the BuildFileSet

    All backends must implement:
    - name: Unique identifier
    - priority: Execution order (lower = earlier, default range 100-999)
    - get_filter_variables(): Provide variables for partition filtering
    - process(): Transform the fileset
    """

    name: str
    priority: int

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Provide filter variables for partition evaluation

        These variables are used when evaluating the source model to determine
        which files should be included in the build.

        Args:
            context: Build context

        Returns:
            Dictionary of variable_name -> value

        Example:
            return {"target_language": "vhdl", "has_verilog_support": True}
        """
        ...

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Process the fileset, transforming it in place

        Backends can:
        - Add new generated files (e.g., transpiled outputs)
        - Remove processed files (e.g., inputs that were transformed)
        - Replace files (e.g., optimized versions)
        - Create build tasks for files
        - Query and filter existing files

        The fileset is modified in place. The modification serial will be
        used to detect convergence.

        Args:
            context: Build context
            fileset: BuildFileSet to transform

        Note:
            This is an async method to support task creation and other
            async operations.
        """
        ...


class BaseBackend(ABC):
    """Base class for backends

    Provides common functionality and enforces the Backend protocol.
    Subclasses must implement get_filter_variables() and process().
    """

    def __init__(self, name: str, priority: int = 500):
        """Initialize backend

        Args:
            name: Unique backend name
            priority: Execution priority (lower = earlier)
                     Suggested ranges:
                     100-299: Preprocessing (transpilers, code generators)
                     300-499: Intermediate processing
                     500-699: Main compilation
                     700-999: Post-processing
        """
        self.name = name
        self.priority = priority
        self.logger = get_logger(f"Backend({name})")

    @abstractmethod
    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Provide filter variables for partition evaluation

        Must be implemented by subclasses.
        """
        ...

    @abstractmethod
    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Process the fileset

        Must be implemented by subclasses.
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name}, priority={self.priority})"


class BackendRegistry:
    """Registry for managing backends

    Maintains the list of registered backends and provides methods for:
    - Registering backends
    - Getting backends in priority order
    - Collecting filter variables from all backends
    """

    def __init__(self):
        """Initialize empty registry"""
        self._backends: list[Backend] = []
        self.logger = get_logger("BackendRegistry")

    def register(self, backend: Backend) -> None:
        """Register a backend

        Args:
            backend: Backend to register

        Raises:
            ValueError: If backend with same name already registered
        """
        # Check for duplicate names
        if any(b.name == backend.name for b in self._backends):
            raise ValueError(f"Backend with name '{backend.name}' already registered")

        self._backends.append(backend)
        self.logger.debug(f"Registered backend: {backend.name} (priority={backend.priority})")

    def get_backends_ordered(self) -> list[Backend]:
        """Get backends in priority order (lowest priority first)

        Returns:
            List of backends sorted by priority
        """
        return sorted(self._backends, key=lambda b: (b.priority, b.name))

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Collect filter variables from all backends

        Args:
            context: Build context

        Returns:
            Combined dictionary of all filter variables

        Note:
            If multiple backends provide the same variable, later backends
            (higher priority) will override earlier ones.
        """
        variables = {}
        for backend in self.get_backends_ordered():
            backend_vars = backend.get_filter_variables(context)
            if backend_vars:
                variables.update(backend_vars)
                self.logger.debug(
                    f"Backend {backend.name} provided variables: {list(backend_vars.keys())}"
                )
        return variables

    def __len__(self) -> int:
        """Number of registered backends"""
        return len(self._backends)

    def __iter__(self):
        """Iterate over backends in priority order"""
        return iter(self.get_backends_ordered())


async def run_backend_iteration(
    context: BuildContext,
    fileset: BuildFileSet,
    registry: BackendRegistry,
    max_iterations: int = 100
) -> int:
    """Run backend iteration loop until convergence

    Iteratively runs all backends until the fileset stops changing
    (modification serial stabilizes).

    Args:
        context: Build context
        fileset: BuildFileSet to process
        registry: Backend registry
        max_iterations: Maximum iterations before giving up

    Returns:
        Number of iterations performed

    Raises:
        RuntimeError: If max_iterations exceeded without convergence

    Example:
        registry = BackendRegistry()
        registry.register(VerilogToVHDL())
        registry.register(GHDL())

        fileset = BuildFileSet(context)
        # ... populate fileset with source files ...

        iterations = await run_backend_iteration(context, fileset, registry)
        print(f"Converged after {iterations} iterations")
    """
    logger = get_logger("BackendIteration")
    iteration = 0

    logger.info(f"Starting backend iteration with {len(registry)} backends")

    while iteration < max_iterations:
        iteration += 1
        serial_before = fileset.modification_serial

        logger.debug(f"Iteration {iteration}: serial={serial_before}, files={len(fileset)}")

        # Run all backends in priority order
        for backend in registry:
            logger.debug(f"Running backend: {backend.name}")
            await backend.process(context, fileset)

        serial_after = fileset.modification_serial

        # Check for convergence
        if serial_after == serial_before:
            logger.info(
                f"Converged after {iteration} iterations "
                f"(serial={serial_after}, files={len(fileset)})"
            )
            return iteration

        logger.debug(
            f"Iteration {iteration} complete: "
            f"serial {serial_before} -> {serial_after}, "
            f"files={len(fileset)}"
        )

    # Failed to converge
    raise RuntimeError(
        f"Backend iteration did not converge after {max_iterations} iterations. "
        f"This may indicate a backend is continuously modifying the fileset."
    )
