"""NVC Pass definitions"""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from .dispatcher import NVCDispatcher
from ...protocol import Dispatcher

class NVCSimulatePass(BasePass):
    """Pass that compiles VHDL designs and creates a simulator executable

    This pass uses NVC to:
    - Analyze VHDL sources by library
    - Elaborate the top-level entity
    - Generate a simulator executable

    Input types: vhdl
    Output types: nvc-simulator
    """
    name = "nvc-simulate"
    input_types = {"vhdl"}
    output_types = {"simulator", "nvc-simulator"}

    def probe(self) -> str | None:
        return self.probe_tool("nvc")

    def filter_vars(self) -> dict[str, Any]:
        """Contribute canonical filter variables for NVC."""
        vhdl_std = self.config.get("vhdl_standard", "1993")

        return {
            "purpose": "simulation",
            "vhdl_frontend": "nvc",
            "simulation_engine": "nvc",
            "vhdl_std": vhdl_std,
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create NVC dispatcher for execution

        Args:
            context: Build context to pass to dispatcher

        Returns:
            NVCDispatcher singleton
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")
        nvc_tool = self.resolve_tool_identifier("nvc")

        return [NVCDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            nvc_tool=nvc_tool
        )]
