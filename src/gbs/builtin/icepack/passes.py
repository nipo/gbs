"""icepack Pass definitions"""

from __future__ import annotations
from typing import Any

from ...planner.passes import Pass
from .dispatcher import IcepackDispatcher
from ...backend.dispatcher import Dispatcher


class IcepackPass(Pass):
    """Pass that generates binary bitstream for Ice40

    This pass takes an ASCII bitstream (.asc) and:
    - Converts it to binary format (.bin)
    - Ready for programming to Ice40 FPGA

    Input types: ice40-asc
    Output types: ice40-bin
    """
    name = "icepack"
    input_types = {"ice40-asc"}
    output_types = {"ice40-bin", "ice40-bitstream"}

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for icepack

        Returns:
            Dictionary with filter variables
        """
        return {
            "target-usage": "bitstream",
            "target": "ice40",
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create icepack dispatcher

        Args:
            context: Build context to pass to dispatcher

        Returns:
            IcepackDispatcher singleton
        """
        icepack_tool = self.config.get("icepack_tool", "icepack")

        return [IcepackDispatcher(
            context=context,
            icepack_tool=icepack_tool,
        )]
