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
    Output types: gowin-fs (bitstream), gowin-bin (binary bitstream), gowin-netlist

    Note: This pass lists only vhdl as input type for planning purposes.
    The dispatcher can also handle verilog sources and gowin-cst constraints,
    but they are optional and don't need to be present for planning.
    """
    name = "gowin-synthesize"
    input_types = {"vhdl", "verilog", "gowin-cst", "gowin-sdc", "gowin-serdes-config"}
    output_types = {
        "gowin-netlist",
        "bitstream",        "gowin-fs", "gowin-bin",
        "synthesis-report", "gowin-synthesis-report",
        "pnr-report",       "gowin-pnr-report",
        "timing-summary",
    }

    def __init__(self,
                 config: dict[str, Any],
                 project_config: dict[str, Any] | None = None,
                 gbs_config: 'GBSConfig | None' = None):
        super().__init__(config, project_config, gbs_config)

        self.vhdl_std = self.config.get("vhdl_standard", "1993")
        self.device = self.config.get("target", {}).get("part")
        self.gowin_path = None
        self.device_info = None
        self._tool_name = self.resolve_tool_identifier("gowin")

        if self.gbs_config:
            _tool_config = self.gbs_config.get_tool(self._tool_name)
            if _tool_config and "path" in _tool_config.config:
                from ...utils import expand_path
                self.gowin_path = expand_path(_tool_config.config["path"])

        # Resolved at construction, so a pass re-created without being
        # probed is as complete as a probed one. A part Gowin's device
        # CSV rejects is reported by probe().
        self.__device_error = None
        if (self.device or "").lower().startswith("gw") and self.gowin_path is not None:
            from .device_info import get_device_info
            try:
                self.device_info = get_device_info(self.gowin_path, self.device)
            except ValueError as e:
                self.__device_error = str(e)

    def probe(self) -> str | None:
        part = (self.device or "").lower()
        if not part.startswith("gw"):
            return f"target part {self.device!r} is not a Gowin device"
        reason = self.probe_tool("gowin")
        if reason:
            return reason
        # The IDE-driven flow needs the install root (path). A tool
        # registered under the same name but exposing only `executable`
        # is a different tool (e.g. `gowin_pack`) that we cannot drive
        # this pass with — surface that with a specific message rather
        # than silently producing no bitstream downstream.
        if self.gowin_path is None:
            identifier = self.resolve_tool_identifier("gowin")
            return (
                f"tool {identifier!r} is configured but does not expose "
                f"a `path` config key (the Gowin install root). Pin the "
                f"correct variant with `tool: gowin:<variant>` if the "
                f"registered `gowin` refers to a different binary "
                f"(e.g. gowin_pack)."
            )
        # A part Gowin's own device CSV rejects is a probe rejection, so
        # the plan-failure diagnostic lists it alongside every other
        # backend's reason instead of crashing the whole planner.
        if self.__device_error is not None:
            return f"Gowin device CSV rejected part {self.device!r}: {self.__device_error}"
        return None

    def filter_vars(self) -> dict[str, Any]:
        """Contribute canonical filter variables for a Gowin build.

        Gowin IDE runs synthesis, place-and-route and bitstream
        generation as a single flow.
        """
        filter_vars: dict[str, Any] = {
            "purpose": "synthesis",
            "vendor": "gowin",
            "vhdl_frontend": "gowin_synth",
            "verilog_frontend": "gowin_synth",
            "synthesis_engine": "gowin_synth",
            "pnr_engine": "gowin_pnr",
            "bitstream_engine": "gowin_bit",
            "vhdl_std": self.vhdl_std,
        }

        if self.device_info:
            filter_vars["part"] = self.device_info.part
            filter_vars["die"] = self.device_info.part
            filter_vars["family"] = self.device_info.family

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
            tool_name = self._tool_name,
            device_info = self.device_info,
        )]
