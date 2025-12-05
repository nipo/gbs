"""Gowin Pass definitions"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...planner.passes import Pass
from ...backend.dispatcher import Dispatcher
from .dispatcher import GowinDispatcher

class GowinSynthesizePass(Pass):
    """Pass that synthesizes HDL to Gowin FPGA bitstream

    This pass uses Gowin EDA tools (via gw_sh) to:
    - Synthesize VHDL/Verilog to netlist
    - Aggregate constraints from multiple sources (optional)
    - Run place & route
    - Generate bitstream

    Input types: vhdl (verilog and gowin-cst are optional, handled by dispatcher)
    Output types: gowin-fs (bitstream), gowin-netlist

    Note: This pass lists only vhdl as input type for planning purposes.
    The dispatcher can also handle verilog sources and gowin-cst constraints,
    but they are optional and don't need to be present for planning.
    """
    name = "gowin-synthesize"
    input_types = {"vhdl", "verilog", "gowin-cst", "gowin-sdc"}
    output_types = {"gowin-fs", "gowin-netlist"}

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for Gowin synthesis

        Sets target-usage=synthesis to allow conditional source filtering.
        Also provides device characteristics if device is configured.

        Returns:
            Dictionary with filter variables
        """
        filter_vars = {
            "target-usage": "synthesis",
            "vendor": "gowin",
        }

        # Device characteristics will be populated by dispatcher at runtime
        # based on the device configuration and device CSV database
        # For planning, we just provide the base variables

        return filter_vars

    def dispatchers(self) -> list[Dispatcher]:
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
        output_dir = self.config.get("output_dir", "build")
        gowin_tool = self.config.get("gowin_tool", "gowin")
        output_base_name = self.config.get("output_base_name")

        return [GowinDispatcher(
            output_dir=output_dir,
            gowin_tool=gowin_tool,
            output_base_name=output_base_name
        )]
