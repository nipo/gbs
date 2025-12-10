"""GHDL Pass definitions"""

from __future__ import annotations
from typing import Any

from ...planner.passes import Pass
from .dispatcher import GHDLAnalyzeDispatcher, GHDLSimulateDispatcher
from ...backend.dispatcher import Dispatcher


class GHDLAnalyzePass(Pass):
    """Pass that analyzes VHDL sources into GHDL library intermediates

    This pass uses GHDL to analyze VHDL sources (ghdl -i/-a) and
    produces .cf library intermediate files. These can be consumed by:
    - GHDLSimulatePass for simulation
    - Yosys+GHDL for synthesis
    - Other tools that can use GHDL libraries

    Input types: vhdl
    Output types: ghdl-cf
    """
    name = "ghdl-analyze"
    input_types = {"vhdl"}
    output_types = {"ghdl-cf"}

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for GHDL analysis

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
        """Create GHDL analysis dispatcher

        Args:
            context: Build context to pass to dispatcher

        Returns:
            GHDLAnalyzeDispatcher singleton
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")
        ghdl_tool = self.config.get("ghdl_tool", "ghdl")

        return [GHDLAnalyzeDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            ghdl_tool=ghdl_tool
        )]


class GHDLSimulatePass(Pass):
    """Pass that creates a simulator executable from GHDL libraries

    This pass takes GHDL library intermediates (.cf files) and:
    - Compiles VHPIDIRECT C sources (simulation-time C interface)
    - Elaborates the top-level entity
    - Generates a simulator executable

    Input types: ghdl-cf, ghdl-vhpidirect-c
    Output types: ghdl-simulator
    """
    name = "ghdl-simulate"
    input_types = {"ghdl-cf", "ghdl-vhpidirect-c"}
    output_types = {"ghdl-simulator"}

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for GHDL simulation

        Sets target-usage=simulation to allow conditional source filtering.

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
        """Create GHDL simulation dispatcher

        Args:
            context: Build context to pass to dispatcher

        Returns:
            GHDLSimulateDispatcher singleton
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")
        ghdl_tool = self.config.get("ghdl_tool", "ghdl")

        return [GHDLSimulateDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            ghdl_tool=ghdl_tool
        )]
