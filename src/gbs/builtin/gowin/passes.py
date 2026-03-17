"""Gowin Pass definitions"""

from __future__ import annotations
from typing import Any
from pathlib import Path
import csv

from ...base import BasePass
from ...protocol import Dispatcher
from .dispatcher import GowinDispatcher

class GowinSynthesizePass(BasePass):
    """Pass that synthesizes HDL to Gowin FPGA bitstream

    This pass uses Gowin EDA tools (via gw_sh) to:
    - Synthesize VHDL/Verilog to netlist
    - Aggregate constraints from multiple sources (optional)
    - Convert SerDes TOML config to CSR (for 5-series with SerDes)
    - Run place & route
    - Generate bitstream

    Input types: vhdl (verilog and gowin-cst are optional, handled by dispatcher)
    Output types: gowin-fs (bitstream), gowin-netlist

    Note: This pass lists only vhdl as input type for planning purposes.
    The dispatcher can also handle verilog sources and gowin-cst constraints,
    but they are optional and don't need to be present for planning.
    """
    name = "gowin-synthesize"
    input_types = {"vhdl", "verilog", "gowin-cst", "gowin-sdc", "gowin-serdes-config"}
    output_types = {"gowin-fs", "gowin-netlist", "gowin-synthesis-report", "gowin-pnr-report"}

    def __init__(self,
                 config: dict[str, Any],
                 project_config: dict[str, Any] | None = None,
                 gbs_config: 'GBSConfig | None' = None):
        super().__init__(config, project_config, gbs_config)

        self.vhdl_std = self.config.get("vhdl_standard", "1993")
        self.device = self.config.get("target", {}).get("part")
        self.gowin_path = None
        self.device_info = None
        self.tool_config = None
        self.tool_name = self.config.get("gowin_tool", "gowin")

        if self.gbs_config:
            self.tool_config = self.gbs_config.get_tool(self.tool_name)
            if self.tool_config and "path" in self.tool_config.config:
                from ...utils import expand_path
                self.gowin_path = expand_path(self.tool_config.config["path"])

        if self.device and self.gowin_path:
            from .device_info import get_device_info
            self.device_info = get_device_info(self.gowin_path, self.device)

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for Gowin synthesis

        Sets target-usage=synthesis to allow conditional source filtering.
        Also provides device characteristics if device is configured.

        Returns:
            Dictionary with filter variables
        """
        filter_vars = {
            "target-usage": "synthesis",
            "vendor": "gowin",
            "hwdep": "gowin",
            "vhdl-version": self.vhdl_std,
        }

        if self.device_info:
            filter_vars["target_part"] = self.device_info.part
            filter_vars["target_part_name"] = self.device_info.family

        return filter_vars

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create Gowin dispatcher for execution

        Args:
            context: Build context to pass to dispatcher

        Returns:
            GowinDispatcher instance
        """
        return [GowinDispatcher(
            context=context,
            vhdl_std = self.vhdl_std,
            tool_name = self.tool_name,
            tool_config = self.tool_config,
            device_info = self.device_info,
        )]
