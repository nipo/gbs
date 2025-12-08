"""GHDL Pass definitions"""

from __future__ import annotations
from typing import Any

from ...planner.passes import Pass
from .dispatcher import GHDLDispatcher
from ...backend.dispatcher import Dispatcher

class GHDLSimulatePass(Pass):
    """Pass that compiles VHDL designs and creates a simulator executable

    This pass uses GHDL to:
    - Import and analyze VHDL sources by library
    - Elaborate the top-level entity
    - Generate a simulator executable

    Input types: vhdl
    Output types: ghdl-simulator
    """
    name = "ghdl-simulate"
    input_types = {"vhdl", "ghdl-vhpidirect-c"}
    output_types = {"ghdl-simulator"}

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for GHDL simulation

        Sets target-usage=simulation to allow conditional source filtering.
        Also provides ghdl-backend and vhdl-version for backend-specific filtering.

        Returns:
            Dictionary with filter variables
        """
        # Get vhdl_standard from config, default to "1993"
        vhdl_std = self.config.get("vhdl_standard", "1993")

        return {
            "target-usage": "simulation",
            "compiler": "ghdl",
            "vhdl-version": vhdl_std,
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create GHDL dispatcher for execution

        Args:
            context: Build context to pass to dispatcher

        Returns:
            GHDLDispatcher singleton
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")
        ghdl_tool = self.config.get("ghdl_tool", "ghdl")

        return [GHDLDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            ghdl_tool=ghdl_tool
        )]
