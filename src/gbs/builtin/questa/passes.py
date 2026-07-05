"""QuestaSim/ModelSim Pass definitions"""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from ...protocol import Dispatcher
from .dispatcher import QuestaDispatcher


class QuestaSimulatePass(BasePass):
    """Pass that creates a QuestaSim/ModelSim GUI project

    This pass generates:
    1. An MPF project file with all sources configured
    2. A shell script that launches the QuestaSim GUI

    Input types:
        - vhdl: VHDL source files
        - verilog: Verilog source files

    Output types:
        - questa-project: MPF project file for QuestaSim GUI
        - questa-gui-launcher: Shell script to launch QuestaSim GUI
    """
    name = "questa-simulate"
    input_types = {"vhdl", "verilog"}
    output_types = {"questa-project", "questa-gui-launcher"}

    def probe(self) -> str | None:
        return self.probe_tool("questa")

    def filter_vars(self) -> dict[str, Any]:
        """Contribute canonical filter variables for QuestaSim."""
        vhdl_std = self.config.get("vhdl_standard", "1993")

        return {
            "purpose": "simulation",
            "vhdl_frontend": "questa",
            "verilog_frontend": "questa",
            "simulation_engine": "questa",
            "vhdl_std": vhdl_std,
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create QuestaSim dispatcher

        Args:
            context: Build context to pass to dispatcher

        Returns:
            QuestaDispatcher instance
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")
        questa_tool = self.resolve_tool_identifier("questa")

        return [QuestaDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            questa_tool=questa_tool,
        )]
