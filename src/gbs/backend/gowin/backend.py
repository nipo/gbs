"""Gowin Backend implementation"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...model.backend import BaseBackend
from ...model.passes import Pass
from ...model.dispatcher import Dispatcher
from .passes import GowinSynthesizePass
from .dispatcher import GowinDispatcher


class GowinBackend(BaseBackend):
    """Gowin FPGA synthesis backend

    Provides Gowin synthesis pass and dispatcher for executing FPGA builds.

    Configuration options:
        - device: Target device (e.g., "GW1NR-9")
        - output_dir: Build output directory
        - gowin_tool: Tool identifier for lookup (default: "gowin")
        - output_base_name: Base name for output files
    """

    def __init__(self):
        super().__init__("gbs.backend.gowin")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str]
    ) -> list[type[Pass]]:
        """Contribute Gowin passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types

        Returns:
            List of Pass classes that can help produce the outputs
        """
        passes = []

        # If any output type is gowin-fs, gowin-netlist, or generic bitstream/netlist,
        # contribute the Gowin synthesis pass
        if any(t in output_types for t in ["gowin-fs", "gowin-netlist", "bitstream", "netlist"]):
            passes.append(GowinSynthesizePass)

        return passes

    def create_dispatcher(self, config: dict[str, Any]) -> Dispatcher:
        """Create Gowin dispatcher for execution

        Args:
            config: Backend configuration with optional:
                - device: Target device string
                - output_dir: Output directory path
                - gowin_tool: Tool identifier
                - output_base_name: Base name for outputs

        Returns:
            GowinDispatcher instance
        """
        output_dir = config.get("output_dir", "build")
        gowin_tool = config.get("gowin_tool", "gowin")
        output_base_name = config.get("output_base_name")

        return GowinDispatcher(
            output_dir=output_dir,
            gowin_tool=gowin_tool,
            output_base_name=output_base_name
        )
