"""Xilinx ISE Pass definitions"""

from __future__ import annotations
from typing import Any
import re
from ...planner.passes import Pass


class IseSynthesizePass(Pass):
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

    def contribute_filter_vars(self, config: dict[str, Any]) -> dict[str, Any]:
        """Contribute filter variables for ISE synthesis

        Sets target-usage=synthesis to allow conditional source filtering.

        Args:
            config: Backend configuration dict

        Returns:
            Dictionary with filter variables
        """
        
        target = config.get("target", {})
        part = target.get("part")
        if not part:
            raise ValueError("No target part")
        m = re.match(f"^(?P<name>xc[^-]+)(?P<speed>-[0-9])(?P<package>[a-z0-9]+)$", part)

        if not m:
            raise ValueError("Must supply target part in backend configuration")

        return {
            "target-usage": "synthesis",
            "vendor": "xilinx",
            "hwdep": "xilinx",
            "target_part": part,
            "part_name": m.group("name"),
            "part_speed": m.group("speed"),
            "part_package": m.group("package"),
        }
