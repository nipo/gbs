"""GHDL Backend implementation"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.protocol import BaseBackend
from ...backend.dispatcher import Dispatcher
from ...planner.passes import Pass
from .passes import GHDLSimulatePass
from .dispatcher import GHDLDispatcher

class GHDLBackend(BaseBackend):
    """GHDL Backend for VHDL simulation

    Provides GHDL simulation pass and dispatcher for executing VHDL builds.

    Configuration options:
        - vhdl_standard: VHDL standard (e.g., "93c", "08", "2008")
        - output_dir: Build output directory
        - ghdl_tool: Tool identifier for lookup (default: "ghdl")
    """

    def __init__(self):
        super().__init__("gbs.backend.ghdl")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str]
    ) -> list[type[Pass]]:
        """Contribute GHDL passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types

        Returns:
            List of Pass classes that can help produce the outputs
        """
        passes = []

        # If any output type is ghdl-simulator or a generic simulator type,
        # contribute the GHDL simulation pass
        if "ghdl-simulator" in output_types or "simulator" in output_types:
            passes.append(GHDLSimulatePass)

        return passes

    def create_dispatcher(self, config: dict[str, Any]) -> Dispatcher:
        """Create GHDL dispatcher for execution

        Args:
            config: Backend configuration with optional:
                - vhdl_standard: VHDL standard string
                - output_dir: Output directory path
                - ghdl_tool: Tool identifier

        Returns:
            GHDLDispatcher instance
        """
        output_dir = config.get("output_dir", "build")
        vhdl_std = config.get("vhdl_standard", "93c")
        ghdl_tool = config.get("ghdl_tool", "ghdl")

        return GHDLDispatcher(
            output_dir=output_dir,
            vhdl_std=vhdl_std,
            ghdl_tool=ghdl_tool
        )
