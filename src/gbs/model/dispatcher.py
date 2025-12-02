"""Dispatcher System for GBS

This module implements the unified dispatcher system where all dispatchers
(preprocessing, transpilation, and main compilation) are equal participants
in an iterative transformation process.

Key concepts:
- Dispatcher: Transforms BuildFileSet iteratively
- DispatcherRegistry: Manages dispatchers and their priorities
- Iteration loop: Runs dispatchers until fileset converges
- Filter variables: Dispatchers provide variables for partition evaluation
"""

from __future__ import annotations
from typing import Protocol, Any, runtime_checkable
from abc import ABC, abstractmethod

from .build import BuildContext, BuildFileSet
from ..logging import get_logger


@runtime_checkable
class Dispatcher(Protocol):
    """Protocol for dispatchers that transform the BuildFileSet

    All dispatchers must implement:
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

        Dispatchers can:
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


class BaseDispatcher(ABC):
    """Base class for dispatchers

    Provides common functionality and enforces the Dispatcher protocol.
    Subclasses must implement get_filter_variables() and process().
    """

    def __init__(self, name: str, priority: int = 500):
        """Initialize dispatcher

        Args:
            name: Unique dispatcher name
            priority: Execution priority (lower = earlier)
                     Suggested ranges:
                     100-299: Preprocessing (transpilers, code generators)
                     300-499: Intermediate processing
                     500-699: Main compilation
                     700-999: Post-processing
        """
        self.name = name
        self.priority = priority
        self.logger = get_logger(f"Dispatcher({name})")

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


class DispatcherRegistry:
    """Registry for managing dispatchers

    Maintains the list of registered dispatchers and provides methods for:
    - Registering dispatchers
    - Getting dispatchers in priority order
    - Collecting filter variables from all dispatchers
    """

    def __init__(self):
        """Initialize empty registry"""
        self._dispatchers: list[Dispatcher] = []
        self.logger = get_logger("DispatcherRegistry")

    def register(self, dispatcher: Dispatcher) -> None:
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
        self.logger.debug(f"Registered dispatcher: {dispatcher.name} (priority={dispatcher.priority})")

    def get_dispatchers_ordered(self) -> list[Dispatcher]:
        """Get dispatchers in priority order (lowest priority first)

        Returns:
            List of dispatchers sorted by priority
        """
        return sorted(self._dispatchers, key=lambda d: (d.priority, d.name))

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Collect filter variables from all dispatchers

        Args:
            context: Build context

        Returns:
            Combined dictionary of all filter variables

        Note:
            If multiple dispatchers provide the same variable, later dispatchers
            (higher priority) will override earlier ones.
        """
        variables = {}
        for dispatcher in self.get_dispatchers_ordered():
            dispatcher_vars = dispatcher.get_filter_variables(context)
            if dispatcher_vars:
                variables.update(dispatcher_vars)
                self.logger.debug(
                    f"Dispatcher {dispatcher.name} provided variables: {list(dispatcher_vars.keys())}"
                )
        return variables

    def __len__(self) -> int:
        """Number of registered dispatchers"""
        return len(self._dispatchers)

    def __iter__(self):
        """Iterate over dispatchers in priority order"""
        return iter(self.get_dispatchers_ordered())


async def run_dispatcher_iteration(
    context: BuildContext,
    fileset: BuildFileSet,
    registry: DispatcherRegistry,
    max_iterations: int = 100
) -> int:
    """Run dispatcher iteration loop until convergence

    Iteratively runs all dispatchers until the fileset stops changing
    (modification serial stabilizes).

    Args:
        context: Build context
        fileset: BuildFileSet to process
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

        fileset = BuildFileSet(context)
        # ... populate fileset with source files ...

        iterations = await run_dispatcher_iteration(context, fileset, registry)
        print(f"Converged after {iterations} iterations")
    """
    logger = get_logger("DispatcherIteration")
    iteration = 0

    logger.info(f"Starting dispatcher iteration with {len(registry)} dispatchers")

    while iteration < max_iterations:
        iteration += 1
        serial_before = fileset.modification_serial

        logger.debug(f"Iteration {iteration}: serial={serial_before}, files={len(fileset)}")

        # Run all dispatchers in priority order
        for dispatcher in registry:
            logger.debug(f"Running dispatcher: {dispatcher.name}")
            await dispatcher.process(context, fileset)

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
        f"Dispatcher iteration did not converge after {max_iterations} iterations. "
        f"This may indicate a dispatcher is continuously modifying the fileset."
    )
