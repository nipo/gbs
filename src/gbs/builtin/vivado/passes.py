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
        "vivado-ip-zip",
        "vivado-ip-repository",
        "vivado-bus-definition",
    }
    output_types = {
        "vivado-routing-report",
        "vivado-timing-report",
        "vivado-power-report",
        "vivado-usage-report",
        "vivado-netlist-edif",
        "vivado-drc-report",
        # Canonical shared names + the legacy aliases planners have
        # been trained on. Passing both keeps existing project files
        # working; new projects should use the canonical form.
        "bitstream",           "vivado-bitstream",
        "synthesis-report",    "vivado-synthesis-report",
        "pnr-report",          "vivado-pnr-report",
    }

    def probe(self) -> str | None:
        target = self.config.get("target") or {}
        part = (target.get("part") or "").lower()
        if not part.startswith("xc"):
            return f"target part {part!r} is not a Xilinx device"
        if part.startswith("xc6") or part.startswith("xc5") or part.startswith("xc4") or part.startswith("xc3"):
            return f"target part {part!r} is pre-7-series; Vivado only handles 7-series and later"
        return self.probe_tool("vivado")

    def filter_vars(self) -> dict[str, Any]:
        """Contribute canonical filter variables for a Vivado build.

        Vivado runs synthesis, place-and-route and bitstream generation
        in a single pass, so all three engine variables are set.
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")

        filter_vars: dict[str, Any] = {
            "purpose": "synthesis",
            "vendor": "xilinx",
            "vhdl_frontend": "vivado",
            "verilog_frontend": "vivado",
            "synthesis_engine": "vivado",
            "pnr_engine": "vivado",
            "bitstream_engine": "vivado",
            "vhdl_std": vhdl_std,
        }

        from .. import xilinx_part
        target = self.config.get("target", {})
        device = target.get("part")
        if device:
            filter_vars["part"] = device
            filter_vars.update(xilinx_part.filter_vars(device))
            if not xilinx_part.parse_part(device):
                import logging
                logger = logging.getLogger("gbs.builtin.vivado.passes")
                logger.warning(
                    f"Cannot parse device <{device}>, should be "
                    f"<part><-speed><package>"
                )

        return filter_vars

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create Vivado dispatcher for execution

        Args:
            context: Build context to pass to dispatcher

        Returns:
            VivadoDispatcher instance
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")
        vivado_tool = self.resolve_tool_identifier("vivado")
        target = self.config.get("target", {})

        return [VivadoDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            target=target,
            vivado_tool=vivado_tool,
        )]
