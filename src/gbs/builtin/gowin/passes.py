"""Gowin Pass definitions"""

from __future__ import annotations
from typing import Any
from pathlib import Path
import csv

from ...planner.passes import Pass
from ...backend.dispatcher import Dispatcher
from .dispatcher import GowinDispatcher

class GowinSynthesizePass(Pass):
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
    output_types = {"gowin-fs", "gowin-netlist"}

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
        }

        # Get device from backend config (populated from output group's target)
        target = self.config.get("target", {})
        device = target.get("part")
        if not device:
            return filter_vars

        filter_vars["target_part"] = device

        # Look up device in Gowin CSV if gbs_config provides tool path
        if self.gbs_config:
            gowin_tool = self.config.get("gowin_tool", "gowin")
            tool_config = self.gbs_config.get_tool(gowin_tool)
            if tool_config and tool_config.config.get("path"):
                gowin_path = Path(tool_config.config["path"])
                csv_path = gowin_path / "IDE" / "data" / "device" / "device_info.csv"

                if csv_path.exists():
                    try:
                        with open(csv_path, 'r', encoding='utf-8') as f:
                            reader = csv.reader(f)
                            for row in reader:
                                if len(row) < 4:
                                    continue
                                if row[1].strip() == device.strip():
                                    filter_vars["target_part_name"] = row[3].strip()
                                    break
                    except Exception:
                        pass  # Silently ignore CSV parsing errors during planning

        return filter_vars

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create Gowin dispatcher for execution

        Args:
            context: Build context to pass to dispatcher

        Returns:
            GowinDispatcher instance
        """
        gowin_tool = self.config.get("gowin_tool", "gowin")

        return [GowinDispatcher(
            context=context,
            gowin_tool=gowin_tool,
        )]
