"""Vivado Pass definitions"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...base import BasePass
from ...protocol import Dispatcher
from ...utils import expand_path
from .dispatcher import VivadoDispatcher


class VivadoSynthesizePass(BasePass):
    """Pass that synthesizes HDL to Xilinx FPGA outputs

    This pass uses Vivado tools in non-project mode to:
    - Create an in-memory project with HDL sources
    - Run synthesis, optimization, placement, and routing
    - Generate bitstream and various reports

    Input types:
        - vhdl: VHDL source files
        - verilog: Verilog source files
        - xilinx-xci: Xilinx IP core files
        - xilinx-xdc: Xilinx Design Constraints
        - xilinx-constraints-tcl: TCL-based constraints
        - vivado-block-design: Vivado block design files
        - vivado-init-tcl: TCL scripts to run at project init

    Output types:
        - vivado-routing-report: Route status report
        - vivado-timing-report: Timing summary report
        - vivado-power-report: Power estimation report
        - vivado-usage-report: Resource utilization report
        - vivado-netlist-edif: Post-implementation EDIF netlist
        - vivado-drc-report: Design Rule Check report
        - vivado-bitstream: Final bitstream file
    """
    name = "vivado-synthesize"
    input_types = {
        "vhdl",
        "verilog",
        "xilinx-xci",
        "xilinx-xdc",
        "xilinx-constraints-tcl",
        "vivado-block-design",
        "vivado-init-tcl",
    }
    output_types = {
        "vivado-routing-report",
        "vivado-timing-report",
        "vivado-power-report",
        "vivado-usage-report",
        "vivado-netlist-edif",
        "vivado-drc-report",
        "vivado-bitstream",
        "vivado-synthesis-report",
        "vivado-pnr-report",
    }

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for Vivado synthesis

        Sets target-usage=synthesis to allow conditional source filtering.

        Returns:
            Dictionary with filter variables
        """
        # Get vhdl_standard from config, default to "1993"
        vhdl_std = self.config.get("vhdl_standard", "1993")

        filter_vars = {
            "target-usage": "synthesis",
            "vendor": "xilinx",
            "hwdep": "xilinx",
            "vhdl-version": vhdl_std,
        }

        # Get device from backend config (populated from output group's target)
        target = self.config.get("target", {})
        device = target.get("part")
        if device:
            filter_vars["target_part"] = device

            # Extract family from part name (e.g., "xc7a35tcsg324-1" -> "artix7")
            part_family = self._extract_family(device)
            if part_family:
                filter_vars["target_part_name"] = part_family

        return filter_vars

    def _extract_family(self, part: str) -> str | None:
        """Extract FPGA family from part number

        Args:
            part: Xilinx part number (e.g., "xc7a35tcsg324-1")

        Returns:
            Family name (e.g., "artix7") or None
        """
        part_lower = part.lower()

        # 7-series
        if part_lower.startswith("xc7a"):
            return "artix7"
        elif part_lower.startswith("xc7k"):
            return "kintex7"
        elif part_lower.startswith("xc7v"):
            return "virtex7"
        elif part_lower.startswith("xc7z"):
            return "zynq7"
        elif part_lower.startswith("xc7s"):
            return "spartan7"

        # UltraScale
        elif part_lower.startswith("xcku"):
            return "kintexu"
        elif part_lower.startswith("xcvu"):
            return "virtexu"

        # UltraScale+
        elif part_lower.startswith("xczu"):
            return "zynquplus"
        elif part_lower.startswith("xcau"):
            return "artixuplus"
        elif part_lower.startswith("xckup"):
            return "kintexuplus"
        elif part_lower.startswith("xcvup"):
            return "virtexuplus"

        # Versal
        elif part_lower.startswith("xcvm"):
            return "versal"
        elif part_lower.startswith("xcvp"):
            return "versal"
        elif part_lower.startswith("xcve"):
            return "versal"

        return None

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create Vivado dispatcher for execution

        Args:
            context: Build context to pass to dispatcher

        Returns:
            VivadoDispatcher instance
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")
        vivado_tool = self.config.get("tool", "vivado")
        target = self.config.get("target", {})

        return [VivadoDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            target=target,
            vivado_tool=vivado_tool,
        )]
