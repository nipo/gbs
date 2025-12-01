"""Pass and Backend Models for Output-Driven Build Planning

This module defines the new pass-based architecture for GBS build planning.
Passes represent atomic transformations (file type A → file type B).
Backends group related passes together.

See doc/plan/build_system_refactoring.md for the complete design.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .build import BuildContext, BuildResource


class Pass(ABC):
    """Atomic transformation unit that converts file types

    A Pass declares its capabilities via input/output types and can
    contribute filter variables for source enumeration. Passes are
    discovered via Backend classes.

    Attributes:
        name: Pass name (e.g., "simulate", "synthesize")
        input_types: Set of input file types this pass can consume
        output_types: Set of output file types this pass produces

    Examples:
        >>> class GhdlSimulatePass(Pass):
        ...     name = "simulate"
        ...     input_types = {"vhdl"}
        ...     output_types = {"simulator"}
    """

    name: str
    input_types: set[str]
    output_types: set[str]

    def contribute_filter_vars(self, config: dict) -> dict:
        """Contribute filter variables for source enumeration

        Passes can add filter_vars that will be merged with the output
        group's filter_vars before source enumeration. This allows passes
        to request specific sources (e.g., sim=1 for simulation).

        Args:
            config: Backend-specific configuration from output_group.backend_config

        Returns:
            Dictionary of filter variables to add

        Examples:
            >>> def contribute_filter_vars(self, config: dict) -> dict:
            ...     return {"sim": 1}
        """
        return {}

    @abstractmethod
    async def execute(
        self,
        context: 'BuildContext',
        inputs: list['BuildResource']
    ) -> list['BuildResource']:
        """Transform input resources to output resources

        This is the core transformation function. The pass receives input
        files and produces output files by executing tool commands, copying
        files, or performing in-memory transformations.

        Args:
            context: Build context with project config and tool access
            inputs: List of input BuildResources

        Returns:
            List of output BuildResources produced by this pass

        Raises:
            BuildError: If the transformation fails

        Examples:
            >>> async def execute(self, context, inputs):
            ...     # Analyze VHDL files
            ...     for inp in inputs:
            ...         await run_ghdl_analyze(inp.path)
            ...     # Elaborate and create simulator
            ...     sim_path = await run_ghdl_elaborate(context.topcell)
            ...     return [BuildResource(sim_path, "simulator")]
        """
        pass


class Backend:
    """Logical grouping of related passes

    A Backend is a collection of passes that work together. There should
    be exactly one Backend per Python module. The backend class provides
    pass discovery via the get_passes() method.

    Attributes:
        passes: List of Pass classes provided by this backend

    Examples:
        >>> class GhdlBackend(Backend):
        ...     passes = [GhdlSimulatePass]
        >>>
        >>> def get_backend() -> type[Backend]:
        ...     return GhdlBackend
    """

    passes: list[type[Pass]]

    @classmethod
    def get_passes(cls) -> list[type[Pass]]:
        """Get all passes provided by this backend

        Returns:
            List of Pass classes
        """
        return cls.passes
