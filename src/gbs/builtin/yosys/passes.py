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
        """Contribute canonical filter variables for Yosys synthesis.

        Yosys drives the synthesis engine; the VHDL frontend comes
        from the upstream ghdl-analyze pass. Verilog goes straight to
        Yosys, so ``verilog_frontend=yosys`` is also set.
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")
        ret: dict[str, Any] = {
            "purpose": "synthesis",
            "verilog_frontend": "yosys",
            "synthesis_engine": "yosys",
            "vhdl_std": vhdl_std,
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
        yosys_tool = self.resolve_tool_identifier("yosys")
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
    output_types = {"ice40-netlist-json", "yosys-synthesis-report"}
    synth_target = "synth_ice40"
    extra_filter_vars = {"vendor": "lattice", "family": "ice40"}


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
    output_types = {"ecp5-netlist-json", "yosys-synthesis-report"}
    synth_target = "synth_ecp5"
    extra_filter_vars = {"vendor": "lattice", "family": "ecp5"}


class YosysXilinxPass(YosysBasePass):
    """Pass that synthesizes VHDL to a Xilinx netlist using Yosys.

    Emits a JSON netlist that nextpnr-xilinx (via openxc7) can
    ingest. `synth_xilinx` defaults to xc7 in the shipped yosys.

    Input types: ghdl-cf
    Output types: xilinx-netlist-json
    """
    name = "yosys-xilinx"
    output_types = {"xilinx-netlist-json", "yosys-synthesis-report"}
    synth_target = "synth_xilinx"
    extra_filter_vars = {"vendor": "xilinx"}

    def filter_vars(self) -> dict[str, Any]:
        """Match the vivado backend's technology-stack shape so a
        project builds the same sources under either flow.
        """
        from .. import xilinx_part
        ret = super().filter_vars()
        target = self.config.get("target", {})
        part = target.get("part")
        if part:
            ret["part"] = part
            ret.update(xilinx_part.filter_vars(part))
        return ret
