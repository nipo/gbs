"""Gowin Pass definitions"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...model.passes import Pass


class GowinSynthesizePass(Pass):
    """Pass that synthesizes HDL to Gowin FPGA bitstream

    This pass uses Gowin EDA tools (via gw_sh) to:
    - Synthesize VHDL/Verilog to netlist
    - Aggregate constraints from multiple sources
    - Run place & route
    - Generate bitstream

    Input types: vhdl, verilog
    Output types: gowin-fs (bitstream), gowin-netlist
    """
    name = "gowin-synthesize"
    input_types = {"vhdl", "verilog"}
    output_types = {"gowin-fs", "gowin-netlist"}

    def contribute_filter_vars(self, config: dict[str, Any]) -> dict[str, Any]:
        """Contribute filter variables for Gowin synthesis

        Sets target-usage=synthesis to allow conditional source filtering.
        Also provides device characteristics if device is configured.

        Args:
            config: Backend configuration dict with optional:
                - device: Target device string (e.g., "GW1NR-9")
                - output_dir: Output directory path
                - gowin_tool: Tool identifier for lookup

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
