"""Pass Models for Type-Based Build Planning

This module defines Pass as planning metadata for GBS build planning.
Passes declare file type transformations but do NOT execute them.
Execution is handled by Dispatchers after planning is complete.

A Pass is pure planning metadata that describes:
- What input file types it needs
- What output file types it produces
- What filter variables it contributes for source selection

The build planner uses Passes to find transformation paths from
available sources to desired outputs.
"""

from __future__ import annotations
from abc import ABC
from typing import Any


class Pass(ABC):
    """Planning metadata describing a file type transformation

    Pass is PURE PLANNING METADATA. It does NOT execute build tools.
    Execution happens via Dispatchers after planning is complete.

    A Pass declares:
    - name: Human-readable identifier
    - input_types: Set of required input file types
    - output_types: Set of produced output file types
    - contribute_filter_vars(): Optional filter variables for source selection

    The build planner queries backends for Passes, then uses them to find
    transformation chains from available sources to desired outputs.

    Attributes:
        name: Pass name (e.g., "ghdl-simulate", "gowin-synthesize")
        input_types: Set of input file types this pass requires
        output_types: Set of output file types this pass produces
        can_fork: If True, planner may explore multiple paths through this pass
        priority: Planning priority (lower = preferred, default 100)

    Examples:
        >>> class GhdlSimulatePass(Pass):
        ...     '''GHDL simulation pass: VHDL → simulator executable'''
        ...     name = "ghdl-simulate"
        ...     input_types = {"vhdl"}
        ...     output_types = {"ghdl-simulator"}
        ...
        ...     def contribute_filter_vars(self, config):
        ...         return {
        ...             "target-usage": "simulation",
        ...             "vhdl-version": config.get("vhdl_standard", "93"),
        ...         }

        >>> class GowinSynthesisPass(Pass):
        ...     '''Gowin synthesis: VHDL/Verilog → netlist'''
        ...     name = "gowin-synthesize"
        ...     input_types = {"vhdl", "verilog", "systemverilog"}
        ...     output_types = {"gowin-netlist"}
        ...     can_fork = True  # Multiple HDL input types
        ...
        ...     def contribute_filter_vars(self, config):
        ...         return {
        ...             "target-usage": "synthesis",
        ...             "vendor": "gowin",
        ...         }
    """

    # Class attributes (must be overridden by subclasses)
    name: str
    input_types: set[str]
    output_types: set[str]

    # Optional class attributes
    can_fork: bool = False
    priority: int = 100

    def contribute_filter_vars(self, config: dict[str, Any]) -> dict[str, Any]:
        """Contribute filter variables for source enumeration

        Passes can provide filter variables that will be merged with the
        OutputGroup's filter_vars before enumerating sources. This allows
        passes to request specific sources based on their needs.

        The planner combines filter_vars from ALL passes in a selected plan
        before enumerating sources. This ensures the source set matches
        what all passes expect.

        Args:
            config: Backend-specific configuration from OutputGroup.backend_config
                   (e.g., {"vhdl_standard": "2008", "optimization": "2"})

        Returns:
            Dictionary of filter variable name -> value

        Examples:
            >>> # GHDL simulation pass wants simulation sources
            >>> def contribute_filter_vars(self, config):
            ...     return {
            ...         "target-usage": "simulation",
            ...         "compiler": "ghdl",
            ...         "vhdl-version": config.get("vhdl_standard", "93"),
            ...     }

            >>> # Gowin synthesis pass wants synthesis sources
            >>> def contribute_filter_vars(self, config):
            ...     return {
            ...         "target-usage": "synthesis",
            ...         "vendor": "gowin",
            ...         "family": config.get("device_family", "GW1N"),
            ...     }

            >>> # Memory initialization pass contributes no variables
            >>> def contribute_filter_vars(self, config):
            ...     return {}
        """
        return {}

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"name={self.name}, "
            f"input={self.input_types}, "
            f"output={self.output_types})"
        )

    def __str__(self) -> str:
        inputs = ", ".join(sorted(self.input_types))
        outputs = ", ".join(sorted(self.output_types))
        return f"{self.name}: [{inputs}] → [{outputs}]"


class PassMetadata:
    """Runtime metadata about a Pass instance

    Used by the planner to track pass selection and configuration.
    This is separate from the Pass class itself to keep Pass immutable.

    Attributes:
        pass_class: The Pass class
        config: Backend-specific configuration for this pass
        filter_vars: Cached filter variables from contribute_filter_vars()
        backend_name: Name of backend that provided this pass
    """

    def __init__(
        self,
        pass_class: type[Pass],
        config: dict[str, Any],
        backend_name: str
    ):
        """Initialize pass metadata

        Args:
            pass_class: The Pass class
            config: Backend configuration
            backend_name: Backend module name (e.g., "gbs.builtin.ghdl")
        """
        self.pass_class = pass_class
        self.config = config
        self.backend_name = backend_name

        # Create a temporary instance to get filter vars
        # (Pass classes should not have __init__ requirements)
        pass_instance = pass_class()
        self.filter_vars = pass_instance.contribute_filter_vars(config)

    @property
    def name(self) -> str:
        """Pass name"""
        return self.pass_class.name

    @property
    def input_types(self) -> set[str]:
        """Input file types"""
        return self.pass_class.input_types

    @property
    def output_types(self) -> set[str]:
        """Output file types"""
        return self.pass_class.output_types

    def __repr__(self) -> str:
        return (
            f"PassMetadata("
            f"pass={self.pass_class.__name__}, "
            f"backend={self.backend_name})"
        )
