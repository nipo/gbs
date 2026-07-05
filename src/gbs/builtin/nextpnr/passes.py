"""nextpnr Pass definitions"""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from .dispatcher import NextpnrDispatcher
from ...protocol import Dispatcher


class NextpnrBasePass(BasePass):
    """Base pass for nextpnr place-and-route

    Provides common functionality for all nextpnr target passes.
    Subclasses should define:
    - name: Pass name
    - target: Target FPGA family (e.g., "ice40", "ecp5")
    - input_types: Expected input file types
    - output_types: Generated output file types
    - default_tool: Default tool name
    """

    # To be overridden by subclasses
    target: str = None
    default_tool: str = None
    # Prefix on the lowercased target part that this pass supports.
    part_prefix: tuple[str, ...] = ()

    def probe(self) -> str | None:
        if self.part_prefix:
            target = self.config.get("target") or {}
            part = (target.get("part") or "").lower()
            if not any(part.startswith(p) for p in self.part_prefix):
                return (
                    f"target part {part!r} not in this pass's family "
                    f"({'/'.join(self.part_prefix)})"
                )
        return self.probe_tool(self.default_tool)

    def filter_vars(self) -> dict[str, Any]:
        """Contribute canonical filter variables for nextpnr PnR."""
        return {
            "purpose": "synthesis",
            "pnr_engine": "nextpnr",
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create nextpnr dispatcher

        Args:
            context: Build context to pass to dispatcher

        Returns:
            NextpnrDispatcher singleton
        """
        nextpnr_tool = self.resolve_tool_identifier(self.default_tool)
        target = self.config.get("target", {})
        part = target["part"]
        package = target["package"]

        return [NextpnrDispatcher(
            context=context,
            target=self.target,
            nextpnr_tool=nextpnr_tool,
            part=part,
            package=package,
        )]


class NextpnrIce40Pass(NextpnrBasePass):
    """Pass that performs place-and-route for Ice40 using nextpnr

    This pass takes a Yosys-generated JSON netlist and:
    - Places and routes the design for Ice40 FPGA
    - Applies timing constraints (if provided)
    - Generates ASCII bitstream (.asc)

    Input types: ice40-netlist-json, ice40-pcf
    Output types: ice40-asc
    """
    name = "nextpnr-ice40"
    target = "ice40"
    default_tool = "nextpnr-ice40"
    part_prefix = ("ice40", "ice5", "up5k")
    input_types = {"ice40-netlist-json", "ice40-pcf"}
    output_types = {"ice40-asc", "nextpnr-pnr-report"}


class NextpnrEcp5Pass(NextpnrBasePass):
    """Pass that performs place-and-route for ECP5 using nextpnr

    This pass takes a Yosys-generated JSON netlist and:
    - Places and routes the design for ECP5 FPGA
    - Applies timing constraints (if provided)
    - Generates text config file (.config)

    Input types: ecp5-netlist-json, ecp5-lpf
    Output types: ecp5-config
    """
    name = "nextpnr-ecp5"
    target = "ecp5"
    default_tool = "nextpnr-ecp5"
    part_prefix = ("lfe5", "lae5")
    input_types = {"ecp5-netlist-json", "ecp5-lpf"}
    output_types = {"ecp5-config", "nextpnr-pnr-report"}


class NextpnrXilinxPass(NextpnrBasePass):
    """Pass that performs place-and-route for Xilinx Series-7 using
    nextpnr-xilinx (openxc7 fork).

    Input types: xilinx-netlist-json, xilinx-xdc
    Output types: nextpnr-fasm

    The chipdb binary is resolved by the dispatcher from the tool's
    `chipdb_root` config key (populated by the apio provider from an
    openxc7 install).
    """
    name = "nextpnr-xilinx"
    target = "xilinx"
    default_tool = "nextpnr-xilinx"
    part_prefix = ("xc7",)
    input_types = {"xilinx-netlist-json", "xilinx-xdc"}
    output_types = {"nextpnr-fasm", "nextpnr-pnr-report"}

    def filter_vars(self) -> dict[str, Any]:
        from .. import xilinx_part
        ret = super().filter_vars()
        ret["vendor"] = "xilinx"
        target = self.config.get("target", {})
        part = target.get("part")
        if part:
            ret["part"] = part
            ret.update(xilinx_part.filter_vars(part))
        return ret

    def dispatchers(self, context) -> list[Dispatcher]:
        """Xilinx target has no `package` field: the chipdb encodes it."""
        nextpnr_tool = self.resolve_tool_identifier(self.default_tool)
        target = self.config.get("target", {})
        part = target["part"]
        return [NextpnrDispatcher(
            context=context,
            target=self.target,
            nextpnr_tool=nextpnr_tool,
            part=part,
            package="",
        )]
