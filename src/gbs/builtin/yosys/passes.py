"""Yosys Pass definitions"""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from .dispatcher import YosysDispatcher
from ...protocol import Dispatcher


class YosysBasePass(BasePass):
    """Base pass for Yosys synthesis

    Provides common functionality for all Yosys target passes.
    Subclasses should define:
    - name: Pass name
    - output_types: Target-specific output types
    - synth_target: Yosys synthesis command (e.g., "synth_ice40")
    """

    # To be overridden by subclasses
    synth_target: str = None

    input_types = {"vhdl", "verilog"}

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for Yosys synthesis

        Returns:
            Dictionary with filter variables
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")
        ret ={
            "target-usage": "synthesis",
            "vhdl-version": vhdl_std,
            "compiler": "yosys",
        }
        ret.update(self.extra_filter_vars)
        return ret

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create Yosys dispatcher for this target

        Args:
            context: Build context to pass to dispatcher

        Returns:
            YosysDispatcher singleton configured for this target
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")
        yosys_tool = self.config.get("yosys_tool", "yosys")
        steps = self.config.get("steps", [])

        return [YosysDispatcher(
            context=context,
            synth_target=self.synth_target,
            yosys_tool=yosys_tool,
            vhdl_std=vhdl_std,
            steps=steps,
        )]

class YosysIce40Pass(YosysBasePass):
    """Pass that synthesizes VHDL to Ice40 netlist using Yosys

    This pass takes GHDL library intermediates (.cf files) and:
    - Reads the design using the GHDL plugin
    - Applies user-defined transformation steps
    - Runs synth_ice40
    - Outputs JSON netlist

    Input types: ghdl-cf
    Output types: ice40-netlist-json
    """
    name = "yosys-ice40"
    output_types = {"ice40-netlist-json"}
    synth_target = "synth_ice40"
    extra_filter_vars = {"hwdep": "lattice-ice40"}


class YosysEcp5Pass(YosysBasePass):
    """Pass that synthesizes VHDL to ECP5 netlist using Yosys

    This pass takes GHDL library intermediates (.cf files) and:
    - Reads the design using the GHDL plugin
    - Applies user-defined transformation steps
    - Runs synth_ecp5
    - Outputs JSON netlist

    Input types: ghdl-cf
    Output types: ecp5-netlist-json
    """
    name = "yosys-ecp5"
    output_types = {"ecp5-netlist-json"}
    synth_target = "synth_ecp5"
    extra_filter_vars = {"hwdep": "lattice-ecp5"}
