"""Vivado Backend implementation"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.protocol import BaseBackend
from ...planner.passes import Pass
from .passes import VivadoSynthesizePass


class VivadoBackend(BaseBackend):
    """Xilinx Vivado FPGA synthesis backend

    Provides Vivado synthesis pass and dispatcher for executing FPGA builds.

    Configuration options:
        - vhdl_standard: VHDL standard (e.g., "1993", "2008", "2019")
        - vivado_tool: Tool identifier for lookup (default: "vivado")
    """

    def __init__(self):
        super().__init__("gbs.builtin.vivado")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list[Pass]:
        """Contribute Vivado passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)

        Returns:
            List of Pass instances that can help produce the outputs
        """
        passes = []

        if set(output_types) & set(["vivado-bitstream"]):
            passes.append(VivadoSynthesizePass(config, project_config, gbs_config))

        return passes
