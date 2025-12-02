"""GHDL Pass definitions"""

from __future__ import annotations
from typing import Any

from ...model.passes import Pass
from .dispatcher import GHDLDispatcher


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
    input_types = {"vhdl"}
    output_types = {"ghdl-simulator"}

    def contribute_filter_vars(self, config: dict[str, Any]) -> dict[str, Any]:
        """Contribute filter variables for GHDL simulation

        Sets target-usage=simulation to allow conditional source filtering.
        Also provides ghdl-backend and vhdl-version for backend-specific filtering.

        Args:
            config: Backend configuration dict with optional:
                - vhdl_standard: VHDL standard string (e.g., "93c", "08", "2008")
                - ghdl_tool: Tool identifier for lookup (default: "ghdl")

        Returns:
            Dictionary with filter variables
        """
        # Get vhdl_standard from config, default to "93c"
        vhdl_std = config.get("vhdl_standard", "93c")

        # Normalize VHDL version to four-digit year
        vhdl_version = GHDLDispatcher._normalize_vhdl_version(vhdl_std)

        return {
            "target-usage": "simulation",
            "compiler": "ghdl",
            "vhdl-version": vhdl_version,
        }
