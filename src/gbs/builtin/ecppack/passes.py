"""ecppack Pass definitions"""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from .dispatcher import EcppackDispatcher
from ...protocol import Dispatcher


class EcppackPass(BasePass):
    """Pass that generates binary bitstream for ECP5

    This pass takes a text config (.config) and:
    - Converts it to binary format (.bit)
    - Ready for programming to ECP5 FPGA

    Input types: ecp5-config
    Output types: ecp5-bit, ecp5-bitstream
    """
    name = "ecppack"
    input_types = {"ecp5-config"}
    output_types = {"ecp5-bit", "ecp5-bitstream"}

    def filter_vars(self) -> dict[str, Any]:
        """Contribute canonical filter variables for ecppack."""
        return {
            "purpose": "synthesis",
            "vendor": "lattice",
            "family": "ecp5",
            "bitstream_engine": "ecppack",
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create ecppack dispatcher

        Args:
            context: Build context to pass to dispatcher

        Returns:
            EcppackDispatcher singleton
        """
        ecppack_tool = self.resolve_tool_identifier("ecppack")

        return [EcppackDispatcher(
            context=context,
            ecppack_tool=ecppack_tool,
        )]
