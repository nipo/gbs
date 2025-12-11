"""QuestaSim/ModelSim Pass definitions"""

from __future__ import annotations
from typing import Any

from ...planner.passes import Pass
from ...backend.dispatcher import Dispatcher
from .dispatcher import QuestaDispatcher


class QuestaSimulatePass(Pass):
    """Pass that creates a QuestaSim/ModelSim simulator

    This pass generates:
    1. A TCL batch script that compiles and runs simulation
    2. A shell script wrapper that invokes vsim with the batch script

    Input types:
        - vhdl: VHDL source files
        - verilog: Verilog source files

    Output types:
        - questa-batch-script: TCL batch script for compilation and simulation
        - questa-simulator: Shell script wrapper for running simulation
    """
    name = "questa-simulate"
    input_types = {"vhdl", "verilog"}
    output_types = {"questa-batch-script", "questa-simulator"}

    def filter_vars(self) -> dict[str, Any]:
        """Contribute filter variables for QuestaSim simulation

        Returns:
            Dictionary with filter variables
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")

        return {
            "target-usage": "simulation",
            "compiler": "questa",
            "vhdl-version": vhdl_std,
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create QuestaSim dispatcher

        Args:
            context: Build context to pass to dispatcher

        Returns:
            QuestaDispatcher instance
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")
        questa_tool = self.config.get("questa_tool", "questa")

        return [QuestaDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            questa_tool=questa_tool,
        )]
