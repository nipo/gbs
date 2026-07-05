"""Xilinx ISE Pass definitions"""

from __future__ import annotations
from typing import Any
from ...base import BasePass
from ...protocol import Dispatcher
from .. import xilinx_part
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
    output_types = {"ise-bitstream", "ise-timing-report", "ise-netlist",
                     "ise-synthesis-report", "ise-pnr-report"}

    # Prefixes ISE cannot handle (7-series onwards, UltraScale,
    # UltraScale+, Versal). Anything else that starts with "xc" is
    # assumed to be Spartan-6/Virtex-6 or earlier.
    _POST_ISE_PREFIXES = (
        "xc7", "xcku", "xcvu", "xcau", "xczu",
        "xcvm", "xcvp", "xcve",
    )

    def probe(self) -> str | None:
        target = self.config.get("target") or {}
        part = (target.get("part") or "").lower()
        if not part.startswith("xc"):
            return f"target part {part!r} is not a Xilinx device"
        if any(part.startswith(p) for p in self._POST_ISE_PREFIXES):
            return (
                f"target part {part!r} is 7-series or later; "
                f"ISE only handles pre-7-series"
            )
        return self.probe_tool("ise")

    def filter_vars(self) -> dict[str, Any]:
        """Contribute canonical filter variables for an ISE build."""
        target = self.config.get("target", {})
        part = target.get("part")
        if not part:
            raise ValueError("No target part")
        if not xilinx_part.parse_part(part):
            raise ValueError(
                f"Cannot parse ISE target part {part!r}, "
                f"expected <die><-speed><package>"
            )

        vhdl_std = self.config.get("vhdl_standard", "1993")

        ret: dict[str, Any] = {
            "purpose": "synthesis",
            "vendor": "xilinx",
            "vhdl_frontend": "ise",
            "verilog_frontend": "ise",
            "synthesis_engine": "ise",
            "pnr_engine": "ise",
            "bitstream_engine": "ise",
            "vhdl_std": vhdl_std,
            "part": part,
        }
        ret.update(xilinx_part.filter_vars(part))
        return ret

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create ISE dispatcher for execution

        Args:
            context: Build context to pass to dispatcher

        Returns:
            IseDispatcher singleton
        """
        tool = self.resolve_tool_identifier("ise")
        vhdl_std = self.config.get("vhdl_standard", "1993")
        target = self.config["target"]

        return [IseDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            target=target,
            tool = tool,
        )]
