"""GHDL Pass definitions"""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from .dispatcher import GHDLAnalyzeDispatcher, GHDLSimulateDispatcher, GHDLRunDispatcher
from ...protocol import Dispatcher


class GHDLAnalyzePass(BasePass):
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
        tool_name = self.config.get("ghdl_tool", "ghdl")

        return [GHDLAnalyzeDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            tool_name=tool_name
        )]


class GHDLSimulatePass(BasePass):
    """Pass that creates a simulator executable from GHDL libraries

    This pass takes GHDL library intermediates (.cf files) and:
    - Compiles VHPIDIRECT C sources (simulation-time C interface, if present)
    - Elaborates the top-level entity
    - Generates a simulator executable

    Input types: ghdl-cf
    Output types: ghdl-simulator

    Note: ghdl-vhpidirect-c is handled opportunistically by the dispatcher if present
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
        tool_name = self.config.get("ghdl_tool", "ghdl")

        return [GHDLSimulateDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            tool_name=tool_name
        )]


class GHDLRunPass(BasePass):
    """Pass that runs GHDL simulator to produce waveforms and simulation logs

    This pass takes a ghdl-simulator executable and runs it to produce:
    - Waveform files (VCD, GHW, or FST format)
    - Simulation logs (stdout/stderr)

    Input types: ghdl-simulator
    Output types: waveform-vcd, waveform-ghw, waveform-fst, simulation-log

    Configuration options (from backend_config):
        - max_simulation_time: Maximum simulation time (e.g., "100 ms", "1 us")
        - success_regex: Optional regex pattern that must appear in logs for success
    """
    name = "ghdl-run"
    input_types = {"ghdl-simulator"}
    output_types = {"waveform-vcd", "waveform-ghw", "waveform-fst", "simulation-log", "simulation-success"}

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create GHDL run dispatcher

        Args:
            context: Build context to pass to dispatcher

        Returns:
            GHDLRunDispatcher singleton
        """
        tool_name = self.config.get("ghdl_tool", "ghdl")
        return [GHDLRunDispatcher(context=context,
                                  tool_name = tool_name,
                                  config = self.config)]
