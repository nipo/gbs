"""QuestaSim/ModelSim Backend implementation"""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend
from ...base import BasePass
from .passes import QuestaSimulatePass


class QuestaBackend(BaseBackend):
    """QuestaSim/ModelSim Backend for VHDL/Verilog simulation

    Provides simulation pass that generates:
    - MPF project file for QuestaSim GUI
    - Shell script launcher for opening the GUI

    Configuration options:
        - vhdl_standard: VHDL standard (e.g., "1993", "2008", "2019")
        - questa_tool: Tool identifier for lookup (default: "questa")
    """

    def __init__(self):
        super().__init__("gbs.builtin.questa")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list[Pass]:
        """Contribute QuestaSim passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)

        Returns:
            List of Pass instances that can help produce the outputs
        """
        passes = []

        # Contribute simulation pass if GUI project outputs are needed
        if any(t in output_types for t in ["questa-gui-launcher", "questa-project", "simulator"]):
            passes.append(QuestaSimulatePass(config, project_config, gbs_config))

        return passes
