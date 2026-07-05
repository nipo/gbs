"""Lattice Diamond Pass definitions"""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from ...protocol import Dispatcher
from .device import DiamondPart
from .dispatcher import DiamondDispatcher


class DiamondEcp5Pass(BasePass):
    """Pass that synthesizes HDL to an ECP5 bitstream with Lattice Diamond

    This pass drives the diamondc Tcl console to:
    - Create a Diamond project (LSE or Synplify synthesis engine)
    - Synthesize VHDL/Verilog and translate to NGD
    - Map, place and route (including trace timing analysis)
    - Generate the bitstream

    Input types: vhdl (verilog and ecp5-lpf are optional, handled by
    the dispatcher)
    Output types: ecp5-bitstream, diamond-synthesis-report,
    diamond-pnr-report
    """
    name = "diamond-ecp5"
    input_types = {"vhdl", "verilog", "ecp5-lpf"}
    output_types = {"ecp5-bitstream", "diamond-synthesis-report", "diamond-pnr-report"}

    def __init__(self,
                 part: DiamondPart,
                 config: dict[str, Any],
                 project_config: dict[str, Any] | None = None,
                 gbs_config: 'GBSConfig | None' = None):
        super().__init__(config, project_config, gbs_config)

        self.part = part
        self.vhdl_std = self.config.get("vhdl_standard", "1993")
        self.synthesis = self.config.get("synthesis", "lse")
        self.strategy = self.config.get("strategy", {})
        self._tool_name = self.resolve_tool_identifier("diamond")

        if self.vhdl_std not in ("1993", "2008"):
            raise ValueError(f"Diamond supports VHDL-1993 and VHDL-2008, not {self.vhdl_std}")
        if self.synthesis not in ("lse", "synplify"):
            raise ValueError(f"Diamond synthesis engine must be lse or synplify, not {self.synthesis}")

    def filter_vars(self) -> dict[str, Any]:
        """Contribute canonical filter variables for a Diamond build.

        The synthesis engine (LSE or Synplify) also drives the VHDL
        frontend; Diamond then runs its own PnR and bitstream stages.
        """
        return {
            "purpose": "synthesis",
            "vendor": "lattice",
            "family": self.part.family,
            "part": self.part.part,
            "die": self.part.device,
            "speed": self.part.speed_grade,
            "package": self.part.package_code,
            "vhdl_frontend": self.synthesis,
            "verilog_frontend": self.synthesis,
            "synthesis_engine": self.synthesis,
            "pnr_engine": "diamond",
            "bitstream_engine": "diamond",
            "vhdl_std": self.vhdl_std,
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create Diamond dispatcher for execution

        Args:
            context: Build context to pass to dispatcher

        Returns:
            DiamondDispatcher singleton
        """
        return [DiamondDispatcher(
            context=context,
            tool_name=self._tool_name,
            part=self.part,
            synthesis=self.synthesis,
            vhdl_std=self.vhdl_std,
            strategy=self.strategy,
        )]
