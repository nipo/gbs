"""Xilinx ISE Pass definitions"""

from __future__ import annotations
from typing import Any
import re
from ...base import BasePass
from ...protocol import Dispatcher
from .dispatcher import IseDispatcher


class IseSynthesizePass(BasePass):
    """Pass that synthesizes HDL to Xilinx ISE bitstream

    This pass uses Xilinx ISE tools to:
    - Synthesize VHDL/Verilog to netlist (XST)
    - Build NGD from netlist and constraints (NGDBUILD)
    - Map to physical resources (MAP)
    - Place and route (PAR)
    - Check timing constraints (TRCE)
    - Generate bitstream (BITGEN)

    Input types: vhdl (verilog is optional, handled by dispatcher)
    Output types: ise-bitstream, ise-timing-report, ise-netlist
    """
    name = "ise-synthesize"
    input_types = {"vhdl", "verilog", "xilinx-ucf"}
    output_types = {"ise-bitstream", "ise-timing-report", "ise-netlist"}

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for ISE synthesis

        Sets target-usage=synthesis to allow conditional source filtering.

        Returns:
            Dictionary with filter variables
        """

        # Get device from backend config (populated from output group's target)
        target = self.config.get("target", {})
        part = target.get("part")
        if not part:
            raise ValueError("No target part")
        m = re.match(f"^(?P<name>xc[^-]+)(?P<speed>-[0-9])(?P<package>[a-z0-9]+)$", part)

        if not m:
            raise ValueError("Must supply target part in backend configuration")

        # Get vhdl_standard from config, default to "1993"
        vhdl_std = self.config.get("vhdl_standard", "1993")

        return {
            "target-usage": "synthesis",
            "vendor": "xilinx",
            "hwdep": "xilinx",
            "target_part": part,
            "part_name": m.group("name"),
            "part_speed": m.group("speed"),
            "target_speed": m.group("speed").lstrip("-"),
            "part_package": m.group("package"),
            "vhdl-version": vhdl_std,
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create ISE dispatcher for execution

        Args:
            context: Build context to pass to dispatcher

        Returns:
            IseDispatcher singleton
        """
        tool = self.config.get("tool", "ise")
        vhdl_std = self.config.get("vhdl_standard", "1993")
        target = self.config["target"]

        return [IseDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            target=target,
            tool = tool,
        )]
