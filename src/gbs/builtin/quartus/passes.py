"""Altera Quartus Pass definitions"""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from ...protocol import Dispatcher
from .dispatcher import QuartusDispatcher


class QuartusSynthesizePass(BasePass):
    """Pass that synthesizes HDL to Altera FPGA bitstream

    This pass uses Quartus Prime tools to:
    - Generate project files (.qpf, .qsf)
    - Run Analysis & Synthesis (quartus_map)
    - Run Fitter / Place & Route (quartus_fit)
    - Run Timing Analysis (quartus_sta)
    - Run Assembler / Generate bitstream (quartus_asm)

    Input types: vhdl, verilog, quartus-sdc, quartus-pin-assignment
    Output types: quartus-sof, quartus-synthesis-report, quartus-pnr-report
    """
    name = "quartus-synthesize"
    input_types = {"vhdl", "verilog", "quartus-sdc", "quartus-pin-assignment"}
    output_types = {
        "quartus-sof",
        "quartus-synthesis-report",
        "quartus-pnr-report",
    }

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for Quartus synthesis"""
        target = self.config.get("target", {})
        part = target.get("part")

        vhdl_std = self.config.get("vhdl_standard", "1993")

        filter_vars = {
            "target-usage": "synthesis",
            "vendor": "altera",
            "hwdep": "altera",
            "vhdl-version": vhdl_std,
        }

        if part:
            filter_vars["target_part"] = part
            family = _extract_family(part)
            if family:
                filter_vars["target_part_name"] = family

        return filter_vars

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create Quartus dispatcher for execution"""
        tool = self.config.get("tool", "quartus")
        vhdl_std = self.config.get("vhdl_standard", "1993")
        target = self.config["target"]

        return [QuartusDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            target=target,
            tool=tool,
        )]


def _extract_family(part: str) -> str | None:
    """Extract FPGA family from Altera part number"""
    part_upper = part.upper()

    # Cyclone 10 LP: 10CL...
    if part_upper.startswith("10CL"):
        return "cyclone10lp"
    # Cyclone 10 GX: 10CX...
    elif part_upper.startswith("10CX"):
        return "cyclone10gx"
    # Cyclone V: 5CS..., 5CE..., 5CG...
    elif part_upper.startswith("5CS") or part_upper.startswith("5CE") or part_upper.startswith("5CG"):
        return "cyclonev"
    # Cyclone IV E: EP4CE...
    elif part_upper.startswith("EP4CE"):
        return "cycloneive"
    # Cyclone IV GX: EP4CGX...
    elif part_upper.startswith("EP4CGX"):
        return "cycloneivgx"
    # MAX 10: 10M...
    elif part_upper.startswith("10M"):
        return "max10"
    # Agilex 5: AGF...
    elif part_upper.startswith("AGF") or part_upper.startswith("A5"):
        return "agilex5"
    # Agilex 7: AGI..., AGM...
    elif part_upper.startswith("AGI") or part_upper.startswith("AGM"):
        return "agilex7"
    # Stratix 10: 1S...
    elif part_upper.startswith("1S"):
        return "stratix10"
    # Arria 10: 10A...
    elif part_upper.startswith("10A"):
        return "arria10"

    return None
