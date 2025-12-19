"""Yosys Backend implementation"""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend
from ...base import BasePass
from .passes import YosysIce40Pass


class YosysBackend(BaseBackend):
    """Yosys Backend for FPGA synthesis

    Provides synthesis passes for various FPGA targets using Yosys.
    Currently supports:
    - Ice40 (via synth_ice40)

    Configuration options:
        - yosys_tool: Tool identifier for lookup (default: "yosys")
        - steps: List of intermediate transformation commands (optional)
    """

    def __init__(self):
        super().__init__("gbs.builtin.yosys")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list[Pass]:
        """Contribute Yosys passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)

        Returns:
            List of Pass instances that can help produce the outputs
        """
        passes = []

        # Contribute ice40 synthesis pass if ice40 netlist is needed
        if "ice40-netlist-json" in output_types:
            passes.append(YosysIce40Pass(config, project_config, gbs_config))

        return passes
