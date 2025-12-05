"""GHDL Backend implementation"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.protocol import BaseBackend
from ...planner.passes import Pass
from .passes import GHDLSimulatePass

class GHDLBackend(BaseBackend):
    """GHDL Backend for VHDL simulation

    Provides GHDL simulation pass and dispatcher for executing VHDL builds.

    Configuration options:
        - vhdl_standard: VHDL standard (e.g., "93c", "08", "2008")
        - ghdl_tool: Tool identifier for lookup (default: "ghdl")
    """

    def __init__(self):
        super().__init__("gbs.builtin.ghdl")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str]
    ) -> list[Pass]:
        """Contribute GHDL passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types

        Returns:
            List of Pass instances that can help produce the outputs
        """
        passes = []

        # If any output type is ghdl-simulator or a generic simulator type,
        # contribute the GHDL simulation pass
        if "ghdl-simulator" in output_types or "simulator" in output_types:
            passes.append(GHDLSimulatePass(config))

        return passes
