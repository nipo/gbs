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

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for nextpnr

        Returns:
            Dictionary with filter variables
        """
        return {
            "target-usage": "pnr",
            "target": self.target,
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create nextpnr dispatcher

        Args:
            context: Build context to pass to dispatcher

        Returns:
            NextpnrDispatcher singleton
        """
        nextpnr_tool = self.config.get("tool", self.default_tool)
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
    input_types = {"ecp5-netlist-json", "ecp5-lpf"}
    output_types = {"ecp5-config", "nextpnr-pnr-report"}
