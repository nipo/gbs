"""nextpnr Pass definitions"""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from .dispatcher import NextpnrDispatcher
from ...protocol import Dispatcher


class NextpnrIce40Pass(BasePass):
    """Pass that performs place-and-route for Ice40 using nextpnr

    This pass takes a Yosys-generated JSON netlist and:
    - Places and routes the design for Ice40 FPGA
    - Applies timing constraints (if provided)
    - Generates ASCII bitstream (.asc)

    Input types: ice40-netlist-json
    Output types: ice40-asc
    """
    name = "nextpnr-ice40"
    input_types = {"ice40-netlist-json", "ice40-pcf"}
    output_types = {"ice40-asc"}

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for nextpnr

        Returns:
            Dictionary with filter variables
        """
        return {
            "target-usage": "pnr",
            "target": "ice40",
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create nextpnr dispatcher

        Args:
            context: Build context to pass to dispatcher

        Returns:
            NextpnrDispatcher singleton
        """
        nextpnr_tool = self.config.get("nextpnr_tool", "nextpnr-ice40")
        part = self.config["part"]
        package = self.config["package"]

        return [NextpnrDispatcher(
            context=context,
            nextpnr_tool=nextpnr_tool,
            part=part,
            package=package,
        )]
